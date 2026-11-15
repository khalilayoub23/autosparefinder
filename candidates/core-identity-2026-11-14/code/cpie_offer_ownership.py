"""
Script:  maintenance/cpie_offer_ownership.py
Purpose: Workstream C (Option 1). For every car-parts.ie article carried by >=2 different brands, decide which PART the
         single existing offer row (supplier Car-Parts.ie, supplier_sku = bare article) really belongs to, using the
         offer's own supplier_url as evidence, and write a manifest: KEEP (holder already right), MOVE (re-point the
         offer to the proven part) or HOLD (evidence not unique -> untouched, reported).
Process:  default run = build the manifest (READ-ONLY, sha256-stamped).
         `--apply --manifest M --expect-sha256 H --approval-ref R`: gate_or_exit (offer policy), then ONLY the MOVE
         rows, one transaction each: both parts locked NOWAIT, the offer must still be on `from_part` with the recorded
         url+price, the target part must hold no Car-Parts.ie offer; the offer's part_id is changed - nothing is
         deleted, no price or url is touched. The previous part_id of every moved offer is written to the result file.
         Run only with the harvester stopped (approved operation) and the actor listed in offer_policy.POLICY_BOUND_ACTORS.
         `--rollback --result RESULT.json --approval-ref R`: puts every applied offer back on its previous part (only if
         it is still on the part the result recorded and the previous part holds no Car-Parts.ie offer); price/url untouched.
Data Imported/Modified: supplier_parts.part_id of MOVE rows only (apply / rollback); nothing in manifest mode.
Data Sources: parts_catalog (specifications.product_url, name), supplier_parts, part_identity brand vocabulary.
Missing Data Delegation: parts of the other brands get no offer here (their real prices were overwritten); the importer
         creates them under a brand-qualified key on the next harvest.
Last Updated: 2026-11-15
"""
from __future__ import annotations
import argparse, asyncio, collections, hashlib, json, os, re, sys, time
from pathlib import Path
import asyncpg
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import part_identity as pi  # noqa: E402

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
SUP = "88f6ce24-ab5f-4f99-8b41-03cf6828cf3d"
OUT = Path(os.getenv("STATE_DIR", "/app/state")) / "cpie_ownership"


def _u(url):
    u = re.sub(r"^https?://(www\.)?", "", (url or "").strip().lower()).split("?")[0].split("#")[0].rstrip("/")
    return u


def _cat(url):
    parts = [x for x in _u(url).split("/") if x]
    while parts and parts[-1].isdigit():  # trailing numeric listing id
        parts.pop()
    return parts[-1] if len(parts) >= 4 else ""


def _make(url):
    parts = _u(url).split("/")
    return parts[2] if len(parts) >= 3 else ""


