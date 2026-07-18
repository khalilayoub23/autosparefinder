"""
ai_catalog_builder.py
---------------------
Uses Hugging Face Inference API to generate real OEM parts catalog data.

Modes:
  --new      Add completely new brands (BYD, MG, Tesla, etc.)
  --expand   Expand thin brands (< TARGET parts) to TARGET parts each
  --all      Both new + expand (default when no flags given)

  python3 ai_catalog_builder.py                    # new + expand all
  python3 ai_catalog_builder.py --new              # only add missing brands
  python3 ai_catalog_builder.py --expand           # only expand thin brands
  python3 ai_catalog_builder.py Toyota BMW --expand
  python3 ai_catalog_builder.py --dry-run
"""
import asyncio, sys, uuid, json, os, hashlib, re, logging
from datetime import datetime
from typing import Any, Dict
import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv
from hf_client import hf_text, hf_router_text, groq_text
from categories import CATEGORY_MAP

load_dotenv()

logger = logging.getLogger(__name__)

_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")

SUPPLIER_NAME = "AutoParts Pro IL"
ILS_TO_USD   = 1 / 3.65

# Target minimum parts per brand after expansion
EXPAND_TARGET = 100

# Brands completely missing from DB — add from scratch
NEW_BRANDS = [
    "BYD", "MG", "Haval", "Chery", "Omoda", "BAIC", "GAC", "Tesla",
]

# Brands currently in DB but with < EXPAND_TARGET parts — expand them
THIN_BRANDS = [
    "Toyota", "BMW", "Volkswagen", "Ford", "Kia", "Nissan", "Honda",
    "Subaru", "Skoda", "Mazda", "Alfa Romeo", "Dodge", "GMC", "RAM",
    "Buick", "Cadillac", "Cupra", "Vauxhall", "Land Rover", "Lancia",
    "Daihatsu", "Jaguar", "Lexus", "Audi", "Dacia", "Acura", "Mini",
    "Seat", "Bentley", "Rolls-Royce", "Lamborghini", "Maserati",
    "Infiniti", "Tata Motors", "Lincoln", "Lynk & Co", "Geely",
    "Fiat", "Isuzu", "Jeep", "Volvo",
]

# Keep for backward compat
MISSING_BRANDS = NEW_BRANDS

# Shared category list from the single source of truth.
CATEGORIES = list(CATEGORY_MAP.keys())
DEFAULT_CATEGORY = "כלי עבודה ואביזרים"

CATALOG_UPSERT = """
INSERT INTO parts_catalog
    (id, sku, name, category, manufacturer, part_type,
     description, specifications,
     base_price, is_active, created_at, updated_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9,true,NOW(),NOW())
ON CONFLICT (sku) DO UPDATE SET
    name=EXCLUDED.name, category=EXCLUDED.category,
    manufacturer=EXCLUDED.manufacturer, part_type=EXCLUDED.part_type,
    description=EXCLUDED.description, base_price=EXCLUDED.base_price,
    is_active=true, updated_at=NOW()
"""

SP_INSERT = """
INSERT INTO supplier_parts
    (id,supplier_id,part_id,supplier_sku,price_ils,price_usd,
     is_available,availability,warranty_months,estimated_delivery_days,
     stock_quantity,min_order_qty,last_checked_at,created_at)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$13)
ON CONFLICT DO NOTHING
"""

def make_sku(brand: str, catalog_num: str, idx: int) -> str:
    prefix = re.sub(r'[^A-Z]', '', brand.upper())[:4].ljust(4, 'X')
    cat = str(catalog_num).replace(' ', '').replace('/', '-').strip()
    raw = f"{prefix}-{cat}"
    if len(raw) > 100:
        raw = f"{prefix}-{hashlib.md5(cat.encode()).hexdigest()[:12]}"
    return raw[:100]


