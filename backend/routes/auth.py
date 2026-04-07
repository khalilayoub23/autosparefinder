"""
Auth — all /api/v1/auth/* endpoints extracted from BACKEND_API_ROUTES.py.

Endpoints:
  POST   /api/v1/auth/register
  POST   /api/v1/auth/login
  POST   /api/v1/auth/verify-2fa
  POST   /api/v1/auth/refresh
  POST   /api/v1/auth/verify-email
  POST   /api/v1/auth/verify-phone
  POST   /api/v1/auth/send-2fa
  POST   /api/v1/auth/logout
  GET    /api/v1/auth/me
  POST   /api/v1/auth/accept-terms
  POST   /api/v1/auth/reset-password
  POST   /api/v1/auth/reset-password/confirm
  POST   /api/v1/auth/change-password
  GET    /api/v1/auth/trusted-devices
  POST   /api/v1/auth/trust-device
  DELETE /api/v1/auth/trusted-devices/{device_id}
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel, EmailStr, Field, validator
from datetime import datetime, timedelta
import os
import uuid as _uuid
import httpx as _httpx

from BACKEND_DATABASE_MODELS import get_pii_db, User, UserProfile, PasswordReset, UserSession
from BACKEND_AUTH_SECURITY import (
    get_current_user,
    register_user, login_user, complete_2fa_login,
    refresh_access_token, logout_user,
    create_password_reset_token, use_password_reset_token,
    change_password, create_2fa_code, verify_2fa_code,
    get_redis, check_rate_limit, generate_device_fingerprint,
    create_access_token, create_refresh_token,
)

router = APIRouter()

_VALID_CUSTOMER_TYPES = {"individual", "mechanic", "garage", "retailer", "fleet"}


class RegisterRequest(BaseModel):
    email: EmailStr
    phone: str = Field(..., max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field(..., max_length=100)
    customer_type: str = "individual"

    @validator("customer_type")
    def validate_customer_type(cls, v):
        if v not in _VALID_CUSTOMER_TYPES:
            raise ValueError(f"customer_type must be one of: {', '.join(sorted(_VALID_CUSTOMER_TYPES))}")
        return v

    @validator("phone")
    def validate_phone(cls, v):
        if v.startswith("+"):
            if len(v) < 8 or len(v) > 16 or not v[1:].isdigit():
                raise ValueError("Invalid international phone number")
        elif not v.startswith("05") or len(v) != 10 or not v.isdigit():
            raise ValueError("Invalid Israeli phone number (must start with 05, 10 digits)")
        return v

    @validator("password")
    def validate_password_strength(cls, v):
        min_len = int(os.getenv("PASSWORD_MIN_LENGTH", 8))
        if len(v) < min_len:
            raise ValueError(f"Password must be at least {min_len} characters long")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        if not any(c.isalpha() for c in v):
            raise ValueError("Password must contain at least one letter")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    trust_device: bool = False


class Login2FARequest(BaseModel):
    user_id: str
    code: str = Field(..., max_length=10)
    trust_device: bool = False


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., max_length=256)


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirmRequest(BaseModel):
    token: str = Field(..., max_length=256)
    new_password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=8, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


# ==============================================================================
# POST /api/v1/auth/register
# ==============================================================================

@router.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(data: RegisterRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Register new user and send 2FA SMS"""
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:register:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    user = await register_user(data.email, data.phone, data.password, data.full_name, db)
    profile_res = await db.execute(select(UserProfile).where(UserProfile.user_id == user.id))
    profile = profile_res.scalar_one_or_none()
    if profile:
        profile.customer_type = data.customer_type
    else:
        db.add(UserProfile(user_id=user.id, customer_type=data.customer_type))
    await db.commit()
    await create_2fa_code(str(user.id), user.phone, db)
    return {
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "customer_type": data.customer_type},
        "message": f"קוד אימות נשלח ל-{user.phone[-4:]}",
    }


# ==============================================================================
# POST /api/v1/auth/login
# ==============================================================================

