from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import requests
from part_type_taxonomy import PART_TYPE_FAMILIES
from requests.exceptions import RequestException

API_URL = "https://data.gov.il/api/3/action/datastore_search"
RESOURCE_IDS = [
    "142afde2-6228-49f9-8a29-9b6c3a0cbe40",
    "5e87a7a1-2f6f-41c1-8aec-7216d52a6cf6",
]
DATA_DIR = Path(__file__).parent / "data"


@dataclass
class DatasetSlice:
    resource_id: str
    total: int
    fields: List[str]
    records: List[Dict[str, Any]]


def _clean_space(value: str) -> str:
    value = (value or "").replace("\u00a0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _strip_punct(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u0590-\u05FF ]+", " ", value)


# Hebrew country/origin words that appear as suffixes in tozeret_nm / tozar
# e.g. "טויוטה יפן" → "טויוטה", "מרצדס בנץ גרמנ" → "מרצדס בנץ"
_HE_COUNTRY_TOKENS = re.compile(
    r"יפן|גרמניה|גרמנ|ספרד|צרפת|קוריאה|ארהב|"
    r"אנגליה|איטליה|שוודיה|בלגיה|הולנד|סלובקיה|הונגריה|פולין|"
    r"תאילנד|תאילנ|הודו|סין|טורקיה|תורכיה|ברזיל|מקסיקו|"
    r"ארצות|ממלכה|בריטניה",
    re.UNICODE,
)

