"""
Script:  maintenance/cpie_wrong_offer_manifest.py
Purpose: Build the sha256-pinned REPAIR manifest (gz, the shape cpie_overwrite_repair.py consumes) for the wrong-offer mission from the
         final classification file. READ-ONLY on the database. Takes a FRESH snapshot of every REPAIR row and REFUSES (exit 1) if any row
         drifted from the classified snapshot (price / url / updated_at / part_id / sku), so a stale classification can never be applied.
Process:  1. load classification (sha256 printed, optional --expect-classification-sha256 pin)  2. SELECT each REPAIR offer (read-only tx)
          3. compare with `current_at_snapshot` (the classification's own snapshot)  4. write manifest + .sha256, print counts.
Data Imported/Modified: none in the database; writes --out (manifest) and --out + '.sha256'.
Data Sources: candidates/core-identity-2026-11-16/wrong_offer_repair/classification_1043_final.json.
Last Updated: 2026-11-16
"""
import argparse, asyncio, datetime as dt, gzip, hashlib, json, os, sys
import asyncpg

CPIE = os.getenv("CPIE_SUPPLIER_ID", "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d")
DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")


def build(cls, live):
    """Pure: (classification rows, {offer_id: live row}) -> (manifest dict, [drift messages])."""
    offers, drift = [], []
    for d in cls:
        if d["verdict"] != "REPAIR":
            continue
        l, snap = live.get(d["offer_id"]), d["current_at_snapshot"]
        if l is None:
            drift.append((d["offer_id"], "missing")); continue
        if l["pid"] != d["part_id"] or l["sku"] != d["article"] or l["sid"] != CPIE:
            drift.append((d["offer_id"], "identity")); continue
        if float(l["price"]) != float(snap["price"]) or l["url"] != snap["url"] or dt.datetime.fromisoformat(l["ua"]) != dt.datetime.fromisoformat(snap["updated_at"]):
            drift.append((d["offer_id"], "value/updated_at drift")); continue
        if "~" in d["article"] or not d.get("target"):
            drift.append((d["offer_id"], "no target / qualified key")); continue
        offers.append({"offer_id": d["offer_id"], "current_part_id": d["part_id"], "article": d["article"], "classification": "VERIFIED_WRONG",
                       "current": {"price": float(l["price"]), "url": l["url"], "updated_at": l["ua"]},
                       "repair": {"readiness": "READY", "target_price": d["target"]["price"], "target_url": d["target"]["url"],
                                  "evidence": d["target"]["evidence"], "disposition": d["disposition"]}})
    return {"offers": offers}, drift


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--classification", required=True)
    ap.add_argument("--expect-classification-sha256")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    raw = open(a.classification, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    if a.expect_classification_sha256 and sha != a.expect_classification_sha256:
        raise SystemExit("REFUSED: classification sha256 mismatch")
    cls = json.loads(raw)
    ids = [d["offer_id"] for d in cls if d["verdict"] == "REPAIR"]
    c = await asyncpg.connect(DB)
    try:
        await c.execute("SET default_transaction_read_only=on")
        rows = await c.fetch("SELECT id::text id, part_id::text pid, supplier_id::text sid, supplier_sku sku, price_ils price, supplier_url url, "
                             "to_char(updated_at,'YYYY-MM-DD\"T\"HH24:MI:SS.US') ua FROM supplier_parts WHERE id=ANY($1::uuid[])", ids)
    finally:
        await c.close()
    live = {r["id"]: dict(r) for r in rows}
    man, drift = build(cls, live)
    if drift:
        print(json.dumps({"drift": drift}))
        raise SystemExit(1)
    with gzip.open(a.out, "wt") as f:
        json.dump(man, f)
    msha = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
    open(a.out + ".sha256", "w").write(msha + "\n")
    print(json.dumps({"classification_sha256": sha, "manifest": a.out, "manifest_sha256": msha, "rows": len(man["offers"])}))


if __name__ == "__main__":
    asyncio.run(main())