@router.post("/api/v1/auth/login")
async def login(data: LoginRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Login – returns tokens or triggers 2FA"""
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:login:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    ua = request.headers.get("user-agent", "")
    try:
        user, access_token, refresh_token = await login_user(
            data.email, data.password, device_fp, ip, ua, data.trust_device, db, redis
        )
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "is_verified": user.is_verified, "is_admin": user.is_admin},
        }
    except HTTPException as e:
        if e.status_code == status.HTTP_202_ACCEPTED:
            return JSONResponse(status_code=202, content={
                "requires_2fa": True,
                "user_id": e.headers.get("X-User-ID"),
                "message": e.detail,
            })
        raise


# ==============================================================================
# POST /api/v1/auth/verify-2fa
# ==============================================================================

@router.post("/api/v1/auth/verify-2fa")
async def verify_2fa(data: Login2FARequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Complete login with 2FA code"""
    device_fp = generate_device_fingerprint(request)
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:verify_2fa:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    ua = request.headers.get("user-agent", "")
    user, access_token, refresh_token = await complete_2fa_login(
        data.user_id, data.code, device_fp, ip, ua, data.trust_device, db
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "user": {"id": str(user.id), "email": user.email, "full_name": user.full_name, "is_verified": user.is_verified, "is_admin": user.is_admin},
    }


# ==============================================================================
# POST /api/v1/auth/refresh
# ==============================================================================

@router.post("/api/v1/auth/refresh")
async def refresh_token(data: RefreshTokenRequest, db: AsyncSession = Depends(get_pii_db)):
    """Refresh access token"""
    new_access, new_refresh = await refresh_access_token(data.refresh_token, db)
    return {"access_token": new_access, "refresh_token": new_refresh, "token_type": "bearer"}


# ==============================================================================
# POST /api/v1/auth/verify-email
# ==============================================================================

@router.post("/api/v1/auth/verify-email")
async def verify_email(token: str, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    """Validate an email verification token (re-uses the PasswordReset table as
    a lightweight token store — a dedicated EmailVerification table can replace
    this when full email verification flow is implemented)."""
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:verify_email:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    if not token:
        raise HTTPException(status_code=400, detail="Token required")
    result = await db.execute(
        select(PasswordReset).where(
            and_(
                PasswordReset.token == token,
                PasswordReset.used_at.is_(None),
                PasswordReset.expires_at > datetime.utcnow(),
            )
        )
    )
    reset = result.scalar_one_or_none()
    if not reset:
        raise HTTPException(status_code=400, detail="Invalid or expired verification token")
    user_result = await db.execute(select(User).where(User.id == reset.user_id))
    user = user_result.scalar_one_or_none()
    if user:
        user.is_verified = True
    reset.used_at = datetime.utcnow()
    await db.commit()
    return {"message": "Email verified"}


# ==============================================================================
# POST /api/v1/auth/verify-phone
# ==============================================================================

@router.post("/api/v1/auth/verify-phone")
async def verify_phone(code: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    success = await verify_2fa_code(str(current_user.id), code, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid code")
    current_user.is_verified = True
    await db.commit()
    return {"message": "Phone verified"}


# ==============================================================================
# POST /api/v1/auth/send-2fa
# ==============================================================================

@router.post("/api/v1/auth/send-2fa")
async def send_2fa(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    await create_2fa_code(str(current_user.id), current_user.phone, db)
    return {"message": f"קוד נשלח ל-{current_user.phone[-4:]}"}


# ==============================================================================
# POST /api/v1/auth/logout
# ==============================================================================

@router.post("/api/v1/auth/logout")
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_pii_db),
):
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    if token:
        await logout_user(token, db)
    return {"message": "Logged out successfully"}


# ==============================================================================
# POST /api/v1/auth/social-login
# ==============================================================================

class SocialLoginRequest(BaseModel):
    provider: str            # 'google' | 'facebook'
    token: str               # ID token (Google) or access token (Facebook)


@router.post("/api/v1/auth/social-login")
async def social_login(
    data: SocialLoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_pii_db),
    redis=Depends(get_redis),
):
    """Verify a Google/Facebook OAuth token and return JWT access+refresh tokens.

    Google:   data.token is the credential (ID token) from Google Identity Services.
    Facebook: data.token is the user access token from the Facebook JS SDK.
    """
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:social_login:{ip}', 10, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')

    provider = data.provider.lower()
    if provider not in ("google", "facebook"):
        raise HTTPException(status_code=400, detail="ספק לא נתמך")

    # ── Verify token with provider and extract profile ───────────────────────
    oauth_id: str = ""
    email: str = ""
    full_name: str = ""
    try:
        async with _httpx.AsyncClient(timeout=10.0) as client:
            if provider == "google":
                resp = await client.get(
                    "https://oauth2.googleapis.com/tokeninfo",
                    params={"id_token": data.token},
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=401, detail="Google token לא תקין")
                info = resp.json()
                # Optionally verify audience (aud) matches our client ID
                client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
                if client_id and info.get("aud") != client_id:
                    raise HTTPException(status_code=401, detail="Google token לא תקין")
                oauth_id = info.get("sub", "")
                email = info.get("email", "")
                full_name = info.get("name", email.split("@")[0])
            else:  # facebook
                resp = await client.get(
                    "https://graph.facebook.com/me",
                    params={"fields": "id,name,email", "access_token": data.token},
                )
                if resp.status_code != 200:
                    raise HTTPException(status_code=401, detail="Facebook token לא תקין")
                info = resp.json()
                oauth_id = info.get("id", "")
                email = info.get("email", "")
                full_name = info.get("name", "")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=502, detail="שגיאה בכניסה עם הספק")

    if not oauth_id or not email:
        raise HTTPException(status_code=401, detail="לא ניתן לאמת — אנא נסה שוב")

    # ── Find or create user ──────────────────────────────────────────────────
    user: Optional[User] = (await db.execute(
        select(User).where(and_(User.oauth_provider == provider, User.oauth_id == oauth_id))
    )).scalar_one_or_none()

    if user is None:
        # Check if e-mail already registered (link accounts)
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user:
            user.oauth_provider = provider
            user.oauth_id = oauth_id
            user.is_verified = True
        else:
            user = User(
                email=email,
                full_name=full_name,
                password_hash=None,
                phone=None,
                oauth_provider=provider,
                oauth_id=oauth_id,
                is_verified=True,
                is_active=True,
            )
            db.add(user)
            await db.flush()
            db.add(UserProfile(user_id=user.id))
        await db.commit()
        await db.refresh(user)

    if not user.is_active:
        raise HTTPException(status_code=403, detail="החשבון הושעה")

    # ── Issue JWT tokens (reuse session machinery) ───────────────────────────
    session_id = str(_uuid.uuid4())
    access_token  = create_access_token(str(user.id), session_id)
    refresh_token = create_refresh_token(str(user.id), session_id)

    ua = request.headers.get("user-agent", "")[:255]
    session = UserSession(
        id=_uuid.UUID(session_id),
        user_id=user.id,
        token=access_token,
        refresh_token=refresh_token,
        device_fingerprint=generate_device_fingerprint(request),
        ip_address=ip,
        user_agent=ua,
        expires_at=datetime.utcnow() + timedelta(days=1),
    )
    db.add(session)
    await db.commit()

    return {
        "access_token":  access_token,
        "refresh_token": refresh_token,
        "token_type":    "bearer",
        "user": {
            "id":          str(user.id),
            "email":       user.email,
            "full_name":   user.full_name,
            "is_verified": user.is_verified,
            "is_admin":    user.is_admin,
        },
    }