async def build() -> Path:
    conn = await asyncpg.connect(DB)
    await conn.execute("SET statement_timeout='900s'")
    parts = await conn.fetch("""SELECT id::text id, sku, oem_number art, name, specifications->>'product_url' pu, is_active
        FROM parts_catalog WHERE specifications->>'source'='Car-Parts.ie'""")
    offers = {r["supplier_sku"]: r for r in await conn.fetch(
        "SELECT id::text id, part_id::text part_id, supplier_sku, supplier_url, price_ils::float price FROM supplier_parts WHERE supplier_id=$1::uuid", SUP)}
    await conn.close()
    by = collections.defaultdict(list)
    for p in parts:
        by[p["art"]].append(p)
    items, counts = [], collections.Counter()
    for art in sorted(by):
        rs = by[art]
        brand = {r["id"]: pi.title_brand(r["name"], art) for r in rs}
        if len({b for b in brand.values() if b}) < 2:
            continue
        off = offers.get(art)
        if off is None:
            counts["HOLD_no_offer"] += 1
            items.append({"article": art, "action": "NONE_no_offer", "parts": [r["id"] for r in rs]}); continue
        holder = next((r for r in rs if r["id"] == off["part_id"]), None)
        ou, oc = _u(off["supplier_url"]), _cat(off["supplier_url"])
        t1 = [r for r in rs if _u(r["pu"]) == ou and brand[r["id"]]]
        if len(t1) >= 1 and len({brand[r["id"]] for r in t1}) == 1:
            tier, cands = "T1_exact_url", t1
        else:
            t2 = [r for r in rs if oc and _cat(r["pu"]) == oc and _make(r["pu"]) == _make(off["supplier_url"]) and brand[r["id"]]]
            if t2 and len({brand[r["id"]] for r in t2}) == 1:
                tier, cands = "T2_make_and_category", t2
            else:
                counts["HOLD_evidence_not_unique"] += 1
                items.append({"article": art, "offer": off["id"], "action": "HOLD", "reason": "offer URL does not single out one brand/part",
                              "holder": off["part_id"], "parts": [[r["id"], brand[r["id"]], r["pu"]] for r in rs]}); continue
        owner_brand = brand[cands[0]["id"]]
        if holder and brand.get(holder["id"]) == owner_brand:
            counts["KEEP"] += 1; act, target = "KEEP", holder["id"]
        else:
            tgt = sorted(cands, key=lambda r: (r["id"] != (cands[0]["id"]), r["sku"]))
            exact = [r for r in cands if _u(r["pu"]) == ou] or cands
            if len({r["id"] for r in exact}) != 1:
                counts["HOLD_target_not_unique"] += 1
                items.append({"article": art, "offer": off["id"], "action": "HOLD", "reason": f"{len(exact)} candidate parts of brand {owner_brand}",
                              "holder": off["part_id"], "candidates": [r["id"] for r in exact]}); continue
            if tier != "T1_exact_url":  # category-only evidence is not enough to move an offer: fail closed
                counts["HOLD_move_needs_exact_url"] += 1
                items.append({"article": art, "offer": off["id"], "action": "HOLD", "reason": "move proposed on category-only evidence",
                              "holder": off["part_id"], "owner_brand": owner_brand, "candidates": [r["id"] for r in exact]}); continue
            counts["MOVE"] += 1; act, target = "MOVE", exact[0]["id"]
        items.append({"article": art, "offer": off["id"], "action": act, "tier": tier, "owner_brand": owner_brand,
                      "from_part": off["part_id"], "to_part": target, "price": off["price"], "url": off["supplier_url"],
                      "holder_brand": brand.get(off["part_id"]),
                      "other_brand_parts_without_offer": sorted(r["id"] for r in rs if brand[r["id"]] != owner_brand)})
    man = {"tool": "maintenance/cpie_offer_ownership.py", "counts": dict(counts), "articles_in_scope": sum(counts.values()), "items": items}
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"cpie_ownership_manifest_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    p.write_text(json.dumps(man, sort_keys=True))
    print(json.dumps({"manifest": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "counts": dict(counts), "in_scope": man["articles_in_scope"]}))
    return p


ACTOR = "maintenance/cpie_offer_ownership.py"


