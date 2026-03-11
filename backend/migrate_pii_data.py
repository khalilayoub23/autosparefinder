"""
migrate_pii_data.py — One-time script to copy all PII rows from
autospare (catalog DB) into autospare_pii (PII DB).

Run BEFORE running the 0003_drop_pii_tables Alembic migration.
Run from backend/ directory with the venv active.
"""
import asyncio
import asyncpg

CAT_DSN = "postgresql://autospare:autospare_dev@localhost:5432/autospare"
PII_DSN = "postgresql://autospare:autospare_dev@localhost:5432/autospare_pii"

# Tables in dependency order (parents before children)
PII_TABLES = [
    "users",
    "vehicles",
    "user_profiles",
    "user_sessions",
    "two_factor_codes",
    "login_attempts",
    "password_resets",
    "user_vehicles",
    "files",
    "file_metadata",
    "orders",
    "order_items",
    "payments",
    "invoices",
    "returns",
    "conversations",
    "messages",
    "agent_actions",
    "agent_ratings",
    "notifications",
]


async def get_columns(conn, table: str) -> list[str]:
    """Return column names in their actual table order."""
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=$1 ORDER BY ordinal_position",
        table,
    )
    return [r["column_name"] for r in rows]


async def migrate_table(cat_conn, pii_conn, table: str) -> int:
    # Get columns that exist in BOTH source and destination
    src_cols = set(await get_columns(cat_conn, table))
    dst_cols = set(await get_columns(pii_conn, table))
    common = [c for c in await get_columns(cat_conn, table) if c in dst_cols]

    if not common:
        print(f"  ⚠️  {table}: no common columns, skipping")
        return 0

    col_list = ", ".join(f'"{c}"' for c in common)
    only_src = src_cols - dst_cols
    only_dst = dst_cols - src_cols
    if only_src:
        print(f"  ℹ️  {table}: source-only cols (dropped): {sorted(only_src)}")
    if only_dst:
        print(f"  ℹ️  {table}: dest-only cols (will be NULL): {sorted(only_dst)}")

    rows = await cat_conn.fetch(f'SELECT {col_list} FROM "{table}"')
    if not rows:
        print(f"  ✅ {table}: 0 rows (empty)")
        return 0

    from datetime import datetime, timezone

    def _strip_tz(v):
        """Strip timezone from aware datetimes — dest columns are naive."""
        if isinstance(v, datetime) and v.tzinfo is not None:
            return v.astimezone(timezone.utc).replace(tzinfo=None)
        return v

    # Disable FK checks per-statement by inserting with a temp override
    placeholders = ", ".join(f"${i + 1}" for i in range(len(common)))
    insert_sql = f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING'

    # Convert records to tuples, stripping tz from datetimes
    data = [tuple(_strip_tz(row[c]) for c in common) for row in rows]
    await pii_conn.executemany(insert_sql, data)
    print(f"  ✅ {table}: {len(rows)} rows copied")
    return len(rows)


async def main():
    print("Connecting to databases…")
    cat_conn = await asyncpg.connect(CAT_DSN)
    pii_conn = await asyncpg.connect(PII_DSN)

    # Temporarily disable FK constraints in PII DB for bulk load
    await pii_conn.execute("SET session_replication_role = 'replica'")

    total = 0
    errors = []
    for table in PII_TABLES:
        try:
            n = await migrate_table(cat_conn, pii_conn, table)
            total += n
        except Exception as exc:
            errors.append((table, str(exc)))
            print(f"  ❌ {table}: {exc}")

    # Re-enable FK constraints
    await pii_conn.execute("SET session_replication_role = 'origin'")

    await cat_conn.close()
    await pii_conn.close()

    print(f"\n{'='*50}")
    print(f"Total rows migrated: {total}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for t, e in errors:
            print(f"  {t}: {e}")
    else:
        print("✅ All tables migrated successfully")
    print("Now run: alembic upgrade head  (to drop PII tables from catalog DB)")


if __name__ == "__main__":
    asyncio.run(main())
