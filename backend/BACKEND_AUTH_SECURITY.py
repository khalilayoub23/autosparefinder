"""
==============================================================================
AUTO SPARE - AUTHENTICATION & SECURITY
==============================================================================
Features:
  - JWT access tokens (15 min) + refresh tokens (7 days)
  - 2FA via Twilio SMS (6-digit code, 10 min expiry)
  - bcrypt password hashing (rounds=12)
  - Rate limiting with Redis
  - Device trust (6 months)
  - Brute force protection (5 attempts → 15 min lockout)
  - Password reset via email token (1 hour)
==============================================================================
"""

import hashlib
import os
import random
import secrets
import string
from datetime import datetime, timedelta
from typing import Optional, Tuple

import redis.asyncio as aioredis
from dotenv import load_dotenv
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from BACKEND_DATABASE_MODELS import (
    LoginAttempt, PasswordReset, TwoFactorCode, User, UserProfile, UserSession,
    get_db,
)

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", secrets.token_hex(32))
JWT_REFRESH_SECRET_KEY = os.getenv("JWT_REFRESH_SECRET_KEY", secrets.token_hex(32))
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))
TWO_FA_EXPIRY_MINUTES = int(os.getenv("2FA_CODE_EXPIRY_MINUTES", "10"))
TRUST_DEVICE_DAYS = int(os.getenv("TRUST_DEVICE_DAYS", "180"))

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# ==============================================================================
# PASSWORD HASHING
# ==============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


# ==============================================================================
# JWT TOKENS
# ==============================================================================

