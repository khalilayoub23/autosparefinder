"""
create_superuser.py — Bootstrap the first (or any) super-admin account.

Usage (interactive):
    python create_superuser.py

Usage (non-interactive / CI):
    SUPERUSER_EMAIL=admin@example.com \
    SUPERUSER_PASSWORD=StrongPass1! \
    SUPERUSER_PHONE=+9665xxxxxxxx \
    SUPERUSER_NAME="Site Admin" \
    python create_superuser.py

The script creates a new user OR promotes an existing user to
is_admin=True + is_super_admin=True.
"""

import asyncio
import getpass
import os
import re
import sys

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

# ---------------------------------------------------------------------------
# DB connection (PII database — where users live)
# ---------------------------------------------------------------------------
DATABASE_PII_URL = os.getenv(
    "DATABASE_PII_URL",
    "postgresql+asyncpg://autospare:autospare@localhost:5432/autospare_pii",
)

pii_engine = create_async_engine(DATABASE_PII_URL, echo=False, future=True)
pii_session_factory = sessionmaker(pii_engine, class_=AsyncSession, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Import the User model and password hasher
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(__file__))
from BACKEND_DATABASE_MODELS import User  # noqa: E402
from BACKEND_AUTH_SECURITY import hash_password  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
_MIN_PASSWORD_LEN = 8


def _prompt(label: str, env_key: str, *, secret: bool = False) -> str:
    value = os.getenv(env_key, "")
    if value:
        display = "****" if secret else value
        print(f"  {label}: {display}  (from env {env_key})")
        return value
    if secret:
        value = getpass.getpass(f"  {label}: ")
    else:
        value = input(f"  {label}: ").strip()
    return value


async def run() -> None:
    print("\n=== AutoSpareFinder — Create Super Admin ===\n")

    email    = _prompt("Email",     "SUPERUSER_EMAIL")
    password = _prompt("Password",  "SUPERUSER_PASSWORD", secret=True)
    phone    = _prompt("Phone",     "SUPERUSER_PHONE")
    name     = _prompt("Full name", "SUPERUSER_NAME")

    # --- Validate inputs ---
    if not _EMAIL_RE.match(email):
        sys.exit("ERROR: invalid email address")
    if len(password) < _MIN_PASSWORD_LEN:
        sys.exit(f"ERROR: password must be at least {_MIN_PASSWORD_LEN} characters")
    if not _PHONE_RE.match(phone):
        sys.exit("ERROR: invalid phone number (use E.164 format, e.g. +9665xxxxxxxx)")
    if not name.strip():
        sys.exit("ERROR: full name is required")

    async with pii_session_factory() as db:
        # --- Enforce single super admin ---
        existing_super = await db.execute(
            select(User).where(User.is_super_admin == True)  # noqa: E712
        )
        existing_super_user = existing_super.scalar_one_or_none()
        if existing_super_user and existing_super_user.email != email:
            sys.exit(
                f"ERROR: A super admin already exists ({existing_super_user.email}). "
                "Only one super admin is allowed. "
                "To transfer the role, run this script with the existing super admin's email."
            )

        result = await db.execute(select(User).where(User.email == email))
        user: User | None = result.scalar_one_or_none()

        if user:
            # Promote existing user (must be the current super admin or no super admin exists)
            user.is_admin       = True
            user.is_super_admin = True
            user.is_active      = True
            user.is_verified    = True
            user.role           = "admin"
            await db.commit()
            print(f"\n✓ Existing user '{email}' is the super admin.\n")
        else:
            # Create brand-new super admin user
            user = User(
                email         = email,
                phone         = phone,
                full_name     = name,
                password_hash = hash_password(password),
                role          = "admin",
                is_admin      = True,
                is_super_admin= True,
                is_active     = True,
                is_verified   = True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            print(f"\n✓ Super admin '{email}' created successfully (id={user.id}).\n")

    await pii_engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