SYSTEM_PROMPT = """You are an expert automotive parts specialist for the Israeli market.
You know OEM part numbers, Hebrew part names, and Israeli market prices.
Always respond with ONLY valid JSON — no markdown, no explanation."""

def build_new_prompt(brand: str) -> str:
    cat_list = ", ".join(CATEGORIES)
    return f"""Generate a list of exactly 50 common OEM/Original replacement parts for {brand} vehicles
that are typically sold in Israel.

For each part provide:
- "name_he": Hebrew name (e.g., "מסנן שמן", "רפידות בלם קדמי")
- "name_en": English name (e.g., "Oil Filter", "Front Brake Pads")
- "catalog_num": Real OEM catalog/part number for {brand}
- "category": ONE of: {cat_list}
- "price_ils": Realistic Israeli market price in ILS (integer, 30–8000)
- "in_stock": true for commonly stocked parts, false for rare/special order

Rules:
- Use REAL {brand} OEM part numbers
- Cover a variety of categories (engine, brakes, suspension, electrical, etc.)
- Prices should reflect real Israeli importer prices
- Mix of in_stock (~70%) and on_order (~30%)

Respond ONLY with a JSON array of 50 objects. No other text."""


def build_expand_prompt(brand: str, existing_catalogs: list, need: int) -> str:
    cat_list = ", ".join(CATEGORIES)
    existing_str = ", ".join(existing_catalogs[:30]) if existing_catalogs else "none yet"
    return f"""Generate {need} MORE OEM/Original replacement parts for {brand} vehicles sold in Israel.

IMPORTANT: These catalog numbers already exist — DO NOT repeat them: {existing_str}

For each part provide:
- "name_he": Hebrew name
- "name_en": English name
- "catalog_num": Real OEM part number for {brand} (different from existing ones above)
- "category": ONE of: {cat_list}
- "price_ils": Realistic Israeli price in ILS (30–8000)
- "in_stock": true/false

Cover different parts than already listed — focus on less common but important parts
(suspension, electrical, cooling, transmission, body parts, etc.)

Respond ONLY with a JSON array of {need} objects. No other text."""


def _extract_json_list(raw: str) -> list:
    """Extract a JSON array from a response that may be wrapped in markdown fences."""
    # Strip ```json ... ``` or ``` ... ``` fences
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'```\s*$', '', raw)
    raw = raw.strip()
    data = json.loads(raw)
    if isinstance(data, list):
        return data
    # find first list value in dict wrapper
    for v in data.values():
        if isinstance(v, list):
            return v
    return []


async def ask_gpt4o(brand: str, prompt: str) -> list:
    """Call GPT-4o with the given prompt. Returns list of part dicts.
    Handles 429 rate-limit by waiting and retrying automatically."""
    for attempt in range(5):
        try:
            raw = await hf_text(prompt, system=SYSTEM_PROMPT, timeout=30.0)
            parts = _extract_json_list(raw)
            if parts:
                return parts
            print(f"  [WARN] attempt {attempt+1}: empty list, retrying...")
            await asyncio.sleep(5)
        except json.JSONDecodeError as e:
            print(f"  [WARN] attempt {attempt+1}: JSON parse error ({e}), retrying...")
            await asyncio.sleep(5)
        except Exception as e:
            err = str(e)
            if "429" in err or "RateLimit" in err:
                wait = 65  # wait 65s to clear the 60s window
                print(f"  [RATE LIMIT] waiting {wait}s before retry (attempt {attempt+1}/5)...")
                await asyncio.sleep(wait)
            else:
                print(f"  [GPT ERROR] {brand}: {e}")
                return []
    return []


