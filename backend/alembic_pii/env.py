"""Alembic env.py for the PII database (autospare_pii)."""
import asyncio
import os
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Load .env so DATABASE_PII_URL is available
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

config = context.config

# Override sqlalchemy.url from env
pii_url = os.environ.get("DATABASE_PII_URL", "")
if pii_url:
    config.set_main_option("sqlalchemy.url", pii_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Migrations are explicit DDL — no need for model metadata
# (PII models live in BACKEND_DATABASE_MODELS.py under PiiBase,
#  but autogenerate is disabled to keep catalog/PII DBs fully independent)
target_metadata = None


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