def _norm_key(value: str) -> str:
    s = _clean_space(value).lower()
    s = _strip_punct(s)
    # Strip Hebrew country/origin suffixes before key comparison
    s = _HE_COUNTRY_TOKENS.sub(" ", s)
    s = re.sub(r"\b(company|co|ltd|inc|llc|motors?|auto|automotive|group|corp|corporation|international)\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_manufacturer(record: Dict[str, Any]) -> str:
    # tozar = clean brand name without country suffix (e.g. "מזדה")
    # tozeret_nm = brand+country variant (e.g. "מזדה יפן") — fallback only
    candidates = [
        str(record.get("tozar") or "").strip(),
        str(record.get("tozeret_nm") or "").strip(),
        str(record.get("tozeret_eretz_nm") or "").strip(),
        str(record.get("tozeret_cd") or "").strip(),
    ]
    for c in candidates:
        if c:
            return _clean_space(c)
    return ""


def _extract_model(record: Dict[str, Any]) -> str:
    candidates = [
        str(record.get("degem_nm") or "").strip(),
        str(record.get("degem_cd") or "").strip(),
    ]
    for c in candidates:
        if c:
            return _clean_space(c)
    return ""


def _extract_year(record: Dict[str, Any]) -> int | None:
    raw = str(record.get("shnat_yitzur") or "").strip()
    if not raw:
        return None
    try:
        y = int(raw)
    except Exception:
        return None
    if 1950 <= y <= 2050:
        return y
    return None


def _extract_engine(record: Dict[str, Any]) -> str:
    return _clean_space(str(record.get("nefah_manoa") or ""))


def _extract_fuel(record: Dict[str, Any]) -> str:
    return _clean_space(str(record.get("delek_nm") or record.get("delek_cd") or ""))


def _extract_trim(record: Dict[str, Any]) -> str:
    return _clean_space(str(record.get("ramat_gimur") or ""))


def fetch_dataset(resource_id: str, limit: int = 1000, max_records: int | None = None) -> DatasetSlice:
    offset = 0
    total = 0
    fields: List[str] = []
    rows: List[Dict[str, Any]] = []

    while True:
        params = {
            "resource_id": resource_id,
            "limit": int(limit),
            "offset": int(offset),
        }
        payload = None
        last_err = None
        for attempt in range(1, 6):
            try:
                resp = requests.get(API_URL, params=params, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
                break
            except RequestException as exc:
                last_err = exc
                time.sleep(min(2 ** attempt, 10))
        if payload is None:
            raise RuntimeError(f"datastore fetch failed for resource_id={resource_id} offset={offset}: {last_err}")
        if not payload.get("success"):
            raise RuntimeError(f"API returned success=false for resource_id={resource_id}")

        result = payload.get("result") or {}
        if not total:
            total = int(result.get("total") or 0)
            fields = [str(f.get("id") or "") for f in (result.get("fields") or [])]

        recs = result.get("records") or []
        if not recs:
            break

        for rec in recs:
            if isinstance(rec, dict):
                rec["__resource_id"] = resource_id
        rows.extend(recs)
        offset += len(recs)

        if max_records and len(rows) >= max_records:
            rows = rows[:max_records]
            break
        if offset >= total:
            break

        time.sleep(0.05)

    return DatasetSlice(resource_id=resource_id, total=total, fields=fields, records=rows)


def _merge_slices(slices: Iterable[DatasetSlice]) -> DatasetSlice:
    all_fields = set()
    merged_records: List[Dict[str, Any]] = []
    total = 0
    ids: List[str] = []
    for ds in slices:
        total += int(ds.total)
        all_fields.update(ds.fields)
        merged_records.extend(ds.records)
        ids.append(ds.resource_id)
    merged_id = ",".join(ids)
    return DatasetSlice(resource_id=merged_id, total=total, fields=sorted(all_fields), records=merged_records)


def build_reports(ds: DatasetSlice) -> Dict[str, Any]:
    mfr_counter: Counter[str] = Counter()
    model_counter: Counter[str] = Counter()
    year_counter: Counter[int] = Counter()
    engine_counter: Counter[str] = Counter()
    fuel_counter: Counter[str] = Counter()
    trim_counter: Counter[str] = Counter()

    missing_mfr = 0
    missing_model = 0
    missing_year = 0

    norm_groups: Dict[str, Counter[str]] = defaultdict(Counter)

    for r in ds.records:
        mfr = _extract_manufacturer(r)
        model = _extract_model(r)
        year = _extract_year(r)
        engine = _extract_engine(r)
        fuel = _extract_fuel(r)
        trim = _extract_trim(r)

        if not mfr:
            missing_mfr += 1
        else:
            # mispar_rechavim_pailim = active vehicles on Israeli roads (resource 5e87a7a1)
            # falls back to 1 per model-spec row when field is absent (resource 142afde2)
            fleet_count = int(r.get("mispar_rechavim_pailim") or 0) or 1
            mfr_counter[mfr] += fleet_count
            nk = _norm_key(mfr)
            norm_groups[nk][mfr] += fleet_count

        if not model:
            missing_model += 1
        else:
            model_counter[model] += 1

        if year is None:
            missing_year += 1
        else:
            year_counter[year] += 1

        if engine:
            engine_counter[engine] += 1
        if fuel:
            fuel_counter[fuel] += 1
        if trim:
            trim_counter[trim] += 1

    canonical_registry: List[Dict[str, Any]] = []
    for nk, variants in norm_groups.items():
        if not nk:
            continue
        ordered = sorted(variants.items(), key=lambda x: (-x[1], x[0]))
        canonical_name = ordered[0][0]
        aliases = [name for name, _ in ordered[1:]]
        canonical_registry.append(
            {
                "canonical_key": nk,
                "canonical_name": canonical_name,
                "aliases": aliases,
                "variant_count": len(ordered),
                "total_records": sum(variants.values()),
            }
        )

    canonical_registry.sort(key=lambda x: (-x["total_records"], x["canonical_name"]))

    dup_groups = [
        {
            "canonical_key": item["canonical_key"],
            "canonical_name": item["canonical_name"],
            "aliases": item["aliases"],
            "total_records": item["total_records"],
        }
        for item in canonical_registry
        if item["variant_count"] > 1
    ]

    quality_issues = {
        "missing_manufacturer_records": missing_mfr,
        "missing_model_records": missing_model,
        "missing_year_records": missing_year,
        "duplicate_or_alias_manufacturer_groups": len(dup_groups),
        "blank_or_corrupted_manufacturer_tokens": sum(1 for k in norm_groups if not k),
    }

    return {
        "summary": {
            "fetched_records": len(ds.records),
            "source_total_records": ds.total,
            "field_count": len(ds.fields),
            "distinct_manufacturers_raw": len(mfr_counter),
            "distinct_manufacturers_canonical": len(canonical_registry),
            "distinct_models": len(model_counter),
            "distinct_years": len(year_counter),
            "distinct_engine_variants": len(engine_counter),
            "distinct_fuel_types": len(fuel_counter),
            "distinct_trim_values": len(trim_counter),
        },
        "quality_issues": quality_issues,
        "manufacturer_frequency": [{"manufacturer": k, "count": v} for k, v in mfr_counter.most_common()],
        "model_frequency_top_200": [{"model": k, "count": v} for k, v in model_counter.most_common(200)],
        "year_distribution": [{"year": y, "count": c} for y, c in sorted(year_counter.items())],
        "fuel_distribution": [{"fuel": k, "count": v} for k, v in fuel_counter.most_common()],
        "trim_distribution_top_200": [{"trim": k, "count": v} for k, v in trim_counter.most_common(200)],
        "canonical_manufacturer_registry": canonical_registry,
        "alias_groups": dup_groups,
        "normalization_rules": {
            "capitalization": "case-insensitive canonical key generation",
            "spacing": "collapse consecutive whitespace, trim edges",
            "punctuation": "strip non-alnum punctuation before key generation",
            "transliteration": "dataset-driven alias grouping by normalized token",
            "abbreviation": "generic legal/entity suffix stripping (company/co/ltd/inc/llc/motors/group/etc)",
        },
    }


def build_priority_tiers(canonical_registry: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = sum(int(x.get("total_records") or 0) for x in canonical_registry)
    cum = 0
    tier1: List[Dict[str, Any]] = []
    tier2: List[Dict[str, Any]] = []
    tier3: List[Dict[str, Any]] = []

    for item in canonical_registry:
        cnt = int(item.get("total_records") or 0)
        projected = (cum + cnt) / total if total > 0 else 1.0
        row = {
            "canonical_name": item["canonical_name"],
            "canonical_key": item["canonical_key"],
            "total_records": cnt,
            "aliases": item.get("aliases") or [],
            "fleet_share_pct": round((cnt / total) * 100, 4) if total else 0.0,
        }
        if projected <= 0.80 or not tier1:
            tier1.append(row)
        elif projected <= 0.95 or not tier2:
            tier2.append(row)
        else:
            tier3.append(row)
        cum += cnt

    return {
        "total_canonical_manufacturers": len(canonical_registry),
        "total_records": total,
        "tiers": {
            "tier1_dominant": tier1,
            "tier2_mid_volume": tier2,
            "tier3_long_tail": tier3,
        },
        "batching_strategy": {
            "default_manufacturers_per_batch": 5,
            "default_records_window_per_batch": 5000,
            "checkpoint_file": "backend/data/rex_transport_import_checkpoint.json",
            "queue_file": "backend/data/rex_transport_import_queue.json",
            "resumable": True,
            "idempotent_required": True,
            "promotion_requires_validation": True,
        },
    }


def build_queue(priority: Dict[str, Any]) -> List[Dict[str, Any]]:
    queue: List[Dict[str, Any]] = []
    i = 0
    for tier_name in ["tier1_dominant", "tier2_mid_volume", "tier3_long_tail"]:
        for row in priority["tiers"][tier_name]:
            i += 1
            queue.append(
                {
                    "queue_id": i,
                    "tier": tier_name,
                    "manufacturer": row["canonical_name"],
                    "canonical_key": row["canonical_key"],
                    "estimated_records": row["total_records"],
                    "status": "pending",
                    "attempts": 0,
                    "last_error": None,
                    "last_run_at": None,
                }
            )
    return queue


def build_high_priority_parts_import_list(
    priority: Dict[str, Any],
    queue: List[Dict[str, Any]],
    reports: Dict[str, Any],
    per_lane_families: int = 8,
) -> List[Dict[str, Any]]:
    """Build deterministic staged part import candidates for Rex.

    Produces manufacturer-scoped, tier-aware part-family tasks across three lanes:
    OEM-only, OES-compatible, and aftermarket-compatible.
    """

    group_rank = {
        "powertrain": 0,
        "chassis": 1,
        "electrical": 2,
        "maintenance": 3,
        "body": 4,
    }

    families = sorted(
        [f for f in PART_TYPE_FAMILIES],
        key=lambda f: (group_rank.get(f.group_id, 99), f.group_label, f.label, f.id),
    )

    def _lane_families(lane: str) -> List[Any]:
        if lane == "OEM":
            chosen = [f for f in families if f.group_id in {"powertrain", "chassis", "electrical"}]
        elif lane == "OES":
            chosen = [f for f in families if f.group_id in {"powertrain", "maintenance", "chassis", "electrical"}]
        else:
            chosen = [f for f in families if f.group_id in {"maintenance", "chassis", "electrical", "body", "powertrain"}]

        if not chosen:
            chosen = families
        return chosen[:max(1, int(per_lane_families))]

    lanes = [
        ("OEM", "oem_only"),
        ("OES", "oes_compatible"),
        ("Aftermarket", "aftermarket_compatible"),
    ]

    tier_weight = {
        "tier1_dominant": 100,
        "tier2_mid_volume": 70,
        "tier3_long_tail": 40,
    }
    lane_weight = {
        "OEM": 30,
        "OES": 20,
        "Aftermarket": 10,
    }

    top_models = [x.get("model") for x in (reports.get("model_frequency_top_200") or [])[:5] if x.get("model")]
    top_years = [x.get("year") for x in (reports.get("year_distribution") or []) if x.get("year")]
    if len(top_years) > 8:
        top_years = top_years[-8:]

    out: List[Dict[str, Any]] = []
    import_id = 0

    for q in sorted(queue, key=lambda x: int(x.get("queue_id") or 0)):
        manufacturer = q.get("manufacturer")
        tier = q.get("tier")
        queue_id = int(q.get("queue_id") or 0)
        est_records = int(q.get("estimated_records") or 0)

        for lane_name, lane_key in lanes:
            fams = _lane_families(lane_name)
            for pos, fam in enumerate(fams, start=1):
                import_id += 1
                score = int(tier_weight.get(tier, 0) + lane_weight.get(lane_name, 0) + max(0, (len(fams) - pos + 1)))
                out.append(
                    {
                        "import_id": import_id,
                        "queue_id": queue_id,
                        "tier": tier,
                        "lane": lane_key,
                        "manufacturer": manufacturer,
                        "canonical_key": q.get("canonical_key"),
                        "part_classification": lane_name,
                        "part_family_id": fam.id,
                        "part_family_label": fam.label,
                        "part_family_group": fam.group_label,
                        "estimated_vehicle_records": est_records,
                        "priority_score": score,
                        "status": "pending",
                        "staging_only": True,
                        "suggested_model_focus": top_models,
                        "suggested_year_focus": top_years,
                    }
                )

    return out


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run(max_records: int | None = None, limit: int = 1000, resource_ids: List[str] | None = None) -> Dict[str, Any]:
    started = datetime.now(timezone.utc).isoformat()
    resource_ids = resource_ids or RESOURCE_IDS

    slices: List[DatasetSlice] = []
    source_profiles: List[Dict[str, Any]] = []
    for rid in resource_ids:
        ds = fetch_dataset(resource_id=rid, limit=limit, max_records=max_records)
        slices.append(ds)
        source_profiles.append(
            {
                "resource_id": rid,
                "source_total_records": ds.total,
                "fetched_records": len(ds.records),
                "field_count": len(ds.fields),
            }
        )

    ds_merged = _merge_slices(slices)
    reports = build_reports(ds_merged)
    priority = build_priority_tiers(reports["canonical_manufacturer_registry"])
    queue = build_queue(priority)
    high_priority_parts = build_high_priority_parts_import_list(priority, queue, reports)

    write_json(
        DATA_DIR / "rex_transport_dataset_profile.json",
        {
            "generated_at": started,
            "resource_ids": resource_ids,
            "source_api": API_URL,
            "source_profiles": source_profiles,
            "summary": reports["summary"],
            "quality_issues": reports["quality_issues"],
        },
    )
    write_json(DATA_DIR / "rex_transport_manufacturer_frequency.json", reports["manufacturer_frequency"])
    write_json(
        DATA_DIR / "rex_transport_normalization_report.json",
        {
            "generated_at": started,
            "resource_ids": resource_ids,
            "normalization_rules": reports["normalization_rules"],
            "alias_groups": reports["alias_groups"],
            "quality_issues": reports["quality_issues"],
        },
    )
    write_json(DATA_DIR / "rex_transport_canonical_manufacturer_registry.json", reports["canonical_manufacturer_registry"])
    write_json(DATA_DIR / "rex_transport_priority_tiers.json", priority)
    write_json(DATA_DIR / "rex_transport_import_queue.json", queue)
    write_json(
        DATA_DIR / "rex_transport_high_priority_parts_import_list.json",
        {
            "generated_at": started,
            "resource_ids": resource_ids,
            "total_items": len(high_priority_parts),
            "items": high_priority_parts,
        },
    )

    checkpoint = {
        "generated_at": started,
        "resource_ids": resource_ids,
        "next_queue_id": 1,
        "last_completed_queue_id": 0,
        "status": "initialized",
    }
    write_json(DATA_DIR / "rex_transport_import_checkpoint.json", checkpoint)

    return {
        "generated_at": started,
        "resource_count": len(resource_ids),
        "resource_ids": resource_ids,
        "records_fetched": len(ds_merged.records),
        "source_total": ds_merged.total,
        "distinct_manufacturers_raw": reports["summary"]["distinct_manufacturers_raw"],
        "distinct_manufacturers_canonical": reports["summary"]["distinct_manufacturers_canonical"],
        "tier1_count": len(priority["tiers"]["tier1_dominant"]),
        "tier2_count": len(priority["tiers"]["tier2_mid_volume"]),
        "tier3_count": len(priority["tiers"]["tier3_long_tail"]),
        "artifacts": [
            str(DATA_DIR / "rex_transport_dataset_profile.json"),
            str(DATA_DIR / "rex_transport_manufacturer_frequency.json"),
            str(DATA_DIR / "rex_transport_normalization_report.json"),
            str(DATA_DIR / "rex_transport_canonical_manufacturer_registry.json"),
            str(DATA_DIR / "rex_transport_priority_tiers.json"),
            str(DATA_DIR / "rex_transport_import_queue.json"),
            str(DATA_DIR / "rex_transport_high_priority_parts_import_list.json"),
            str(DATA_DIR / "rex_transport_import_checkpoint.json"),
        ],
    }



def sync_market_priority_to_db(
    priority_tiers_path: Path | None = None,
    db_url: str | None = None,
) -> Dict[str, Any]:
    """
    Read the priority tiers JSON produced by run() and write il_market_priority
    values into car_brands.  Brands not found in the DB are skipped (not created).

    Priority = 1-based rank within Tier1, then Tier2, then Tier3.
    This is the bridge between the annual market-analysis run and Rex/DB-agent
    configuration — Rex and the DB agent read il_market_priority from the DB to
    decide what to work on first.

    Returns a summary dict suitable for system_logs.
    """
    import os as _os

    tiers_path = priority_tiers_path or DATA_DIR / "rex_transport_priority_tiers.json"
    if not tiers_path.exists():
        return {"status": "error", "reason": "priority_tiers file missing — run pipeline first"}

    tiers = json.loads(tiers_path.read_text(encoding="utf-8"))

    # Build ranked list: Tier1 first, then Tier2, then Tier3
    ranked: List[Dict[str, Any]] = []
    for tier_name in ["tier1_dominant", "tier2_mid_volume", "tier3_long_tail"]:
        ranked.extend(tiers.get("tiers", {}).get(tier_name, []))

    if not ranked:
        return {"status": "error", "reason": "no ranked manufacturers found in tiers file"}

    # Hebrew canonical name → rank (1-based)
    he_to_rank: Dict[str, int] = {
        item["canonical_name"]: idx + 1
        for idx, item in enumerate(ranked)
    }
    # Also index aliases
    for idx, item in enumerate(ranked):
        for alias in item.get("aliases") or []:
            if alias and alias not in he_to_rank:
                he_to_rank[alias] = idx + 1

    # Connect to catalog DB
    url = db_url or _os.getenv(
        "DATABASE_URL",
        "postgresql://autospare:e4b79d75ca640dbe7f259618f078b82f21573e419308f668beed5e20b26b1d43@postgres_catalog/autospare",
    )
    # Strip asyncpg driver prefix so psycopg2 can parse the URL
    url = url.replace("postgresql+asyncpg://", "postgresql://")

    try:
        import psycopg2
    except ImportError:
        return {"status": "error", "reason": "psycopg2 not available"}

    conn = psycopg2.connect(url)
    conn.autocommit = False
    cur = conn.cursor()

    # Fetch all active car_brands with their aliases
    cur.execute("SELECT id, name, aliases FROM car_brands WHERE is_active = TRUE")
    rows = cur.fetchall()

    updated = 0
    skipped = 0
    not_in_transport = 0

    for brand_id, en_name, aliases_arr in rows:
        aliases_arr = aliases_arr or []
        # Try to find a rank via any Hebrew alias that matches transport data
        rank = None
        for alias in aliases_arr:
            alias_str = str(alias or "").strip()
            if alias_str in he_to_rank:
                rank = he_to_rank[alias_str]
                break
            # Also try the norm_key match
            nk = _norm_key(alias_str)
            for he, r in he_to_rank.items():
                if _norm_key(he) == nk:
                    rank = r
                    break
            if rank:
                break

        if rank is None:
            not_in_transport += 1
            skipped += 1
            continue

        cur.execute(
            "UPDATE car_brands SET il_market_priority = %s, updated_at = NOW() WHERE id = %s",
            (rank, brand_id),
        )
        updated += 1

    conn.commit()
    cur.close()
    conn.close()

    result = {
        "status": "ok",
        "total_transport_brands": len(ranked),
        "car_brands_updated": updated,
        "car_brands_skipped_no_match": skipped,
        "car_brands_not_in_transport": not_in_transport,
        "top10_priority": [
            {"rank": idx + 1, "canonical_he": item["canonical_name"], "fleet_share_pct": item["fleet_share_pct"]}
            for idx, item in enumerate(ranked[:10])
        ],
    }
    print(
        f"[TransportPipeline] sync_market_priority_to_db: "
        f"updated={updated} skipped={skipped} not_in_transport={not_in_transport}"
    )
    return result

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="REX Transport Office dataset analysis + manufacturer prioritization pipeline")
    p.add_argument("--max-records", type=int, default=None, help="Optional cap for quicker dry-runs (per resource)")
    p.add_argument("--page-limit", type=int, default=500, help="API page size")
    p.add_argument("--resource-ids", type=str, default="", help="Optional comma-separated resource IDs")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    resource_ids = [x.strip() for x in str(args.resource_ids or "").split(",") if x.strip()]
    report = run(
        max_records=args.max_records,
        limit=max(1, int(args.page_limit)),
        resource_ids=resource_ids or None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
