"""READ-ONLY. Step 3: classify every cross-brand-exposed bare Car-Parts.ie offer and build the repair manifest.
Inputs: replay2.jsonl (replay from the 2026-11-14 backup) and replay.jsonl (replay from the 2026-11-15 backup, used when the
longer replay cannot explain the live offer). Classes: VERIFIED_WRONG | VERIFIED_CORRECT | AMBIGUOUS | MISSING_EVIDENCE.
Identity rule: a row's brand and the offer-part's brand both come from part_identity.title_brand(name, article) (the platform's
single identity policy); a shared article, price, description or listing page is NEVER used as identity."""
import asyncio, asyncpg, json, os, sys, collections, hashlib, datetime as dt
sys.path.insert(0, "/app")
import part_identity
SID = open("/tmp/f/sid.txt").read().strip()
r2 = {}; r1 = {}
for l in open("/tmp/f/replay2.jsonl"): x = json.loads(l); r2[x["article"]] = x
for l in open("/tmp/f/replay.jsonl"): x = json.loads(l); r1[x["article"]] = x
meta = json.load(open("/tmp/f/replay2.jsonl.meta.json"))
async def main():
    c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                              server_settings={"default_transaction_read_only": "on", "statement_timeout": "900000"})
    arts = sorted(r2)
    parts = collections.defaultdict(list)
    for i in range(0, len(arts), 20000):
        for r in await c.fetch("""SELECT id::text pid, sku, name, oem_number art, is_active FROM parts_catalog
                                  WHERE oem_number=ANY($1::text[]) AND specifications->>'source'='Car-Parts.ie'""", arts[i:i+20000]):
            parts[r["art"]].append(dict(r))
    pb = {}                                   # part id -> brand
    for a, ps in parts.items():
        for p in ps: pb[p["pid"]] = part_identity.title_brand(p["name"], a)
    out, C = [], collections.Counter()
    for art in arts:
        a2 = r2[art]; a1 = r1.get(art)
        use, src = (a2, "replay_from_20261114_backup") if a2["matched"] else ((a1, "replay_from_20261115_backup") if a1 and a1["matched"] else (None, None))
        base = a2
        X = base["owner_brand"]
        foreign_parts = sorted({b for p in parts.get(art, []) for b in [pb[p["pid"]]] if b and X and b != X})
        foreign_rows = sorted({b for b in (set(base["brands_in_rows"]) | set(a1["brands_in_rows"] if a1 else [])) if b and X and b != X})
        _ws0 = a2["writers"] if a2["matched"] else (a1["writers"] if a1 and a1["matched"] else [])
        _uw = any(w and w["brand"] is None for w in _ws0)
        exposed = bool(foreign_parts or foreign_rows or (X is None) or _uw)
        if not exposed: C["not_exposed(no_other_brand_part_or_row)"] += 1; continue
        rec = {"offer_id": base["offer"], "current_part_id": base["part"], "current_part_sku": base["part_sku"], "current_part_name": base["part_name"],
               "article": art, "owner_brand": X, "foreign_brands_seen": sorted(set(foreign_parts) | set(foreign_rows)),
               "current": base["actual"], "state_at_20261114_snapshot": base["backup"], "state_at_20261115_snapshot": base["backup15"],
               "evidence_source": src, "importer_versions_replayed": "old(<06:46:21Z) / buggy(06:46:21-16:56:32Z) / fixed(>16:56:32Z)"}
        if not use:
            cls, why = "MISSING_EVIDENCE", "live offer value not reproduced by the retained payload replay (writer not identifiable)"
        else:
            ws = use["writers"]; kinds = set()
            for w in ws:
                if w is None: kinds.add("none")
                elif w["brand"] is None or X is None: kinds.add("unresolved")
                elif w["brand"] == X: kinds.add("same")
                else: kinds.add("cross")
            rec["last_writer_candidates"] = ws
            cf = use["counterfactual"]; rec["counterfactual_owner_only_state"] = cf
            if kinds == {"same"}:
                cls, why = "VERIFIED_CORRECT", "last writer row is the owner brand (payload row, exact value, updated_at inside the batch window)"
                if {m for w in ws for m in w["batch_modes"]} == {"old"}: rec["verification_strength"] = "consistent_not_independent(old importer renamed the part with the same row; owner may be set by that row)"
            elif kinds == {"cross"}:
                same_val = all(x["price"] == use["actual"]["price"] and (x["url"] or "").lower() == (use["actual"]["url"] or "").lower() for x in cf)
                if same_val and any(x["writer"] for x in cf): cls, why = "VERIFIED_CORRECT", "a foreign-brand row wrote the offer but the value is identical to the state written by an owner-brand payload row"; rec["foreign_writer_identical_value"] = True
                elif same_val: cls, why = "AMBIGUOUS", "foreign-brand row value equals the snapshot value and no owner-brand row exists: the snapshot itself may be foreign (reviewer finding)"
                else:
                    cls, why = "VERIFIED_WRONG", "last writer row belongs to a different resolved brand (payload row, exact price+url, updated_at inside the batch window)"
                    modes = {m for w in ws for m in w["batch_modes"]}
                    rec["cause"] = ("listing_page_same_url_defect(buggy importer 06:46-16:56Z)" if modes == {"buggy"} else "original_C_defect(old importer, last-writer-wins on bare key)" if modes == {"old"} else "boundary_batch(old/buggy ambiguous)")
            elif kinds == {"none"}: cls, why = "MISSING_EVIDENCE", "no payload row wrote the offer since the replay start; its state predates the retained evidence (backup value only)"
            else: cls, why = "AMBIGUOUS", "writer brand or owner brand unresolved, or candidate writers disagree: " + ",".join(sorted(kinds))
        rec["classification"], rec["reason"] = cls, why
        C[cls] += 1; out.append(rec)
    # ---- repair targets, price corroboration, missing offers ----
    wrong = [r for r in out if r["classification"] == "VERIFIED_WRONG"]
    ph = collections.defaultdict(list)
    ids = [r["offer_id"] for r in wrong]
    for i in range(0, len(ids), 5000):
        for h in await c.fetch("""SELECT supplier_part_id::text sp, old_price_ils, new_price_ils, source, created_at FROM price_history
                                  WHERE supplier_part_id=ANY($1::uuid[]) AND created_at>=TIMESTAMP '2026-11-14 03:00' ORDER BY created_at""", ids[i:i+5000]):
            ph[h["sp"]].append({"old": str(h["old_price_ils"]), "new": str(h["new_price_ils"]), "src": h["source"], "at": h["created_at"].isoformat()})
    for r in wrong:
        cf = r["counterfactual_owner_only_state"]
        if len(cf) != 1: r["repair"] = {"action": "NONE", "why": "owner-only state not unique"}; r["classification_note"] = "ownership proven, repair value ambiguous"; C["wrong_cf_not_unique"] += 1; continue
        cf = cf[0]; r["price_history_since_20261114"] = ph.get(r["offer_id"], [])
        src = "owner_brand_payload_row" if cf["writer"] else "backup_snapshot_value(not proof of ownership)"
        fw = r["last_writer_candidates"][0]
        cross_t = fw["payload_time"].replace("+00:00", "")
        first_after = next((e for e in r["price_history_since_20261114"] if e["at"] >= cross_t[:19]), None)
        ph_equal = bool(first_after and cf["price"] is not None and abs(float(first_after["old"]) - cf["price"]) < 0.011)
        ph_contra = bool(first_after and cf["price"] is not None and abs(float(first_after["old"]) - cf["price"]) >= 0.011)
        modes = {m for w in r["last_writer_candidates"] for m in w["batch_modes"]}
        if cf["writer"]:
            seq_ok = cf["writer"]["import_end"] < fw["payload_time"]
            ready = "READY" if (seq_ok and not ph_contra) else "NOT_READY"
            reason = "owner-brand row precedes the foreign writer strictly and price_history does not contradict" if ready == "READY" else ("owner row batch overlaps/follows the foreign batch (true order unproven)" if not seq_ok else "price_history contradicts the target price")
        elif modes == {"buggy"}: ready, reason = "UNDO_CANDIDATE_NEEDS_SEPARATE_APPROVAL", "restores the value present before the 06:46-16:56Z defect wrote; ownership of that prior value is not proven"
        else: ready, reason = "NOT_READY", "target is the 11-14 backup value; backups contain foreign-brand values (reviewer finding), no owner-brand row exists"
        r["repair"] = {"readiness": ready, "readiness_reason": reason, "action": "UPDATE supplier_parts SET price_ils,supplier_url (offer id + part id + expected current values checked)", "target_price": cf["price"], "target_url": cf["url"],
                       "target_source": src, "price_history_old_price_equals_target(informational, not proof)": ph_equal, "price_differs_from_current": cf["price"] != r["current"]["price"], "url_differs_from_current": (cf["url"] or "").lower() != (r["current"]["url"] or "").lower()}
        C["repair_" + ready] += 1
        C["wrong_price_reconstructable_from_owner_row"] += bool(cf["writer"]); C["wrong_price_from_backup_only"] += not cf["writer"]
        C["wrong_price_differs"] += r["repair"]["price_differs_from_current"]
    # missing offers: foreign rows that, under the buggy importer, wrote a bare offer instead of their own qualified one
    bpart = {}
    miss = []
    byart = {r["article"]: r for r in out}
    ev_by_art = collections.defaultdict(list)
    for art, a in r2.items():
        for e in a["cross_brand_write_events"]: ev_by_art[art].append(e)
    pairs = {}
    for art, evs in ev_by_art.items():
        for e in evs:
            if e["brand"] is None: continue
            pairs.setdefault((art, e["brand"]), set()).add(e["mode"])
    pids = [p["pid"] for (art, br) in pairs for p in parts.get(art, []) if pb[p["pid"]] == br]
    have = set()
    for i in range(0, len(pids), 20000):
        for r in await c.fetch("SELECT DISTINCT part_id::text p FROM supplier_parts WHERE supplier_id=$1::uuid AND part_id=ANY($2::uuid[])", SID, pids[i:i+20000]): have.add(r["p"])
    for (art, br), modes in sorted(pairs.items()):
        cands = [p for p in parts.get(art, []) if pb[p["pid"]] == br]
        rec = {"article": art, "foreign_brand": br, "modes_seen": sorted(modes), "candidate_parts": [{"part_id": p["pid"], "sku": p["sku"], "has_any_car_parts_ie_offer": p["pid"] in have} for p in cands]}
        if not cands: rec["classification"], rec["finding"] = "MISSING_EVIDENCE", "no part of that brand+article found"
        elif any(p["pid"] in have for p in cands): rec["classification"], rec["finding"] = "VERIFIED_CORRECT", "that brand already has an offer on a part of its own"
        else: rec["classification"], rec["finding"] = "VERIFIED_WRONG", "MISSING_OFFER (brand part(s) exist, none owns a Car-Parts.ie offer)"
        rec["cause"] = "buggy-window" if "buggy" in modes else "original_C_defect(old importer)"
        miss.append(rec); C["missing_pair_" + rec["classification"] + "_" + ("buggy" if "buggy" in modes else "old")] += 1
    summary = {"generated_utc": dt.datetime.utcnow().isoformat(), "replay_meta_t_read_utc": meta["t_read_utc"], "counts": dict(C), "exposed_offers": len(out),
               "total_bare_offers_replayed": len(r2)}
    json.dump({"summary": summary, "offers": out, "foreign_row_bare_write_events": miss}, open(sys.argv[1], "w"), default=str)
    print(json.dumps(summary, indent=1))
asyncio.run(main())