def create_access_token(user_id: str, session_id: str) -> str:
    payload = {
        "sub": user_id,
        "session_id": session_id,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, session_id: str) -> str:
    payload = {
        "sub": user_id,
        "session_id": session_id,
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_REFRESH_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


def decode_refresh_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_REFRESH_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


# ==============================================================================
# REDIS CLIENT
# ==============================================================================

_redis_client: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis_client
    if _redis_client is None:
        try:
            _redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
            await _redis_client.ping()
        except Exception:
            # Fall back to a mock if Redis is unavailable (dev/test)
            _redis_client = None
    return _redis_client


async def close_redis():
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None


# ==============================================================================
# DEVICE FINGERPRINT
# ==============================================================================

def generate_device_fingerprint(request: Request) -> str:
    """Generate a stable fingerprint from request headers."""
    components = [
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        request.headers.get("accept-encoding", ""),
        request.client.host if request.client else "",
    ]
    raw = "|".join(components)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


# ==============================================================================
# RATE LIMITING
# ==============================================================================

async def check_rate_limit(redis: aioredis.Redis, key: str, limit: int, window_seconds: int) -> bool:
    """Returns True if allowed, False if rate limited."""
    if redis is None:
        return True  # skip if Redis unavailable
    try:
        current = await redis.incr(key)
        if current == 1:
            await redis.expire(key, window_seconds)
        return current <= limit
    except Exception:
        return True


# ==============================================================================
# BRUTE FORCE PROTECTION
# ==============================================================================

async def record_failed_login(
    email: str,
    ip_address: str,
    db: AsyncSession,
    failure_reason: str = "invalid_credentials",
):
    """Record a failed login attempt and lock account if threshold exceeded."""
    # Find user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    # Record attempt
    attempt = LoginAttempt(
        user_id=user.id if user else None,
        email=email,
        ip_address=ip_address,
        success=False,
        failure_reason=failure_reason,
    )
    db.add(attempt)

    if user:
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= MAX_LOGIN_ATTEMPTS:
            user.locked_until = datetime.utcnow() + timedelta(minutes=LOGIN_LOCKOUT_MINUTES)
            user.failed_login_count = 0

    await db.commit()


async def record_successful_login(user: User, ip_address: str, db: AsyncSession):
    user.failed_login_count = 0
    user.locked_until = None
    attempt = LoginAttempt(
        user_id=user.id,
        email=user.email,
        ip_address=ip_address,
        success=True,
    )
    db.add(attempt)
    await db.commit()


# ==============================================================================
# 2FA
# ==============================================================================

def generate_2fa_code() -> str:
    # In development, use a fixed code if DEV_2FA_CODE is set
    dev_code = os.getenv("DEV_2FA_CODE")
    if dev_code:
        return dev_code
    return "".join(random.choices(string.digits, k=6))


async def send_sms_2fa(phone: str, code: str) -> bool:
    """Send 2FA code via Twilio SMS. Returns True if sent."""
    if not TWILIO_ACCOUNT_SID:
        # Dev mode: print code to console
        print(f"[DEV] 2FA code for {phone}: {code}")
        return True
    try:
        from twilio.rest import Client
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(
            body=f"קוד האימות שלך ל-Auto Spare: {code}\nתוקף: 10 דקות",
            from_=TWILIO_PHONE_NUMBER,
            to=phone,
        )
        return True
    except Exception as e:
        print(f"[ERROR] SMS send failed: {e}")
        return False


async def create_2fa_code(user_id: str, phone: str, db: AsyncSession) -> Optional[str]:
    """Create and store a 2FA code, send via SMS."""
    code = generate_2fa_code()
    expires = datetime.utcnow() + timedelta(minutes=TWO_FA_EXPIRY_MINUTES)

    two_fa = TwoFactorCode(
        user_id=user_id,
        code=code,
        phone=phone,
        expires_at=expires,
    )
    db.add(two_fa)
    await db.commit()

    await send_sms_2fa(phone, code)
    return code


async def verify_2fa_code(user_id: str, code: str, db: AsyncSession) -> bool:
    """Verify a 2FA code. Returns True if valid."""
    result = await db.execute(
        select(TwoFactorCode).where(
            and_(
                TwoFactorCode.user_id == user_id,
                TwoFactorCode.verified_at.is_(None),
                TwoFactorCode.expires_at > datetime.utcnow(),
            )
        ).order_by(TwoFactorCode.created_at.desc()).limit(1)
    )
    two_fa = result.scalar_one_or_none()

    if not two_fa:
        return False

    two_fa.attempts = (two_fa.attempts or 0) + 1

    if two_fa.attempts > 3:
        await db.commit()
        raise HTTPException(status_code=400, detail="Too many attempts. Request a new code.")

    if two_fa.code != code:
        await db.commit()
        return False

    two_fa.verified_at = datetime.utcnow()
    await db.commit()
    return True


# ==============================================================================
# SESSION MANAGEMENT
# ==============================================================================

async def create_session(
    user: User,
    access_token: str,
    refresh_token: str,
    device_fingerprint: str,
    ip_address: str,
    user_agent: str,
    trust_device: bool,
    db: AsyncSession,
) -> UserSession:
    """Persist a new session to the database."""
    trusted_until = (
        datetime.utcnow() + timedelta(days=TRUST_DEVICE_DAYS) if trust_device else None
    )

    session = UserSession(
        user_id=user.id,
        token=access_token,
        refresh_token=refresh_token,
        device_fingerprint=device_fingerprint,
        ip_address=ip_address,
        user_agent=user_agent,
        is_trusted_device=trust_device,
        trusted_until=trusted_until,
        expires_at=datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        last_used_at=datetime.utcnow(),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


async def revoke_session(token: str, db: AsyncSession):
    result = await db.execute(select(UserSession).where(UserSession.token == token))
    session = result.scalar_one_or_none()
    if session:
        session.revoked_at = datetime.utcnow()
        await db.commit()


# ==============================================================================
# CORE AUTH FLOWS
# ==============================================================================

async def register_user(
    email: str,
    phone: str,
    password: str,
    full_name: str,
    db: AsyncSession,
) -> User:
    """Register a new user. Raises HTTPException on conflict."""
    # Check email
    result = await db.execute(select(User).where(User.email == email))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    # Check phone
    result = await db.execute(select(User).where(User.phone == phone))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Phone number already registered")

    user = User(
        email=email,
        phone=phone,
        password_hash=hash_password(password),
        full_name=full_name,
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    await db.flush()  # get the ID

    # Create empty profile
    profile = UserProfile(user_id=user.id)
    db.add(profile)

    await db.commit()
    await db.refresh(user)
    return user


async def login_user(
    email: str,
    password: str,
    device_fingerprint: str,
    ip_address: str,
    user_agent: str,
    trust_device: bool,
    db: AsyncSession,
    redis: Optional[aioredis.Redis] = None,
) -> Tuple[User, str, str]:
    """
    Authenticate user. Returns (user, access_token, refresh_token).
    Raises 202 HTTPException if 2FA is required.
    """
    # Rate limit: 5 login attempts per minute per IP
    if redis:
        allowed = await check_rate_limit(redis, f"login:{ip_address}", 5, 60)
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many login attempts. Try again later.")

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password_hash):
        await record_failed_login(email, ip_address, db)
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is deactivated")

    if user.locked_until and user.locked_until > datetime.utcnow():
        minutes_left = int((user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
        raise HTTPException(
            status_code=423,
            detail=f"Account locked. Try again in {minutes_left} minutes",
        )

    # Check for trusted device
    is_trusted = False
    if device_fingerprint:
        result = await db.execute(
            select(UserSession).where(
                and_(
                    UserSession.user_id == user.id,
                    UserSession.device_fingerprint == device_fingerprint,
                    UserSession.is_trusted_device == True,
                    UserSession.trusted_until > datetime.utcnow(),
                    UserSession.revoked_at.is_(None),
                )
            )
        )
        is_trusted = result.scalars().first() is not None

    # If 2FA required (not trusted device and verified user)
    if not is_trusted:
        # Dev mode: skip 2FA when DEV_2FA_CODE is set
        if os.getenv("DEV_2FA_CODE") and os.getenv("ENVIRONMENT", "development") == "development":
            print(f"[DEV] Skipping 2FA for {email} (DEV_2FA_CODE is set)")
        else:
            # Send 2FA and raise 202
            await create_2fa_code(str(user.id), user.phone, db)
            exc = HTTPException(
                status_code=status.HTTP_202_ACCEPTED,
                detail=f"2FA code sent to {user.phone[-4:]}",
            )
            exc.headers = {"X-User-ID": str(user.id)}
            raise exc

    # Trusted device - issue tokens directly
    await record_successful_login(user, ip_address, db)

    session_id = secrets.token_hex(16)
    access_token = create_access_token(str(user.id), session_id)
    refresh_token = create_refresh_token(str(user.id), session_id)

    await create_session(user, access_token, refresh_token, device_fingerprint, ip_address, user_agent, trust_device, db)

    return user, access_token, refresh_token


async def complete_2fa_login(
    user_id: str,
    code: str,
    device_fingerprint: str,
    ip_address: str,
    user_agent: str,
    trust_device: bool,
    db: AsyncSession,
) -> Tuple[User, str, str]:
    """Complete login after 2FA verification."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    verified = await verify_2fa_code(user_id, code, db)
    if not verified:
        raise HTTPException(status_code=400, detail="Invalid 2FA code")

    await record_successful_login(user, ip_address, db)

    session_id = secrets.token_hex(16)
    access_token = create_access_token(str(user.id), session_id)
    refresh_token = create_refresh_token(str(user.id), session_id)

    await create_session(user, access_token, refresh_token, device_fingerprint, ip_address, user_agent, trust_device, db)

    return user, access_token, refresh_token


async def refresh_access_token(refresh_token_str: str, db: AsyncSession) -> Tuple[str, str]:
    """Use a refresh token to issue a new access + refresh token pair."""
    payload = decode_refresh_token(refresh_token_str)
    user_id = payload.get("sub")

    # Verify session exists and is not revoked
    result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.refresh_token == refresh_token_str,
                UserSession.revoked_at.is_(None),
            )
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Session expired or revoked")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Revoke old session
    session.revoked_at = datetime.utcnow()

    # Create new tokens
    session_id = secrets.token_hex(16)
    new_access = create_access_token(str(user.id), session_id)
    new_refresh = create_refresh_token(str(user.id), session_id)

    # New session
    new_session = UserSession(
        user_id=user.id,
        token=new_access,
        refresh_token=new_refresh,
        device_fingerprint=session.device_fingerprint,
        ip_address=session.ip_address,
        user_agent=session.user_agent,
        is_trusted_device=session.is_trusted_device,
        trusted_until=session.trusted_until,
        expires_at=datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(new_session)
    await db.commit()

    return new_access, new_refresh


async def logout_user(token: str, db: AsyncSession):
    await revoke_session(token, db)


async def create_password_reset_token(email: str, db: AsyncSession) -> Optional[str]:
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        return None  # Don't reveal existence

    token = secrets.token_urlsafe(32)
    reset = PasswordReset(
        user_id=user.id,
        token=token,
        expires_at=datetime.utcnow() + timedelta(hours=1),
    )
    db.add(reset)
    await db.commit()

    # TODO: send email with reset link
    print(f"[DEV] Password reset token for {email}: {token}")
    return token


async def use_password_reset_token(token: str, new_password: str, db: AsyncSession) -> bool:
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
        return False

    result = await db.execute(select(User).where(User.id == reset.user_id))
    user = result.scalar_one_or_none()
    if not user:
        return False

    user.password_hash = hash_password(new_password)
    reset.used_at = datetime.utcnow()
    await db.commit()
    return True


async def change_password(user: User, current_password: str, new_password: str, db: AsyncSession) -> bool:
    if not verify_password(current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(new_password)
    await db.commit()
    return True


async def update_phone_number(user: User, new_phone: str, verification_code: str, db: AsyncSession) -> bool:
    verified = await verify_2fa_code(str(user.id), verification_code, db)
    if not verified:
        raise HTTPException(status_code=400, detail="Invalid verification code")

    # Check phone not taken
    result = await db.execute(select(User).where(User.phone == new_phone))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Phone already in use")

    user.phone = new_phone
    await db.commit()
    return True


# ==============================================================================
# FASTAPI DEPENDENCIES
# ==============================================================================

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Extract and validate JWT from Authorization header."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account deactivated")

    return user


async def get_current_active_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Inactive user")
    return user


async def get_current_verified_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Phone verification required")
    return user


async def get_current_admin_user(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return user