async def insert_parts(conn, supplier_id, brand: str, parts: list,
                       existing_skus: set, dry_run: bool, now: datetime):
    """Insert parts into parts_catalog + supplier_parts. Returns (cat_ins, sp_ins, errors)."""
    cat_ins = sp_ins = errors = 0
    for i, part in enumerate(parts):
        try:
            name_he  = str(part.get("name_he") or part.get("name") or "חלק חלוף").strip()
            name_en  = str(part.get("name_en", "")).strip()
            cat_num  = str(part.get("catalog_num") or part.get("part_number") or f"R{i+1}").strip()
            category = str(part.get("category") or DEFAULT_CATEGORY).strip()
            price    = float(part.get("price_ils") or 100)
            in_stock = bool(part.get("in_stock", True))
            if category not in CATEGORIES:
                category = DEFAULT_CATEGORY
            sku = make_sku(brand, cat_num, i)
            if sku in existing_skus:
                continue
            existing_skus.add(sku)
            name_full = f"{name_he} - {name_en}" if name_en else name_he
            if dry_run:
                cat_ins += 1; sp_ins += 1; continue
            part_id = uuid.uuid4()
            await conn.execute(CATALOG_UPSERT,
                part_id, sku, name_full[:255], category, brand, "OEM",
                name_full[:500],
                json.dumps({"catalog_num": cat_num, "source": "ai_gpt4o"}, ensure_ascii=False),
                round(price, 2),
            )
            cat_ins += 1
            actual = await conn.fetchrow("SELECT id FROM parts_catalog WHERE sku=$1", sku)
            actual_id = actual['id']
            price_ils = round(price, 2)
            price_usd = round(price * ILS_TO_USD, 2)
            av_code   = "in_stock" if in_stock else "on_order"
            await conn.execute(SP_INSERT,
                uuid.uuid4(), supplier_id, actual_id, cat_num,
                price_ils, price_usd, in_stock, av_code,
                12, 7 if in_stock else 21, 10 if in_stock else 0, 1, now)
            sp_ins += 1
        except Exception as e:
            errors += 1
            print(f"    [ERR] {e}")
    return cat_ins, sp_ins, errors