# ==============================================================================
# GET /api/v1/auth/me
# ==============================================================================

@router.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    profile = await db.get(UserProfile, current_user.id)
    terms_accepted_at = None
    if profile is None:
        result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
        profile = result.scalar_one_or_none()
    if profile:
        terms_accepted_at = profile.terms_accepted_at
    return {
        "id": str(current_user.id),
        "email": current_user.email,
        "phone": current_user.phone,
        "full_name": current_user.full_name,
        "is_verified": current_user.is_verified,
        "is_admin": current_user.is_admin,
        "created_at": current_user.created_at,
        "terms_accepted_at": terms_accepted_at,
    }


# ==============================================================================
# POST /api/v1/auth/accept-terms
# ==============================================================================

@router.post("/api/v1/auth/accept-terms")
async def accept_terms(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    """Record that the logged-in user has accepted the privacy policy and terms of service."""
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == current_user.id))
    profile = result.scalar_one_or_none()
    now = datetime.utcnow()
    if profile is None:
        profile = UserProfile(user_id=current_user.id, terms_accepted_at=now)
        db.add(profile)
    else:
        profile.terms_accepted_at = now
    await db.commit()
    return {"terms_accepted_at": now}


# ==============================================================================
# POST /api/v1/auth/reset-password
# ==============================================================================

