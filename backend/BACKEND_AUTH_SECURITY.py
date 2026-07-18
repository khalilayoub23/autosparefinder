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
import json
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
    get_db, get_pii_db,
)

load_dotenv()

# ==============================================================================
# CONFIGURATION
# ==============================================================================

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_REFRESH_SECRET_KEY = os.getenv("JWT_REFRESH_SECRET_KEY", "")

# Fail fast in production — ephemeral secrets invalidate all sessions every restart.
_env = os.getenv("ENVIRONMENT", "development")
if _env == "production":
    if not JWT_SECRET_KEY:
        raise RuntimeError("JWT_SECRET_KEY environment variable must be set in production")
    if not JWT_REFRESH_SECRET_KEY:
        raise RuntimeError("JWT_REFRESH_SECRET_KEY environment variable must be set in production")
else:
    # Development fallback: generate a stable-per-process random key with a warning
    if not JWT_SECRET_KEY:
        JWT_SECRET_KEY = secrets.token_hex(32)
        print("[WARN] JWT_SECRET_KEY not set — using random key. All tokens will be lost on restart.")
    if not JWT_REFRESH_SECRET_KEY:
        JWT_REFRESH_SECRET_KEY = secrets.token_hex(32)
        print("[WARN] JWT_REFRESH_SECRET_KEY not set — using random key. All tokens will be lost on restart.")
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
TWILIO_MESSAGING_SERVICE_SID = os.getenv("TWILIO_MESSAGING_SERVICE_SID")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
USE_TWILIO_MESSAGING_SERVICE = os.getenv("USE_TWILIO_MESSAGING_SERVICE", "false").lower() in ("1", "true", "yes", "on")

# Quiet the Twilio SDK's HTTP logger: at INFO it dumps the full request/response (incl.
# the destination phone number) to docker logs on every SMS. Raise to WARNING so routine
# sends log nothing; genuine errors still surface. (The 2FA code was never in the body
# log, but the phone number was — this closes that.)
import logging as _logging
_logging.getLogger("twilio.http_client").setLevel(_logging.WARNING)
_logging.getLogger("twilio").setLevel(_logging.WARNING)
ENABLE_WHATSAPP_2FA = os.getenv("ENABLE_WHATSAPP_2FA", "false").lower() in ("1", "true", "yes", "on")
# When true: deliver the 2FA code via the WhatsApp (Baileys) bridge FIRST — free, no
# Twilio — and only fall back to Twilio SMS if the WhatsApp send fails (bridge down, or
# the number has no WhatsApp). This keeps users from ever being locked out while making
# WhatsApp the default channel. Set WHATSAPP_2FA_PRIMARY=1 to enable.
WHATSAPP_2FA_PRIMARY = os.getenv("WHATSAPP_2FA_PRIMARY", "false").lower() in ("1", "true", "yes", "on")

# Customer-service chat link included in the 2FA SMS. WhatsApp by default — it's
# login-free, so a customer who is stuck AT the login/2FA step can still reach a human
# (a web chat behind auth would be useless in exactly that moment). Override with
# TWO_FA_SUPPORT_URL (e.g. a Telegram https://t.me/... or web-chat URL) if preferred.
TWO_FA_SUPPORT_URL = (
    os.getenv("TWO_FA_SUPPORT_URL")
    or os.getenv("NOA_WHATSAPP_URL")
    or "https://wa.me/972532426920"
).strip().rstrip("/")

# Branded/private 2FA text template — warm + welcoming, with the CS-chat link.
# Placeholders: {name}, {code}, {minutes}, {support}
# Kept to ~2 UCS-2 (Hebrew) SMS segments (≤134 UTF-16 units incl. a normal-length
# name) — warm greeting + CS-chat link + security note, no tagline/extra emoji.
TWO_FA_MESSAGE_TEMPLATE = os.getenv(
    "TWO_FA_MESSAGE_TEMPLATE",
    "היי {name} 👋 ברוכים הבאים ל-AutoSpare!\n"
    "קוד האימות: {code} (בתוקף {minutes} דק').\n"
    "עזרה: {support}\n"
    "קוד אישי, לא לשתף.",
)
# Icon now lives inside the warm text, so no orphan brand-icon line by default.
TWO_FA_BRAND_ICON = os.getenv("TWO_FA_BRAND_ICON", "").strip()
TWO_FA_LOGO_URL = os.getenv("TWO_FA_LOGO_URL", "").strip()

