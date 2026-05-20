"""
supplier_pdf_import.py  -  PDF-first supplier catalog import pipeline

Pipeline per manufacturer:
  1. Parse PDF -> extract rows (OEM, name, price, availability, warranty)
  2. Read DB rows for same manufacturer
  3. Compare: new parts / price changes / name fixes / availability changes
  4. Import: upsert to parts_catalog (INSERT new, UPDATE stale)
  5. Fix corrupt rows (Hebrew-text OEM, junk keys) -> mark inactive + queue to AI agent
  6. Trigger Meilisearch scoped sync
  7. Count verification: PDF count vs DB active count

Usage:
  python supplier_pdf_import.py --pdf /path/to/ORA.pdf --manufacturer ORA [--apply]
  python supplier_pdf_import.py --pdf /path/to/ORA.pdf --manufacturer ORA --dry-run

Notes:
  - Price in PDF is assumed to be ILS incl. 18% VAT (base_price field).
  - availability column: zamin (available) / lo zamin (not available).
  - If no availability column found, all rows are treated as active.
  - Script is idempotent: safe to re-run; uses ON CONFLICT upsert.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import subprocess

# Module-level regex constants -- defined once, never inline
_OEM_PAT   = re.compile(r"\b([A-Z0-9][A-Z0-9/\-\.]{3,25})\b", re.ASCII)
_PRICE_PAT = re.compile(r"\b(\d{1,5}(?:[,\.]\d{3})*(?:[,\.]\d{1,2}))\b", re.ASCII)
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import asyncpg
import pdfplumber
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from categories import guess_category_by_text

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger("supplier_pdf_import")

# --- DB connection ---

_raw_url = os.getenv("DATABASE_URL", "")
if not _raw_url:
    raise RuntimeError("DATABASE_URL environment variable is required")
DB_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://").replace("+asyncpg", "")


def _candidate_db_urls(url):
    out = [url]
    parsed = urlparse(url)
    host = parsed.hostname
    if not host or host in {"127.0.0.1", "localhost"}:
        return out
    for alias in ("127.0.0.1", "localhost", "db", "postgres", "postgres_catalog"):
        out.append(urlunparse(parsed._replace(netloc=parsed.netloc.replace(host, alias))))
    seen, dedup = set(), []
    for item in out:
        if item not in seen:
            seen.add(item); dedup.append(item)
    return dedup


async def connect_db():
    last_err = None
    for url in _candidate_db_urls(DB_URL):
        try:
            return await asyncpg.connect(url)
        except Exception as exc:
            last_err = exc
    raise last_err or RuntimeError("No DB connection")

# --- Data structures ---

@dataclass
class PdfRow:
    oem_number: str
    oem_norm: str
    name: str | None = None
    name_he: str | None = None
    price: float | None = None
    available: bool = True
    warranty_years: int | None = None
    warranty_km: int | None = None
    warranty_notes: str | None = None
    raw_row: dict = field(default_factory=dict)


@dataclass
class ImportReport:
    manufacturer: str
    pdf_path: str
    pdf_rows: int = 0
    pdf_valid_keys: int = 0
    db_rows_before: int = 0
    db_active_before: int = 0
    inserted: int = 0
    updated_price: int = 0
    updated_name: int = 0
    deactivated_unavailable: int = 0
    reactivated: int = 0
    junk_deactivated: int = 0
    junk_queued_to_agent: int = 0
    db_active_after: int = 0
    meili_synced: bool = False
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)
    category_counts: dict = field(default_factory=dict)


# --- Normalization helpers ---

def clean(v):
    if v is None: return None
    s = str(v).strip()
    return s if s and s.lower() not in {"nan", "none", "-", "—"} else None


def normalize_key(v):
    s = clean(v)
    if not s: return None
    s = s.upper()
    s = re.sub(r"\s+", "", s)
    if re.match(r"^[A-Z]{2,6}-[0-9A-Z]", s):
        s = re.sub(r"^[A-Z]{2,6}-", "", s)
    if re.fullmatch(r"[0-9]+\.0+", s):
        s = s.split(".", 1)[0]
    if re.fullmatch(r"0+[0-9]+", s):
        s = s.lstrip("0") or s
    return s or None


def _is_hebrew(text):
    return bool(re.search(r"[֐-׿]", text))


def parse_price(v):
    s = clean(v)
    if not s: return None
    s = s.replace("₪", "").replace(",", "").replace(" ", "")
    try:
        num = float(s)
        return round(num, 2) if num > 0 else None
    except (TypeError, ValueError):
        return None


def parse_warranty(text):
    if not text: return None, None, None
    t = text.lower()
    years = km = None
    m = re.search(r"(\d+)\s*שנ", t)
    if m: years = int(m.group(1))
    m = re.search(r"(\d+)\s*year", t)
    if m: years = int(m.group(1))
    m = re.search(r"(\d[\d,]*)\s*k?m", t)
    if m: km = int(m.group(1).replace(",", ""))
    notes = text.strip()[:200]
    return years, km, notes or None


# --- Column auto-detection ---

_AVAIL_T = {"זמין", "available", "availability", "in stock", "stock", "qty", "status"}
_PRICE_T = {"price", "מחיר", "cost", "מכירה", "sell"}
_NAME_T  = {"name", "description", "שם", "תיאור", "part name"}
_OEM_T   = {"catalog", "oem", "part no", "part number", "מספר", "קטלוג", "code", "ref", "sku"}
_WARR_T  = {"warranty", "אחריות", "garanti"}


def detect_columns(headers):
    result = {"oem": None, "name": None, "price": None, "available": None, "warranty": None}
    for field_name, tokens in [("oem",_OEM_T),("name",_NAME_T),("price",_PRICE_T),("available",_AVAIL_T),("warranty",_WARR_T)]:
        best_score, best_idx = 0, None
        for i, h in enumerate(headers):
            if h is None: continue
            score = sum(1 for t in tokens if t in str(h).lower())
            if score > best_score:
                best_score, best_idx = score, i
        if best_score > 0:
            result[field_name] = best_idx
    return result


# --- PDF parsing ---

def _extract_tables(pdf_path):
    import concurrent.futures, threading
    from pdfminer.pdfpage import PDFPage as _PDFPage

    def _page_tables(page_num):
        try:
            # Open a fresh handle per thread to avoid shared state issues
            with pdfplumber.open(pdf_path) as _pdf:
                t = _pdf.pages[page_num].extract_tables()
                return t or []
        except Exception:
            return []

    # Use pdfminer to count pages — safe when pages have None MediaBox
    try:
        with open(pdf_path, "rb") as _f:
            n_pages = sum(1 for _ in _PDFPage.get_pages(_f))
    except Exception:
        n_pages = 0
    if n_pages == 0:
        return []

    log.info("PDF has %d pages — extracting tables in parallel", n_pages)
    tables = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(_page_tables, i): i for i in range(n_pages)}
        for fut in concurrent.futures.as_completed(futures, timeout=240):
            try:
                result = fut.result(timeout=5)
                if result:
                    tables.extend(result)
            except Exception:
                pass
    return tables


def _cells(row):
    return [str(c).strip() if c is not None else "" for c in row]


def parse_pdf(pdf_path, manufacturer):
    log.info("Parsing PDF: %s", pdf_path)
    # For large PDFs (>150 pages), skip slow table extraction and go straight to text
    try:
        from pdfminer.pdfpage import PDFPage as _PDFPage
        with open(pdf_path, 'rb') as _f:
            n_pages = sum(1 for _ in _PDFPage.get_pages(_f))
    except Exception:
        n_pages = 0
    if n_pages > 150:
        log.info("Large PDF (%d pages) — using fast text extraction", n_pages)
        return _parse_text_fallback(pdf_path, manufacturer)
    tables = _extract_tables(pdf_path)
    if not tables:
        log.warning("No tables found — using text fallback")
        return _parse_text_fallback(pdf_path, manufacturer)

    all_rows = []
    col_map = None

    for table in tables:
        if not table or len(table) < 2:
            continue
        if col_map is None:
            for idx, row in enumerate(table[:5]):
                cells = _cells(row)
                cm = detect_columns(cells)
                if cm["oem"] is not None:
                    col_map = cm
                    header_idx = idx
                    log.info("Columns detected at row %d: %s", idx, cm)
                    log.info("Headers: %s", cells)
                    break

        if col_map is None:
            first = _cells(table[0])
            ncols = len(first)
            col_map = {
                "available": 0 if ncols >= 5 else None,
                "price":     1 if ncols >= 5 else (0 if ncols == 4 else None),
                "name":      2 if ncols >= 5 else (1 if ncols >= 4 else None),
                "oem":       ncols - 1,
                "warranty":  None,
            }
            header_idx = 0
            log.info("Fallback column map: %s", col_map)

        for row in table[header_idx + 1:]:
            cells = _cells(row)
            if not any(cells):
                continue

            def get(idx):
                if idx is None or idx >= len(cells): return None
                v = cells[idx]
                return v if v else None

            raw_oem = get(col_map["oem"])
            if not raw_oem:
                continue
            oem_norm = normalize_key(raw_oem)
            if not oem_norm:
                continue

            avail_raw = get(col_map["available"])
            if avail_raw is not None:
                al = avail_raw.lower().strip()
                available = al not in {"\u05dc\u05d0 \u05d6\u05de\u05d9\u05df", "not available", "unavailable", "out", "0", "false", "no"}
            else:
                available = True

            price = parse_price(get(col_map["price"]))

            raw_name = get(col_map["name"])
            name_he = name_en = None
            if raw_name:
                if _is_hebrew(raw_name):
                    name_he = raw_name.strip()
                else:
                    name_en = raw_name.strip()

            wy, wk, wn = parse_warranty(get(col_map["warranty"]))

            all_rows.append(PdfRow(
                oem_number=raw_oem.strip(),
                oem_norm=oem_norm,
                name=name_en or f"{manufacturer.upper()} {oem_norm}",
                name_he=name_he,
                price=price,
                available=available,
                warranty_years=wy,
                warranty_km=wk,
                warranty_notes=wn,
                raw_row=dict(enumerate(cells)),
            ))

    log.info("Parsed %d rows from PDF", len(all_rows))
    return all_rows


def _parse_column_page(elements_with_pos, manufacturer):
    """
    Column-aware parser for PDFs where each LTTextContainer spans a full column.
    elements_with_pos: list of (x0, text) tuples from one page.
    Returns list of PdfRow or [] if structure not recognised.
    """
    oem_re    = re.compile(r"^(?=.*[A-Z])(?=.*[0-9])[A-Z0-9]{4,}(?:[-./][A-Z0-9]+)*$")  # mixed alpha+digit
    price_re  = re.compile(r"^\d{1,7}[,.]?\d{0,3}$")

    # Bucket elements by x position (50px tolerance)
    col_data = {}
    for x0, raw_text in elements_with_pos:
        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
        if not lines:
            continue
        x_bucket = round(x0 / 50) * 50
        col_data.setdefault(x_bucket, []).extend(lines)

    if not col_data:
        return []

    # Score each column
    oem_col_x = price_col_x = avail_col_x = None
    best_oem = best_price = best_avail = 0

    for x_bucket, lines in col_data.items():
        n = max(len(lines), 1)
        oem_c   = sum(1 for l in lines if oem_re.match(l))
        price_c = sum(1 for l in lines if price_re.match(l) and ("." in l or "," in l))
        avail_c = sum(1 for l in lines if "ןימז" in l)  # ןימז
        if oem_c / n > 0.25 and oem_c > best_oem:
            best_oem = oem_c; oem_col_x = x_bucket
        if price_c / n > 0.25 and price_c > best_price:
            best_price = price_c; price_col_x = x_bucket
        if avail_c / n > 0.25 and avail_c > best_avail:
            best_avail = avail_c; avail_col_x = x_bucket

    if not oem_col_x:
        return []

    oem_lines   = [l for l in col_data[oem_col_x]   if oem_re.match(l)]
    price_lines = col_data.get(price_col_x, [])
    avail_lines = col_data.get(avail_col_x, [])

    results = []
    for i, oem in enumerate(oem_lines):
        oem_norm = normalize_key(oem)
        if not oem_norm or len(oem_norm) < 4:
            continue

        price = None
        if i < len(price_lines):
            try:
                price = float(price_lines[i].replace(",", ""))
                if price <= 0:
                    price = None
            except (ValueError, AttributeError):
                pass

        available = True
        if i < len(avail_lines):
            available = "אל" not in avail_lines[i]  # אל

        results.append(PdfRow(
            oem_number=oem,
            oem_norm=oem_norm,
            name=f"{manufacturer.upper()} {oem_norm}",
            price=price,
            available=available,
        ))
    return results


def _parse_text_fallback(pdf_path, manufacturer):
    """
    Fast text extraction for large PDFs (>150 pages).
    Strategy:
      1. Try PyMuPDF (fitz) text extraction + line-regex (very fast ~2s).
      2. If yield < threshold (e.g. column-layout PDFs like Mitsubishi where
         fitz gives few regex-matchable lines), fall back to pdfminer
         extract_pages() + _parse_column_page() which handles RTL column PDFs.
    This avoids the 339s pdfminer-for-all-pages penalty on large plain-text PDFs
    while still correctly handling column-layout PDFs.
    """
    import fitz  # PyMuPDF - fast extraction

    # ── Step 1: fast fitz extraction ────────────────────────────────────
    fitz_rows = []
    n_pages = 0
    try:
        doc = fitz.open(pdf_path)
        n_pages = len(doc)
        log.info("Text fallback: %d pages via PyMuPDF", n_pages)
        fitz_lines = []
        for page in doc:
            fitz_lines.extend(page.get_text("text").splitlines())
        doc.close()
        for i, line in enumerate(fitz_lines):
            line_up = line.upper().strip()
            if not line_up:
                continue
            oem_matches = list(_OEM_PAT.finditer(line_up))
            if not oem_matches:
                continue
            oem_raw = oem_matches[-1].group(1)
            if not (re.search(r'[A-Z]', oem_raw) and re.search(r'[0-9]', oem_raw)):
                continue
            nk = normalize_key(oem_raw)
            if not nk or len(nk) < 4:
                continue
            # Skip price/availability lines (e.g. Suzuki: "67.50 לא זמין AVK310 סוזוקי").
            # Real OEM description lines always contain מקורי or חליפי (part type marker).
            # Price lines contain availability (זמין/לא זמין) WITHOUT a type marker.
            _line_has_avail = ('\u05d6\u05de\u05d9\u05df' in line)  # זמין
            _line_has_type  = ('\u05de\u05e7\u05d5\u05e8\u05d9' in line or '\u05d7\u05dc\u05d9\u05e4\u05d9' in line)  # מקורי/חליפי
            if _line_has_avail and not _line_has_type:
                continue
            price = None
            for pm in _PRICE_PAT.finditer(line):
                try:
                    price = parse_price(pm.group(1))
                    if price and price > 0:
                        break
                except Exception:
                    pass
            # Multi-line format lookup:
            # Hyundai: OEM on N, price+avail on N+1
            # Suzuki standard: price+avail on N-1, type+desc+OEM on N
            # Suzuki split: price on N-3, brand on N-2, type on N-1, OEM on N
            next_line = fitz_lines[i + 1] if i + 1 < len(fitz_lines) else ""
            if price is None and next_line:
                for pm in _PRICE_PAT.finditer(next_line):
                    try:
                        price = parse_price(pm.group(1))
                        if price and price > 0:
                            break
                    except Exception:
                        pass
            if price is None:
                for back in range(1, 4):  # look up to 3 lines back
                    if i - back < 0:
                        break
                    prev = fitz_lines[i - back]
                    for pm in _PRICE_PAT.finditer(prev):
                        try:
                            price = parse_price(pm.group(1))
                            if price and price > 0:
                                break
                        except Exception:
                            pass
                    if price:
                        prev_line = prev
                        break
            prev_line = fitz_lines[i - 1] if i > 0 else ""
            avail = True
            combined = line + next_line + prev_line
            if "\u05dc\u05d0 \u05d6\u05de\u05d9\u05df" in combined or "unavail" in combined.upper():
                avail = False
            # Extract Hebrew description:
            # 1. From text before OEM on current line (Suzuki: type+desc+OEM on N)
            # 2. Fallback: from next_line (Hyundai: desc on N+1)
            name_he = None
            oem_pos = line.upper().find(oem_raw)
            prefix_text = line[:oem_pos] if oem_pos > 0 else ""
            if prefix_text and any("\u0590" <= c <= "\u05ff" for c in prefix_text):
                he_words = re.findall(r"[\u0590-\u05ff][\u0590-\u05ff\s\-\'\"]*", prefix_text)
                candidate = " ".join(he_words).strip()
                # Strip leading part-type labels (מקורי / חליפי) — they are not descriptions
                candidate = re.sub(r"^(\u05de\u05e7\u05d5\u05e8\u05d9|\u05d7\u05dc\u05d9\u05e4\u05d9)\s*", "", candidate).strip()
                # Strip trailing/leading availability text
                candidate = re.sub(r"(\u05dc\u05d0\s*)?\u05d6\u05de\u05d9\u05df", "", candidate).strip()
                if candidate:
                    name_he = candidate[:255]
            if not name_he and next_line:
                desc = re.sub(r"[\d,\.]+\s*(זמין|לא\s*זמין).*$", "", next_line).strip()
                desc = re.sub(r"[\d,\.]+\s*$", "", desc).strip()
                if desc and any("\u0590" <= c <= "\u05ff" for c in desc):
                    name_he = desc[:255]
            fitz_rows.append(PdfRow(
                oem_number=oem_raw,
                oem_norm=nk,
                name=f"{manufacturer.upper()} {nk}",
                name_he=name_he,
                price=price,
                available=avail,
            ))
    except Exception as e:
        log.warning("PyMuPDF extraction failed: %s", e)

    # Threshold: fitz must yield at least 1 row per 10 pages to be considered good.
    # Column-layout PDFs (Mitsubishi) yield ~0 rows via line-regex; plain-text yields 1000s.
    min_expected = max(20, n_pages // 10)
    if len(fitz_rows) >= min_expected:
        log.info("PyMuPDF yield %d rows (threshold %d) — using fitz result", len(fitz_rows), min_expected)
        rows = fitz_rows
    else:
        # ── Step 2: pdfminer column extraction (column-layout PDFs) ────
        log.info("PyMuPDF yield %d rows < threshold %d — falling back to pdfminer column extraction",
                 len(fitz_rows), min_expected)
        from pdfminer.high_level import extract_pages as _extract_pages
        from pdfminer.layout import LTTextContainer as _LTText
        rows = []
        try:
            for page_layout in _extract_pages(pdf_path):
                try:
                    elems = [
                        (el.x0, el.get_text())
                        for el in page_layout
                        if isinstance(el, _LTText) and el.get_text().strip()
                    ]
                    rows.extend(_parse_column_page(elems, manufacturer))
                except Exception:
                    continue
        except Exception as e:
            log.warning("pdfminer column extraction failed: %s", e)

    seen, unique = set(), []
    for r in rows:
        if r.oem_norm not in seen:
            seen.add(r.oem_norm)
            unique.append(r)
    log.info("Text fallback: %d unique keys", len(unique))
    return unique


# --- DB operations ---# --- DB operations ---

async def fetch_manufacturer_rows(conn, manufacturer):
    rows = await conn.fetch(
        """SELECT id, sku, oem_number, name, name_he, category, base_price,
                  is_active, manufacturer
           FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)""",
        manufacturer,
    )
    out = {}
    for row in rows:
        nk = normalize_key(row["oem_number"])
        if nk:
            # Prefer active rows over inactive when OEM key collides
            if nk not in out or (not out[nk]["is_active"] and row["is_active"]):
                out[nk] = dict(row)
    return out


async def _get_manufacturer_id(conn, manufacturer):
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name)=LOWER($1) LIMIT 1", manufacturer)
    if row: return str(row["id"])
    row = await conn.fetchrow(
        "SELECT id FROM car_brands WHERE LOWER(name) LIKE $1 LIMIT 1",
        f"%{manufacturer.lower()}%")
    return str(row["id"]) if row else None


def _make_sku(manufacturer, oem_norm, existing_skus):
    prefix = re.sub(r"[^A-Z0-9]", "", manufacturer.upper())[:4] or "PART"
    base = f"{prefix}-{oem_norm}"[:95]
    sku = base
    n = 1
    while sku.upper() in existing_skus:
        n += 1; sku = f"{base}-{n}"
    existing_skus.add(sku.upper())
    return sku


async def upsert_parts(conn, manufacturer, pdf_rows, db_rows, dry_run):
    metrics = dict(inserted=0, updated_price=0, updated_name=0,
                   deactivated_unavailable=0, reactivated=0, category_counts={})

    all_sku_rows = await conn.fetch(
        "SELECT sku FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1)", manufacturer)
    existing_skus = {str(r["sku"]).upper() for r in all_sku_rows if r["sku"]}

    manufacturer_id = await _get_manufacturer_id(conn, manufacturer)

    to_insert, to_uprice, to_uname, to_ucatname, to_deact, to_react = [], [], [], [], [], []

    for pdf_row in pdf_rows:
        nk = pdf_row.oem_norm
        existing = db_rows.get(nk)

        if existing is None:
            sku = _make_sku(manufacturer, nk, existing_skus)
            cat = guess_category_by_text(f"{pdf_row.name or ''} {pdf_row.name_he or ''} {manufacturer}") or "general"
            metrics["category_counts"][cat] = metrics["category_counts"].get(cat, 0) + 1
            to_insert.append((
                uuid.uuid4(), sku[:100],
                (pdf_row.name or f"{manufacturer.upper()} {nk}")[:255],
                cat[:100], manufacturer, manufacturer_id, "OEM",
                pdf_row.name_he[:255] if pdf_row.name_he else None,
                json.dumps({"source":"supplier_pdf_import","imported_at":datetime.utcnow().isoformat()},ensure_ascii=False),
                json.dumps([{"manufacturer":manufacturer.upper(),"model":"All Models",
                             "year_from":2020,"year_to":datetime.utcnow().year,
                             "source":"supplier_pdf_import"}],ensure_ascii=False),
                pdf_row.oem_number[:100],
                float(pdf_row.price or 0.0),
                pdf_row.available,
            ))
            metrics["inserted"] += 1
        else:
            rid = str(existing["id"])
            if pdf_row.available and not existing.get("is_active"):
                to_react.append(rid); metrics["reactivated"] += 1
            elif not pdf_row.available and existing.get("is_active"):
                to_deact.append(rid); metrics["deactivated_unavailable"] += 1

            if pdf_row.price is not None:
                db_price = float(existing.get("base_price") or 0)
                if abs(db_price - pdf_row.price) > 0.01:
                    to_uprice.append((pdf_row.price, rid)); metrics["updated_price"] += 1

            if pdf_row.name and existing.get("name"):
                db_name = str(existing["name"])
                if re.fullmatch(r"[A-Z]+ (PART )?[A-Z0-9\-]+", db_name):
                    to_uname.append((
                        pdf_row.name[:255],
                        pdf_row.name_he[:255] if pdf_row.name_he else None,
                        rid))
                    metrics["updated_name"] += 1

            # Update name_he + category for existing rows where description is missing
            if pdf_row.name_he and not existing.get("name_he"):
                new_cat = guess_category_by_text(
                    f"{pdf_row.name or ''} {pdf_row.name_he} {manufacturer}"
                ) or None
                cur_cat = existing.get("category") or "general"
                upd_cat = new_cat if (new_cat and new_cat != "general" and cur_cat in ("general", "כללי", None)) else cur_cat
                to_ucatname.append((pdf_row.name_he[:255], upd_cat[:100], rid))
                metrics["updated_desc"] = metrics.get("updated_desc", 0) + 1

    if not dry_run:
        BATCH = 25
        for i in range(0, len(to_insert), BATCH):
            await conn.executemany(
                """INSERT INTO parts_catalog
                    (id,sku,name,category,manufacturer,manufacturer_id,part_type,
                     name_he,specifications,compatible_vehicles,oem_number,
                     base_price,is_active,
                     part_condition,is_safety_critical,needs_oem_lookup,master_enriched,
                     created_at,updated_at)
                   VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,$11,
                          $12,$13,'new',false,false,false,NOW(),NOW())
                   ON CONFLICT (sku) DO NOTHING""",
                to_insert[i:i+BATCH])
        if to_uprice:
            await conn.executemany(
                "UPDATE parts_catalog SET base_price=$1,updated_at=NOW() WHERE id=$2::uuid", to_uprice)
        if to_uname:
            await conn.executemany(
                "UPDATE parts_catalog SET name=$1,name_he=$2,updated_at=NOW() WHERE id=$3::uuid", to_uname)
        if to_ucatname:
            for i in range(0, len(to_ucatname), BATCH):
                await conn.executemany(
                    "UPDATE parts_catalog SET name_he=$1,category=$2,updated_at=NOW() WHERE id=$3::uuid",
                    to_ucatname[i:i+BATCH])
        if to_deact:
            await conn.execute(
                "UPDATE parts_catalog SET is_active=FALSE,updated_at=NOW() WHERE id=ANY($1::uuid[])", to_deact)
        if to_react:
            await conn.execute(
                "UPDATE parts_catalog SET is_active=TRUE,updated_at=NOW() WHERE id=ANY($1::uuid[])", to_react)

    return metrics


# --- Junk deactivation + agent queue ---

async def deactivate_junk_and_queue(conn, manufacturer, dry_run):
    junk_rows = await conn.fetch(
        r"""SELECT id, oem_number, name FROM parts_catalog
            WHERE LOWER(manufacturer)=LOWER($1) AND is_active=TRUE
              AND (oem_number ~ '[\u0590-\u05ff]'
                   OR (length(oem_number) <= 3 AND oem_number !~ '^[0-9]+$'))""",
        manufacturer)
    if not junk_rows:
        return 0, 0
    junk_ids = [str(r["id"]) for r in junk_rows]
    if not dry_run:
        await conn.execute(
            "UPDATE parts_catalog SET is_active=FALSE,updated_at=NOW() WHERE id=ANY($1::uuid[])",
            junk_ids)
        for row in junk_rows:
            try:
                await conn.execute(
                    """INSERT INTO agent_todos
                        (id,title,description,status,priority,assigned_to_agent,artifacts,created_at,updated_at)
                       VALUES(gen_random_uuid(),
                              'Fix junk OEM: ' || $3,
                              $4,
                              'not_started','high','db_cleanup_agent',
                              jsonb_build_object('part_id',$1,'manufacturer',$2,'oem_number',$3,'reason','hebrew_or_junk_oem'),
                              NOW(),NOW())""",
                    str(row["id"]), manufacturer, row["oem_number"],
                    f"Part {row['id']} ({manufacturer}) has junk OEM number: {row['oem_number'][:60]}. Deactivated. Re-import from supplier PDF.")
            except Exception:
                pass
    return len(junk_rows), len(junk_rows)


# --- Meilisearch sync ---

def trigger_meili_sync(manufacturer, dry_run):
    if dry_run:
        log.info("[DRY-RUN] Would sync Meilisearch for %s", manufacturer)
        return True
    script = Path(__file__).parent / "meili_sync.py"
    if not script.exists():
        log.warning("meili_sync.py not found")
        return False
    try:
        res = subprocess.run(
            ["python3", str(script), "--manufacturer", manufacturer, "--no-rebuild"],
            capture_output=True, text=True, timeout=600,
            cwd=str(Path(__file__).parent))
        if res.returncode == 0:
            log.info("Meilisearch sync OK for %s", manufacturer); return True
        log.warning("meili_sync stderr: %s", res.stderr[-300:]); return False
    except Exception as exc:
        log.warning("meili_sync error: %s", exc); return False


# --- Main pipeline ---

async def run_pipeline(pdf_path, manufacturer, apply):
    t0 = datetime.utcnow()
    report = ImportReport(manufacturer=manufacturer, pdf_path=pdf_path)

    print("PROGRESS:10", flush=True)
    # 1. Parse PDF
    pdf_rows = parse_pdf(pdf_path, manufacturer)
    report.pdf_rows = len(pdf_rows)
    seen_norms = {}
    for r in pdf_rows:
        if r.oem_norm not in seen_norms:
            seen_norms[r.oem_norm] = r
    unique = list(seen_norms.values())
    report.pdf_valid_keys = len(unique)
    log.info("PDF: %d raw rows -> %d unique keys", report.pdf_rows, report.pdf_valid_keys)
    print("PROGRESS:30", flush=True)

    # 2. Read DB
    conn = await connect_db()
    try:
        db_rows = await fetch_manufacturer_rows(conn, manufacturer)
        report.db_rows_before = len(db_rows)
        report.db_active_before = sum(1 for r in db_rows.values() if r.get("is_active"))
        log.info("DB: %d total (%d active) for %s",
                 report.db_rows_before, report.db_active_before, manufacturer)
        print("PROGRESS:45", flush=True)

        # 3. Upsert
        m = await upsert_parts(conn, manufacturer, unique, db_rows, dry_run=not apply)
        report.inserted = m["inserted"]
        report.updated_price = m["updated_price"]
        report.updated_name = m["updated_name"]
        report.category_counts = m.get("category_counts", {})
        report.deactivated_unavailable = m["deactivated_unavailable"]
        report.reactivated = m["reactivated"]
        print("PROGRESS:65", flush=True)

        # 4. Fix junk
        jd, jq = await deactivate_junk_and_queue(conn, manufacturer, dry_run=not apply)
        report.junk_deactivated = jd
        report.junk_queued_to_agent = jq
        print("PROGRESS:80", flush=True)

        # 5. Count verify
        active = await conn.fetchval(
            "SELECT COUNT(*) FROM parts_catalog WHERE LOWER(manufacturer)=LOWER($1) AND is_active=TRUE",
            manufacturer)
        report.db_active_after = int(active or 0)
        print("PROGRESS:90", flush=True)
    finally:
        await conn.close()

    # 6. Meilisearch
    if apply:
        report.meili_synced = trigger_meili_sync(manufacturer, dry_run=False)

    report.elapsed_s = (datetime.utcnow() - t0).total_seconds()
    print("PROGRESS:98", flush=True)
    _print_report(report, dry_run=not apply)
    return report


def _print_report(r, dry_run):
    mode = "DRY-RUN" if dry_run else "APPLIED"
    diff = r.db_active_after - r.pdf_valid_keys
    status = "IN SYNC" if abs(diff) <= 5 else f"GAP {diff:+d}"
    lines = [
        f"\n{'='*58}",
        f"  Supplier PDF Import Report [{mode}]",
        f"  Manufacturer : {r.manufacturer}",
        f"  PDF          : {r.pdf_path}",
        f"{'='*58}",
        f"  PDF rows parsed      : {r.pdf_rows}",
        f"  PDF unique keys      : {r.pdf_valid_keys}",
        f"  DB rows before       : {r.db_rows_before}  (active: {r.db_active_before})",
        f"  --- Changes ---",
        f"  Inserted new         : {r.inserted}",
        f"  Price updated        : {r.updated_price}",
        f"  Name updated         : {r.updated_name}",
        f"  Deactivated (unavail): {r.deactivated_unavailable}",
        f"  Reactivated          : {r.reactivated}",
        f"  Junk OEM deactivated : {r.junk_deactivated}",
        f"  Junk queued to agent : {r.junk_queued_to_agent}",
        f"  --- Final ---",
        f"  DB active after      : {r.db_active_after}",
        f"  PDF unique keys      : {r.pdf_valid_keys}",
        f"  PDF vs DB delta      : {diff:+d}  [{status}]",
        f"  Meili synced         : {'yes' if r.meili_synced else 'no/skipped'}",
        f"  Elapsed              : {r.elapsed_s:.1f}s",
        f"{'='*58}",
    ]
    if r.errors:
        lines += [f"  Errors:"] + [f"    - {e}" for e in r.errors[:5]]
    print("\n".join(lines) + "\n")
    # Emit structured JSON for frontend consumption
    import json as _json
    report_data = {
        "manufacturer": r.manufacturer,
        "pdf_rows": r.pdf_rows,
        "pdf_valid_keys": r.pdf_valid_keys,
        "db_rows_before": r.db_rows_before,
        "db_active_before": r.db_active_before,
        "inserted": r.inserted,
        "updated_price": r.updated_price,
        "updated_name": r.updated_name,
        "deactivated_unavailable": r.deactivated_unavailable,
        "reactivated": r.reactivated,
        "junk_deactivated": r.junk_deactivated,
        "db_active_after": r.db_active_after,
        "meili_synced": r.meili_synced,
        "elapsed_s": round(r.elapsed_s, 1),
        "errors": r.errors[:5],
        "category_counts": r.category_counts,
        "dry_run": dry_run,
    }
    print("REPORT_JSON:" + _json.dumps(report_data, ensure_ascii=False), flush=True)


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Import supplier PDF catalog into parts_catalog")
    parser.add_argument("--pdf", required=True, help="Path to supplier PDF file")
    parser.add_argument("--manufacturer", required=True, help="Manufacturer name e.g. ORA")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    group.add_argument("--dry-run", action="store_true", dest="dry_run")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERROR: PDF not found: {pdf_path}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(run_pipeline(
        pdf_path=str(pdf_path),
        manufacturer=args.manufacturer,
        apply=args.apply,
    ))


if __name__ == "__main__":
    main()