async def enrich_pending_parts(db: AsyncSession, limit: int = 200) -> Dict[str, Any]:
    """
    Callable enrichment task for db_update_agent / pipeline use.

    Finds up to `limit` parts_catalog rows where:
        needs_oem_lookup = FALSE AND master_enriched = FALSE
    Strategy:
      - Fast path: parts with name_he + deterministic quality → no AI, process instantly
      - AI path: batched 10 per HF call → 0.5s between batches (120 RPM, safe for HF PRO)

    Returns {"task": "enrich_pending_parts", "status": "ok",
             "processed": int, "inserted_master": int,
             "inserted_variants": int, "errors": int}
    """
    from sqlalchemy import text as _text

    report: Dict[str, Any] = {
        "task": "enrich_pending_parts",
        "status": "ok",
        "processed": 0,
        "inserted_master": 0,
        "inserted_variants": 0,
        "errors": 0,
    }

    if not os.getenv("HF_TOKEN", ""):
        report["status"] = "skipped"
        report["reason"] = "HF_TOKEN not set"
        return report

    rows = (await db.execute(
        _text("""
            SELECT id, sku, name, name_he, category, part_type, manufacturer
            FROM parts_catalog
            WHERE needs_oem_lookup = FALSE
              AND master_enriched  = FALSE
              AND is_active        = TRUE
            ORDER BY created_at ASC
            LIMIT :lim
        """),
        {"lim": limit},
    )).fetchall()

    QUALITY_MAP = {
        "oem":                  "OEM",
        "original":             "OEM",
        "oem_equivalent":       "OEM_Equivalent",
        "aftermarket_premium":  "Aftermarket_Premium",
        "aftermarket_standard": "Aftermarket_Standard",
        "aftermarket":          "Aftermarket_Standard",
        "economy":              "Economy",
    }
    VALID_QUALITY = set(QUALITY_MAP.values())

    _OEM_BRANDS = {
        "acura", "honda", "toyota", "lexus", "nissan", "infiniti", "mazda",
        "subaru", "mitsubishi", "kia", "hyundai", "volkswagen", "audi",
        "bmw", "mercedes", "ford", "chevrolet", "gm", "volvo", "jaguar",
        "land rover", "porsche", "skoda", "seat", "cupra",
    }

    def _infer_quality(row) -> str | None:
        pt = (row.part_type or "").lower()
        if pt in ("oem", "original"):
            return "OEM"
        if (row.manufacturer or "").lower() in _OEM_BRANDS:
            return "OEM_Equivalent"
        return None

    async def _upsert_one(row, canonical_name: str, canonical_name_he: str, quality_level: str):
        try:
            master_row = (await db.execute(
                _text("""
                    INSERT INTO parts_master
                        (id, canonical_name, canonical_name_he,
                         category, part_type, is_safety_critical,
                         created_at, updated_at)
                    VALUES
                        (gen_random_uuid(), :cname, :cname_he,
                         :category, :part_type, false, NOW(), NOW())
                    ON CONFLICT (canonical_name, category) DO UPDATE
                        SET updated_at = NOW()
                    RETURNING id
                """),
                {
                    "cname":     canonical_name,
                    "cname_he":  canonical_name_he,
                    "category":  row.category or "כללי",
                    "part_type": row.part_type or "Aftermarket",
                },
            )).fetchone()

            if master_row:
                report["inserted_master"] += 1
                await db.execute(
                    _text("""
                        INSERT INTO part_variants
                            (id, master_part_id, catalog_part_id,
                             quality_level, manufacturer, sku, created_at)
                        VALUES
                            (gen_random_uuid(), :mid, :cid,
                             :ql, :mfr, :sku, NOW())
                        ON CONFLICT (master_part_id, catalog_part_id) DO NOTHING
                    """),
                    {
                        "mid": str(master_row[0]),
                        "cid": str(row.id),
                        "ql":  quality_level,
                        "mfr": row.manufacturer or "",
                        "sku": row.sku,
                    },
                )
                report["inserted_variants"] += 1

            await db.execute(
                _text("UPDATE parts_catalog SET master_enriched = TRUE WHERE id = :id"),
                {"id": str(row.id)},
            )
            await db.commit()
            report["processed"] += 1
        except Exception:
            report["errors"] += 1
            try:
                await db.rollback()
            except Exception:
                pass

    def _build_batch_prompt(batch: list) -> str:
        items = "\n".join(
            f'{i+1}. name="{r.name}", name_he="{r.name_he or ""}", '
            f'type="{r.part_type}", brand="{r.manufacturer}", category="{r.category}"'
            for i, r in enumerate(batch)
        )
        return (
            f"For each of these {len(batch)} auto parts, return a JSON array of objects "
            f'with keys: "canonical_name" (English, 2-6 words), '
            f'"canonical_name_he" (Hebrew, 2-6 words), '
            f'"quality_level" (one of: OEM, OEM_Equivalent, Aftermarket_Premium, '
            f'Aftermarket_Standard, Economy).\n\n'
            f"{items}\n\n"
            f"Return ONLY a valid JSON array of {len(batch)} objects in the same order."
        )

    def _parse_batch_response(batch: list, raw: str) -> list:
        try:
            s, e = raw.find("["), raw.rfind("]") + 1
            items_data = json.loads(raw[s:e]) if s >= 0 and e > s else []
        except Exception:
            items_data = []
        results = []
        for i, row in enumerate(batch):
            d = items_data[i] if i < len(items_data) else {} if isinstance(items_data, list) else {}
            cname    = str(d.get("canonical_name",    row.name or ""))[:255]
            cname_he = str(d.get("canonical_name_he", row.name_he or ""))[:255]
            raw_ql   = str(d.get("quality_level", "Aftermarket_Standard")).lower()
            ql       = QUALITY_MAP.get(raw_ql, "Aftermarket_Standard")
            if ql not in VALID_QUALITY:
                ql = "Aftermarket_Standard"
            results.append((row, cname, cname_he, ql))
        return results

    _ai_sem = asyncio.Semaphore(2)  # limit concurrent Groq calls to stay under rate limits

    async def _enrich_ai_batch(batch: list, use_hf_router: bool) -> list:
        """Groq primary (fast) → HF Router → Cerebras fallback chain."""
        prompt = _build_batch_prompt(batch)
        async with _ai_sem:
            for provider_fn, kwargs in [
                (groq_text,      {"model": "llama-3.1-8b-instant", "timeout": 30.0}),
                (hf_router_text, {"timeout": 60.0}),
                (hf_text,        {"timeout": 60.0}),
            ]:
                try:
                    raw = (await provider_fn(prompt, system=SYSTEM_PROMPT, **kwargs)).strip()
                    return _parse_batch_response(batch, raw)
                except Exception:
                    continue
            return [
                (row, (row.name or "")[:255], (row.name_he or "")[:255], "Aftermarket_Standard")
                for row in batch
            ]

    # ── Split into fast-path (no AI) and AI-needed ────────────────────────────
    fast_rows = []
    ai_rows   = []
    for row in rows:
        has_he      = bool((row.name_he or "").strip())
        inferred_ql = _infer_quality(row)
        if has_he and inferred_ql:
            fast_rows.append((row, (row.name or "").strip()[:255],
                              (row.name_he or "").strip()[:255], inferred_ql))
        else:
            ai_rows.append(row)

    # Fast path — no AI, instant
    for row, cname, cname_he, ql in fast_rows:
        await _upsert_one(row, cname, cname_he, ql)

    # AI path — batches of 30, Groq primary + HF/Cerebras fallback, 2 concurrent
    AI_BATCH = 30
    batches = [ai_rows[i:i + AI_BATCH] for i in range(0, len(ai_rows), AI_BATCH)]
    tasks   = [_enrich_ai_batch(b, i % 2 == 0) for i, b in enumerate(batches)]
    all_results = await asyncio.gather(*tasks)
    for batch_results in all_results:
        for row, cname, cname_he, ql in batch_results:
            await _upsert_one(row, cname, cname_he, ql)

    return report