# ==============================================================================
# PASSWORD HASHING
# ==============================================================================

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)


def hash_password(password: str) -> str:
    import bcrypt as _bcrypt
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    import bcrypt as _bcrypt
    try:
        return _bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except Exception:
        return False


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


async def publish_notification(user_id: str, payload: dict) -> None:
    """Publish a notification payload to the user's Redis Pub/Sub channel."""
    r = await get_redis()
    if r:
        await r.publish(f"user:{user_id}:notifications", json.dumps(payload))


# ==============================================================================
# DEVICE FINGERPRINT
# ==============================================================================

def generate_device_fingerprint(request: Request) -> str:
    """Generate a stable per-browser fingerprint from request headers.

    Avoid using client IP here because it can change frequently behind reverse
    proxies/tunnels and causes trusted-device checks to fail unexpectedly.
    """
    components = [
        request.headers.get("user-agent", ""),
        request.headers.get("accept-language", ""),
        request.headers.get("accept-encoding", ""),
        request.headers.get("sec-ch-ua", ""),
        request.headers.get("sec-ch-ua-platform", ""),
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

# Secret used to HMAC the 2FA code before storing it. Keyed off JWT_SECRET_KEY (a
# server-side secret that never lives in the DB), so a DB-only breach cannot brute-force
# the 6-digit code (10^6 space would be trivial to reverse from a plain hash without a
# secret key). Overridable via TWO_FA_HASH_SECRET.
_TWO_FA_HASH_SECRET = (os.getenv("TWO_FA_HASH_SECRET") or JWT_SECRET_KEY or "").encode()


def hash_2fa_code(code: str) -> str:
    """HMAC-SHA256 of a 2FA code (hex). Codes are stored/compared as this hash, never raw."""
    import hmac as _hmac
    import hashlib as _hashlib
    return _hmac.new(_TWO_FA_HASH_SECRET, (code or "").encode(), _hashlib.sha256).hexdigest()


def _2fa_code_matches(raw_input: str, stored: str) -> bool:
    """Constant-time compare. Handles legacy plaintext rows (len 6) during the transition
    window so a code issued just before this deploy still verifies until it expires."""
    import hmac as _hmac
    if stored and len(stored) == 6:  # legacy plaintext (pre-hashing); expires within 10 min
        return _hmac.compare_digest(stored, raw_input or "")
    return _hmac.compare_digest(stored or "", hash_2fa_code(raw_input))


def generate_2fa_code() -> str:
    # DEV_2FA_CODE is ONLY permitted in non-production environments.
    # Using it in production would be a security backdoor.
    dev_code = os.getenv("DEV_2FA_CODE")
    if dev_code:
        env = os.getenv("ENVIRONMENT", "development")
        if env == "production":
            # Silently ignore the backdoor in production; generate a real code
            print("[SECURITY] DEV_2FA_CODE is set but ignored in production environment.")
        else:
            return dev_code
    return "".join(random.choices(string.digits, k=6))


def _normalize_e164(phone: str, default_cc: str = "972") -> str:
    """Normalize phone to E.164. Israeli 05XXXXXXXX → +97205XXXXXXXX."""
    phone = phone.strip()
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return f"+{default_cc}{phone[1:]}"
    return f"+{phone}"


# Bidi control marks for RTL SMS. Hebrew is RTL but the code/URL/"AutoSpare" are LTR;
# without direction hints the phone's bidi algorithm reorders those runs and the message
# looks scrambled. RLM at each line start pins the line's base direction to RTL; LRM
# around the URL keeps that LTR run intact so it doesn't jumble the Hebrew around it.
_RLM = "‏"  # RIGHT-TO-LEFT MARK
_LRM = "‎"  # LEFT-TO-RIGHT MARK


def _rtl_lines(text: str) -> str:
    """Prefix every non-empty line with RLM so mixed Hebrew/Latin lines render RTL."""
    return "\n".join((_RLM + ln) if ln.strip() else ln for ln in text.split("\n"))


def _build_2fa_message(code: str, full_name: Optional[str] = None) -> str:
    """Render the configured 2FA message text safely (RTL-clean for Hebrew phones)."""
    safe_name = (full_name or "").strip()[:60]
    support_ltr = f"{_LRM}{TWO_FA_SUPPORT_URL}{_LRM}"
    try:
        base = TWO_FA_MESSAGE_TEMPLATE.format(
            code=code,
            minutes=TWO_FA_EXPIRY_MINUTES,
            name=safe_name or "לקוח יקר",
            support=support_ltr,
            # Back-compat: honor any custom template still using {site}.
            site=support_ltr,
        ).strip()
    except Exception:
        # Fallback to a safe default if template placeholders are invalid
        greeting = f"היי {safe_name} 👋 " if safe_name else "היי 👋 "
        base = (
            f"{greeting}ברוכים הבאים ל-AutoSpare! קוד האימות שלך הוא {code} "
            f"(בתוקף {TWO_FA_EXPIRY_MINUTES} דקות). צריכים עזרה? דברו איתנו: {support_ltr}. "
            "הקוד אישי לך בלבד, נא לא לשתף."
        )

    lines = []
    if TWO_FA_BRAND_ICON:
        lines.append(TWO_FA_BRAND_ICON)
    lines.append(base)
    if TWO_FA_LOGO_URL:
        # SMS cannot embed an inline image reliably, so include a direct logo link.
        lines.append(f"לוגו: {_LRM}{TWO_FA_LOGO_URL}{_LRM}")
    return _rtl_lines("\n".join(lines))


async def send_sms_2fa(phone: str, code: str, full_name: Optional[str] = None) -> bool:
    """Send 2FA code via Twilio SMS. Returns True if sent."""
    phone = _normalize_e164(phone)
    # Real SMS mode requires both account SID and auth token.
    if not TWILIO_ACCOUNT_SID or not TWILIO_AUTH_TOKEN:
        # Dev mode: print code to console
        if os.getenv("ENVIRONMENT", "production") == "development":
            print(f"[DEV] 2FA code for {phone}: {code}")
        return True
    try:
        import asyncio as _asyncio
        from twilio.rest import Client

        def _send_sync():
            client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            base_params = {
                "body": _build_2fa_message(code, full_name),
                "to": phone,
            }
            # Preferred path: explicit sender number for deterministic delivery
            # (some Messaging Service setups fail with 21704 on trial/misconfigured accounts).
            if TWILIO_PHONE_NUMBER:
                client.messages.create(
                    **base_params,
                    from_=TWILIO_PHONE_NUMBER,
                )
                return

            # Optional path: Messaging Service SID (disabled by default).
            if USE_TWILIO_MESSAGING_SERVICE and TWILIO_MESSAGING_SERVICE_SID:
                client.messages.create(
                    **base_params,
                    messaging_service_sid=TWILIO_MESSAGING_SERVICE_SID,
                )
                return

            raise RuntimeError("No Twilio sender configured: set TWILIO_PHONE_NUMBER (or enable USE_TWILIO_MESSAGING_SERVICE)")

        await _asyncio.to_thread(_send_sync)
        return True
    except Exception as e:
        print(f"[ERROR] SMS send failed: {e}")
        return False


async def send_whatsapp_2fa(phone: str, code: str, full_name: Optional[str] = None) -> dict:
    """Send 2FA code via the WhatsApp (Baileys) bridge.

    Returns {"ok": bool, "key": <sent-message key or None>}. The key is stored so the
    code message can be deleted-for-everyone after the code is verified.
    """
    if not (ENABLE_WHATSAPP_2FA or WHATSAPP_2FA_PRIMARY):
        return {"ok": False, "key": None}
    try:
        from social.whatsapp_provider import get_whatsapp_provider
        provider = get_whatsapp_provider()
        result = await provider.send_message(
            phone,
            _build_2fa_message(code, full_name)
        )
        if not result.get("ok"):
            print(f"[WARN] WhatsApp 2FA failed: {result.get('error')}")
        return {"ok": bool(result.get("ok")), "key": result.get("key")}
    except Exception as e:
        print(f"[WARN] WhatsApp 2FA error: {e}")
        return {"ok": False, "key": None}


async def create_2fa_code(user_id: str, phone: str, db: AsyncSession) -> Optional[str]:
    """Create and store a 2FA code, send via SMS."""
    code = generate_2fa_code()
    expires = datetime.utcnow() + timedelta(minutes=TWO_FA_EXPIRY_MINUTES)

    user_row = await db.execute(select(User).where(User.id == user_id))
    user = user_row.scalar_one_or_none()
    full_name = user.full_name if user and user.full_name else None

    two_fa = TwoFactorCode(
        user_id=user_id,
        code=hash_2fa_code(code),  # store only the HMAC hash — never the raw code
        phone=phone,
        expires_at=expires,
    )
    db.add(two_fa)
    await db.commit()

    import asyncio as _asyncio
    # Delivery channel selection:
    #  • WHATSAPP_2FA_PRIMARY → WhatsApp first (free, via Baileys bridge); fall back to
    #    Twilio SMS only if WhatsApp fails, so a user is never locked out.
    #  • ENABLE_WHATSAPP_2FA (and not primary) → send BOTH channels in parallel.
    #  • neither → SMS only (original behaviour).
    if WHATSAPP_2FA_PRIMARY:
        wa = {"ok": False, "key": None}
        try:
            wa = await send_whatsapp_2fa(phone, code, full_name)
        except Exception as _e:
            print(f"[2FA] WhatsApp primary send error: {_e}")
        if wa.get("ok"):
            # Persist the message key so the code can be deleted-for-everyone once
            # it's verified (delete-after-verify). No key = still delivered, just
            # not deletable; harmless.
            if wa.get("key"):
                import json as _json
                two_fa.wa_message_key = _json.dumps(wa["key"])
                await db.commit()
            print("[2FA] code delivered via WhatsApp (primary)")
        else:
            print("[2FA] WhatsApp send failed — falling back to Twilio SMS")
            await send_sms_2fa(phone, code, full_name)
    else:
        tasks = [send_sms_2fa(phone, code, full_name)]
        if ENABLE_WHATSAPP_2FA:
            tasks.append(send_whatsapp_2fa(phone, code, full_name))
        await _asyncio.gather(*tasks, return_exceptions=True)
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

    if not _2fa_code_matches(code, two_fa.code):
        await db.commit()
        return False

    two_fa.verified_at = datetime.utcnow()
    _wa_key_raw = getattr(two_fa, "wa_message_key", None)
    await db.commit()

    # Best-effort: now that the code is verified, delete the WhatsApp code message from
    # the chat (delete-for-everyone) so it doesn't linger. NEVER let a delete failure
    # affect the login — the code is already verified; this is pure hygiene.
    if _wa_key_raw:
        try:
            import json as _json
            from social.whatsapp_provider import delete_message as _wa_delete
            res = await _wa_delete(_json.loads(_wa_key_raw))
            if not res.get("ok"):
                print(f"[2FA] WA code-message delete not confirmed (non-fatal): {res.get('error')}")
        except Exception as _e:
            print(f"[2FA] WA code-message delete failed (non-fatal): {_e}")
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
            if os.getenv("ENVIRONMENT", "production") == "development":
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


def create_email_verification_token(user_id: str, hours: int = 48) -> str:
    """Stateless signed email-verification token (HMAC over user_id + expiry, keyed by
    JWT_SECRET_KEY). No DB row needed — self-contained and tamper-proof."""
    import hmac as _hmac, hashlib as _hl, base64 as _b64, time as _t
    exp = int(_t.time()) + hours * 3600
    msg = f"{user_id}.{exp}"
    sig = _hmac.new(JWT_SECRET_KEY.encode(), msg.encode(), _hl.sha256).hexdigest()[:32]
    return _b64.urlsafe_b64encode(f"{msg}.{sig}".encode()).decode().rstrip("=")


def verify_email_verification_token(token: str) -> Optional[str]:
    """Return the user_id if the token is valid + unexpired, else None."""
    import hmac as _hmac, hashlib as _hl, base64 as _b64, time as _t
    try:
        pad = "=" * (-len(token) % 4)
        raw = _b64.urlsafe_b64decode((token + pad).encode()).decode()
        user_id, exp, sig = raw.rsplit(".", 2)
        if int(exp) < int(_t.time()):
            return None
        expected = _hmac.new(JWT_SECRET_KEY.encode(), f"{user_id}.{exp}".encode(), _hl.sha256).hexdigest()[:32]
        return user_id if _hmac.compare_digest(sig, expected) else None
    except Exception:
        return None


def create_cart_recovery_token(user_id: str, hours: int = 72) -> str:
    """Signed, purpose-scoped ('cart') recovery token (HMAC over user_id + expiry, keyed by
    JWT_SECRET_KEY). Lets an abandoned-cart email/WhatsApp link open the RECIPIENT's own cart
    by logging them into their own account — regardless of which session the browser currently
    holds (a plain /cart link shows whoever is logged into that device, not the recipient).
    Short 72h TTL bounds a forwarded-email window. Purpose tag prevents replaying an
    email-verification token here (and vice-versa)."""
    import hmac as _hmac, hashlib as _hl, base64 as _b64, time as _t
    exp = int(_t.time()) + hours * 3600
    msg = f"cart.{user_id}.{exp}"
    sig = _hmac.new(JWT_SECRET_KEY.encode(), msg.encode(), _hl.sha256).hexdigest()[:32]
    return _b64.urlsafe_b64encode(f"{msg}.{sig}".encode()).decode().rstrip("=")


def verify_cart_recovery_token(token: str) -> Optional[str]:
    """Return the user_id if the token is a valid, unexpired, purpose='cart' token, else None."""
    import hmac as _hmac, hashlib as _hl, base64 as _b64, time as _t
    try:
        pad = "=" * (-len(token) % 4)
        raw = _b64.urlsafe_b64decode((token + pad).encode()).decode()
        body, sig = raw.rsplit(".", 1)
        purpose, user_id, exp = body.split(".", 2)
        if purpose != "cart":
            return None
        if int(exp) < int(_t.time()):
            return None
        expected = _hmac.new(JWT_SECRET_KEY.encode(), body.encode(), _hl.sha256).hexdigest()[:32]
        return user_id if _hmac.compare_digest(sig, expected) else None
    except Exception:
        return None


async def send_verification_email(user, db: AsyncSession) -> bool:
    """Build a verification link and email it via the branded template. Best-effort."""
    try:
        if not getattr(user, "email", ""):
            return False
        token = create_email_verification_token(str(user.id))
        site = os.getenv("FRONTEND_URL", "https://autosparefinder.co.il").rstrip("/")
        verify_link = f"{site}/api/v1/auth/verify-email?token={token}"
        from routes.email_utils import send_template
        from email_templates import verify_email as _verify_tpl
        return await send_template(user.email, user.full_name or "",
                                   _verify_tpl(user.full_name or "", verify_link))
    except Exception as exc:
        print(f"[Email] verification send failed: {exc}")
        return False


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

    reset_link = f"{os.getenv('FRONTEND_URL', 'http://localhost:5173')}/reset-password?token={token}"

    # Branded RTL template via email_utils (provider-neutral: Gmail/any SMTP, SendGrid fallback)
    try:
        from routes.email_utils import send_template
        from email_templates import password_reset
        sent = await send_template(email, user.full_name or "",
                                   password_reset(user.full_name or "", reset_link, minutes=60))
        if not sent and os.getenv("ENVIRONMENT", "production") == "development":
            print(f"[DEV] Password reset link for {email}: {reset_link}")
    except Exception as exc:
        print(f"[ERROR] Failed to send password reset email: {exc}")
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
    db: AsyncSession = Depends(get_pii_db),
) -> User:
    """Extract and validate JWT from Authorization header."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Authentication required")

    token = credentials.credentials
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    session_id = payload.get("session_id")

    # Check that the session has not been revoked (e.g. by logout)
    session_result = await db.execute(
        select(UserSession).where(
            and_(
                UserSession.token == token,
                UserSession.revoked_at.is_(None),
            )
        )
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=401, detail="Session has been revoked")

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


async def get_current_super_admin(current_user: User = Depends(get_current_admin_user)) -> User:
    if not current_user.is_super_admin:
        raise HTTPException(status_code=403, detail="Super admin access required")
    return current_user
