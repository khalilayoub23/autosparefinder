"""Pre-change export / verify / restore of parts_catalog.base_price (isolated DB, rolled-back transaction)."""
import uuid

import pytest

import base_price_snapshot as snap
from test_base_price_writers import pg  # noqa: F401
from test_importers_e2e import brand, mkpart


async def test_export_verify_corrupt_restore_roundtrip(tmp_path):
    async with pg() as conn:
        mid = await brand(conn, f"zz-snap-{uuid.uuid4().hex[:6]}")
        t = uuid.uuid4().hex[:8]
        for i, b in enumerate([None, 10.5, 99.0]):
            await mkpart(conn, f"SNAP-{t}-{i}", "ZZ", mid, base=b)
        m = await snap.export_snapshot(conn, tmp_path)
        assert m["rows"] >= 3 and m["nonnull"] >= 2
        assert snap.verify_snapshot(tmp_path)["ok"]
        # a later (A3-like) change
        await conn.execute("UPDATE parts_catalog SET base_price = base_price + 1 WHERE sku LIKE $1 AND base_price IS NOT NULL", f"SNAP-{t}-%")
        await conn.execute("UPDATE parts_catalog SET base_price = 5 WHERE sku = $1", f"SNAP-{t}-0")
        new_row = f"SNAP-{t}-new"
        await mkpart(conn, new_row, "ZZ", mid, base=1.0)                      # created after the snapshot
        p = await snap.plan(conn, tmp_path)
        assert p["differ"] >= 3
        dry = await snap.restore(conn, tmp_path, apply=False)
        assert dry["dry_run"] and dry["applied"] == 0
        with pytest.raises(PermissionError):
            await snap.restore(conn, tmp_path, apply=True, confirm="wrong")
        done = await snap.restore(conn, tmp_path, apply=True, confirm=m["sha256"])
        assert done["applied"] >= 3
        vals = {r["sku"]: r["base_price"] for r in await conn.fetch("SELECT sku, base_price FROM parts_catalog WHERE sku LIKE $1", f"SNAP-{t}-%")}
        assert vals[f"SNAP-{t}-0"] is None and float(vals[f"SNAP-{t}-1"]) == 10.5 and float(vals[f"SNAP-{t}-2"]) == 99.0
        assert float(vals[new_row]) == 1.0                                     # new rows untouched
        again = await snap.restore(conn, tmp_path, apply=True, confirm=m["sha256"])
        assert again["applied"] == 0                                           # idempotent


async def test_a_tampered_snapshot_is_refused(tmp_path):
    async with pg() as conn:
        await snap.export_snapshot(conn, tmp_path)
        (tmp_path / snap.CSV_NAME).write_text("tampered\n")
        assert not snap.verify_snapshot(tmp_path)["ok"]
        with pytest.raises(RuntimeError):
            await snap.plan(conn, tmp_path)