async def run(mode_new=True, mode_expand=True, specific_brands=None, dry_run=False):
    if not os.getenv("HF_TOKEN", ""):
        print("ERROR: HF_TOKEN not set in .env"); return

    print(f"{'[DRY RUN] ' if dry_run else ''}Connecting to DB and Hugging Face Inference API...")
    conn   = await asyncpg.connect(DB_URL)

    supplier = await conn.fetchrow("SELECT id FROM suppliers WHERE name=$1", SUPPLIER_NAME)
    if not supplier:
        print(f"ERROR: Supplier '{SUPPLIER_NAME}' not found!"); await conn.close(); return
    supplier_id = supplier['id']
    print(f"Supplier: {SUPPLIER_NAME} ({supplier_id})\n")

    now = datetime.utcnow()
    total_cat = total_sp = 0

    # ── Phase 1: Add completely new brands ────────────────────────────────────
    if mode_new:
        new_targets = specific_brands if specific_brands else NEW_BRANDS
        print("=" * 60)
        print(f"PHASE 1: Adding {len(new_targets)} new brands (50 parts each)")
        print("=" * 60)
        for brand in new_targets:
            # Check if already in DB
            existing = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", brand)
            if existing > 0 and not specific_brands:
                print(f"  [{brand}] already has {existing} parts, skipping (use --expand to grow)")
                continue

            # Delete any stub entries first
            if not dry_run and existing > 0:
                await conn.execute("""
                    DELETE FROM supplier_parts WHERE part_id IN
                    (SELECT id FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1))""", brand)
                await conn.execute(
                    "DELETE FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", brand)

            print(f"\n[{brand}] Asking GPT-4o for 50 parts...")
            prompt = build_new_prompt(brand)
            parts  = await ask_gpt4o(brand, prompt)
            if not parts:
                print(f"  ⚠ No parts returned, skipping"); continue
            print(f"  Got {len(parts)} parts")
            if dry_run:
                for p in parts[:2]:
                    print(f"    sample: {p.get('name_he','?')} | {p.get('catalog_num','?')} | {p.get('price_ils','?')}₪")
                total_cat += len(parts); continue
            c, s, e = await insert_parts(conn, supplier_id, brand, parts, set(), dry_run, now)
            print(f"  ✅ catalog={c}, supplier_parts={s}, errors={e}")
            total_cat += c; total_sp += s
            await asyncio.sleep(7)  # stay under 10 req/min

    # ── Phase 2: Expand thin brands ───────────────────────────────────────────
    if mode_expand:
        expand_targets = specific_brands if specific_brands else THIN_BRANDS
        print()
        print("=" * 60)
        print(f"PHASE 2: Expanding {len(expand_targets)} thin brands to {EXPAND_TARGET}+ parts")
        print("=" * 60)
        for brand in expand_targets:
            current = await conn.fetchval(
                "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", brand)
            if current >= EXPAND_TARGET:
                print(f"  [{brand}] already has {current} parts ✓ skip")
                continue

            need = EXPAND_TARGET - current
            # Get existing catalog numbers to avoid duplication
            existing_rows = await conn.fetch(
                """SELECT sku, specifications FROM parts_catalog
                   WHERE LOWER(manufacturer)=LOWER($1)""", brand)
            existing_skus = {r['sku'] for r in existing_rows}
            existing_cats = []
            for r in existing_rows:
                try:
                    spec = json.loads(r['specifications'] or '{}')
                    c = spec.get('catalog_num')
                    if c: existing_cats.append(c)
                except: pass

            print(f"\n[{brand}] has {current} parts → need {need} more (target={EXPAND_TARGET})")
            prompt = build_expand_prompt(brand, existing_cats, need)
            parts  = await ask_gpt4o(brand, prompt)
            if not parts:
                print(f"  ⚠ No parts returned, skipping"); continue
            print(f"  Got {len(parts)} new parts from GPT-4o")
            if dry_run:
                for p in parts[:2]:
                    print(f"    sample: {p.get('name_he','?')} | {p.get('catalog_num','?')} | {p.get('price_ils','?')}₪")
                total_cat += len(parts); continue
            c, s, e = await insert_parts(conn, supplier_id, brand, parts, existing_skus, dry_run, now)
            print(f"  ✅ added catalog={c}, supplier_parts={s}, errors={e} → total now ~{current+c}")
            total_cat += c; total_sp += s
            await asyncio.sleep(7)  # stay under 10 req/min

    await conn.close()

    print()
    print("=" * 60)
    if dry_run:
        print(f"[DRY RUN] Would insert ~{total_cat} catalog + supplier_parts entries")
    else:
        print(f"✅ Done!  catalog={total_cat:,}  supplier_parts={total_sp:,}")
        print("  (HF text model via Hugging Face Inference API)")
    print("=" * 60)