@router.post("/api/v1/auth/reset-password")
async def reset_password(data: PasswordResetRequest, request: Request, db: AsyncSession = Depends(get_pii_db), redis=Depends(get_redis)):
    ip = request.client.host if request.client else "unknown"
    if redis:
        allowed = await check_rate_limit(redis, f'rate:reset_password:{ip}', 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail='יותר מדי בקשות — נסה שוב בעוד דקה')
    await create_password_reset_token(data.email, db)
    return {"message": "אם המייל קיים במערכת, נשלח קישור לאיפוס סיסמה"}


# ==============================================================================
# POST /api/v1/auth/reset-password/confirm
# ==============================================================================

@router.post("/api/v1/auth/reset-password/confirm")
async def reset_password_confirm(data: PasswordResetConfirmRequest, db: AsyncSession = Depends(get_pii_db)):
    success = await use_password_reset_token(data.token, data.new_password, db)
    if not success:
        raise HTTPException(status_code=400, detail="Invalid or expired token")
    return {"message": "הסיסמה שונתה בהצלחה"}


# ==============================================================================
# POST /api/v1/auth/change-password
# ==============================================================================

@router.post("/api/v1/auth/change-password")
async def change_password_ep(data: ChangePasswordRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    await change_password(current_user, data.current_password, data.new_password, db)
    return {"message": "הסיסמה שונתה בהצלחה"}


# ==============================================================================
# GET /api/v1/auth/trusted-devices
# ==============================================================================

@router.get("/api/v1/auth/trusted-devices")
async def get_trusted_devices(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(
        select(UserSession).where(and_(
            UserSession.user_id == current_user.id,
            UserSession.is_trusted_device == True,
            UserSession.trusted_until > datetime.utcnow(),
            UserSession.revoked_at.is_(None),
        ))
    )
    sessions = result.scalars().all()
    return {"devices": [{"id": str(s.id), "device_name": s.device_name or "Unknown", "last_used": s.last_used_at, "trusted_until": s.trusted_until} for s in sessions]}


# ==============================================================================
# POST /api/v1/auth/trust-device
# ==============================================================================

@router.post("/api/v1/auth/trust-device")
async def trust_device(device_fingerprint: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(
        select(UserSession).where(and_(
            UserSession.user_id == current_user.id,
            UserSession.device_fingerprint == device_fingerprint,
            UserSession.revoked_at.is_(None),
        )).order_by(UserSession.created_at.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    session.is_trusted_device = True
    session.trusted_until = datetime.utcnow() + timedelta(days=180)
    await db.commit()
    return {"message": "Device trusted for 6 months"}


# ==============================================================================
# DELETE /api/v1/auth/trusted-devices/{device_id}
# ==============================================================================

@router.delete("/api/v1/auth/trusted-devices/{device_id}")
async def delete_trusted_device(device_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_pii_db)):
    result = await db.execute(select(UserSession).where(and_(UserSession.id == device_id, UserSession.user_id == current_user.id)))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Device not found")
    session.is_trusted_device = False
    session.trusted_until = None
    await db.commit()
    return {"message": "Device trust removed"}