async def apply(path: Path, ref: str) -> None:
    man = json.load(open(path))
    conn = await asyncpg.connect(DB)
    done, skipped = [], []
    try:
        for it in man["items"]:
            if it.get("action") != "MOVE":
                continue
            try:
                async with conn.transaction():
                    got = await conn.fetch("SELECT id FROM parts_catalog WHERE id = ANY($1::uuid[]) FOR UPDATE NOWAIT",
                                           [it["from_part"], it["to_part"]])
                    off = await conn.fetchrow("SELECT part_id::text pid, supplier_url, price_ils::float price FROM supplier_parts "
                                              "WHERE id=$1::uuid FOR UPDATE NOWAIT", it["offer"])
                    if len(got) != 2 or off is None or off["pid"] != it["from_part"] or off["supplier_url"] != it["url"] \
                            or abs((off["price"] or 0) - (it["price"] or 0)) > 1e-9:
                        raise RuntimeError("precondition: offer or parts changed since the manifest")
                    if await conn.fetchval("SELECT 1 FROM supplier_parts WHERE part_id=$1::uuid AND supplier_id=$2::uuid LIMIT 1", it["to_part"], SUP):
                        raise RuntimeError("precondition: target part already holds a Car-Parts.ie offer")
                    await conn.execute("UPDATE supplier_parts SET part_id=$2::uuid, updated_at=NOW() WHERE id=$1::uuid", it["offer"], it["to_part"])
                    done.append({"offer": it["offer"], "previous_part_id": it["from_part"], "new_part_id": it["to_part"], "article": it["article"]})
            except Exception as exc:  # noqa: BLE001 - one row's failure must not stop the others; it is reported
                skipped.append({"offer": it.get("offer"), "article": it.get("article"), "reason": str(exc)[:200]})
    finally:
        await conn.close()
    res = OUT / f"cpie_ownership_result_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    res.write_text(json.dumps({"manifest": str(path), "manifest_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                               "approval_ref": ref, "applied": len(done), "skipped": len(skipped), "done": done, "skipped_rows": skipped}))
    print(json.dumps({"result": str(res), "applied": len(done), "skipped": len(skipped)}))


async def rollback(result_path: Path, ref: str, dsn: str = None) -> dict:
    res = json.load(open(result_path))
    conn = await asyncpg.connect(dsn or DB)
    back, skipped = [], []
    try:
        for d in res["done"]:
            try:
                async with conn.transaction():
                    off = await conn.fetchrow("SELECT part_id::text pid FROM supplier_parts WHERE id=$1::uuid FOR UPDATE NOWAIT", d["offer"])
                    if off is None or off["pid"] != d["new_part_id"]:
                        raise RuntimeError("precondition: offer is no longer on the part the result recorded")
                    if await conn.fetchval("SELECT 1 FROM supplier_parts WHERE part_id=$1::uuid AND supplier_id=$2::uuid LIMIT 1",
                                           d["previous_part_id"], SUP):
                        raise RuntimeError("precondition: previous part already holds a Car-Parts.ie offer")
                    await conn.execute("UPDATE supplier_parts SET part_id=$2::uuid, updated_at=NOW() WHERE id=$1::uuid", d["offer"], d["previous_part_id"])
                    back.append(d["offer"])
            except Exception as exc:  # noqa: BLE001
                skipped.append({"offer": d["offer"], "reason": str(exc)[:200]})
    finally:
        await conn.close()
    out = OUT / f"cpie_ownership_rollback_{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json"
    out.write_text(json.dumps({"result": str(result_path), "approval_ref": ref, "restored": len(back), "skipped": len(skipped), "skipped_rows": skipped}))
    print(json.dumps({"rollback": str(out), "restored": len(back), "skipped": len(skipped)}))
    return {"restored": len(back), "skipped": skipped}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--result")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--manifest")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--approval-ref")
    a = ap.parse_args()
    if a.rollback:
        import offer_policy
        offer_policy.gate_or_exit(ACTOR, False)
        if not a.approval_ref or not a.result:
            print("REFUSED: --result and --approval-ref are required"); raise SystemExit(3)
        asyncio.run(rollback(Path(a.result), a.approval_ref))
    elif not a.apply:
        asyncio.run(build())
    else:
        import offer_policy
        offer_policy.gate_or_exit(ACTOR, False)
        mp = Path(a.manifest)
        if not a.expect_sha256 or hashlib.sha256(mp.read_bytes()).hexdigest() != a.expect_sha256 or not a.approval_ref:
            print("REFUSED: --expect-sha256 must equal the manifest hash and --approval-ref is required")
            raise SystemExit(3)
        asyncio.run(apply(mp, a.approval_ref))
