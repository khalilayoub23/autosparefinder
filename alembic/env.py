from __future__ import with_statement
import os
import re
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy import text

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import project metadata so autogenerate can detect table changes.
# We import from backend/ since that's where the real models live.
try:
    import sys, os as _os
    # Ensure 'backend/' is on sys.path when running alembic from repo root
    _backend_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'backend')
    if _backend_path not in sys.path:
        sys.path.insert(0, _backend_path)
    from BACKEND_DATABASE_MODELS import Base, PiiBase
    # Combine both metadata objects so autogenerate sees all 35 tables
    target_metadata = [Base.metadata, PiiBase.metadata]
except Exception as _exc:
    print(f"[alembic/env.py] WARNING: Could not import models: {_exc}")
    target_metadata = None

# get DB URL from environment (recommended).
# For the synchronous alembic runner we need a psycopg2 URL, not asyncpg.
_raw_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url") or ""
# Convert postgresql+asyncpg:// → postgresql+psycopg2:// for sync runner
DATABASE_URL = re.sub(r"postgresql\+asyncpg", "postgresql+psycopg2", _raw_url)


def run_migrations_offline():
    context.configure(
        url=DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    from sqlalchemy import create_engine
    connectable = create_engine(DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