async def lookup_oem_spec(db: AsyncSession, limit: int = 100, max_seconds: float = 1500.0) -> Dict[str, Any]:
    """
    Uses an LLM to find OEM numbers for inactive parts with needs_oem_lookup=TRUE.
    After finding OEM: clears needs_oem_lookup so enrich_pending_parts can process them.

    Soft time-budget (added 2026-07-11): each row is a rate-limited LLM call, so a
    large `limit` used to blow past the run_all_tasks 60-min hard timeout and get
    KILLED (status=error, 8×/day). Now it stops after `max_seconds` and returns a
    clean partial result (status=ok, stopped_early=True); the remaining rows are
    picked up next cycle. The task therefore never hits the hard timeout.
    """
    import time as _time
    from sqlalchemy import text as _text

    _t0 = _time.monotonic()
    report: Dict[str, Any] = {
        "task": "lookup_oem_spec",
        "status": "ok",
        "scanned": 0,
        "oem_found": 0,
        "oem_not_found": 0,
        "stopped_early": False,
        "errors": [],
    }

    rows = (await db.execute(
        _text("""
            SELECT id, sku, name, name_he, category, part_type, manufacturer
            FROM parts_catalog
            WHERE is_active = FALSE
              AND needs_oem_lookup = TRUE
              AND name IS NOT NULL
              AND name != ''
            ORDER BY RANDOM()
            LIMIT :lim
        """),
        {"lim": limit},
    )).fetchall()
    report["scanned"] = len(rows)

    if not rows:
        logger.info("[lookup_oem_spec] No parts to process")
        return report

    for row in rows:
        # Stop before the run_all_tasks hard timeout; finish the rest next cycle.
        if _time.monotonic() - _t0 > max_seconds:
            report["stopped_early"] = True
            logger.info("[lookup_oem_spec] soft budget %.0fs reached — stopping early after %d rows",
                        max_seconds, report["oem_found"] + report["oem_not_found"])
            break
        try:
            prompt = (
                f'You are an automotive parts specialist. '
                f'Given this auto part, return the most likely OEM part number.\n\n'
                f'Part name (Hebrew): "{row.name}"\n'
                f'English name hint: "{row.name_he or ""}"\n'
                f'Vehicle manufacturer: "{row.manufacturer}"\n'
                f'Category: "{row.category or "unknown"}"\n'
                f'Part type: "{row.part_type or "unknown"}"\n\n'
                f'Return ONLY valid JSON, no explanation:\n'
                f'{{"oem_number": "EXACT_OEM_NUMBER_OR_NULL", '
                f'"confidence": "high|medium|low", '
                f'"canonical_name_en": "2-5 word English name"}}'
            )

            raw = await hf_text(prompt, system=SYSTEM_PROMPT, timeout=30.0)
            if not raw:
                report["oem_not_found"] += 1
                continue

            clean = raw.strip()
            clean = re.sub(r'^```(?:json)?\s*', '', clean)
            clean = re.sub(r'```\s*$', '', clean)
            s, e = clean.find("{"), clean.rfind("}") + 1
            data = json.loads(clean[s:e]) if s >= 0 and e > s else {}

            oem = str(data.get("oem_number") or "").strip()
            confidence = str(data.get("confidence", "low")).lower()
            name_en = str(data.get("canonical_name_en") or "").strip()

            if oem and oem.upper() != "NULL" and confidence in ("high", "medium"):
                await db.execute(_text("""
                    UPDATE parts_catalog
                    SET oem_number        = :oem,
                        needs_oem_lookup  = FALSE,
                        is_active         = TRUE,
                        name_he           = COALESCE(NULLIF(name_he, ''), :name_en),
                        updated_at        = NOW()
                    WHERE id = :part_id
                """), {
                    "oem": oem,
                    "name_en": name_en,
                    "part_id": str(row.id),
                })
                await db.commit()
                report["oem_found"] += 1
                logger.info(
                    "[lookup_oem_spec] %s -> OEM %s (conf=%s)",
                    row.name, oem, confidence
                )
            else:
                report["oem_not_found"] += 1

        except Exception as exc:
            report["errors"].append(f"{row.sku}: {exc}")
            logger.error("[lookup_oem_spec] Error on %s: %s", row.sku, exc)
            try:
                await db.rollback()
            except Exception:
                pass

    logger.info("[lookup_oem_spec] Done: %s", report)
    return report


if __name__ == "__main__":
    flags   = {a for a in sys.argv[1:] if a.startswith("--")}
    brands  = [a for a in sys.argv[1:] if not a.startswith("--")] or None
    dry     = "--dry-run" in flags
    do_new  = "--expand" not in flags   # default: do new unless --expand only
    do_exp  = "--new"    not in flags   # default: do expand unless --new only
    if "--all" in flags:
        do_new = do_exp = True
    asyncio.run(run(mode_new=do_new, mode_expand=do_exp,
                    specific_brands=brands, dry_run=dry))
