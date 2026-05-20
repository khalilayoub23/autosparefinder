"""
_parse_text_fallback is only called for PDFs > 150 pages.
All current large PDFs (Suzuki, Mercedes-Benz) are plain-text/table layout.
Small column-layout PDFs (Mitsubishi) go through _extract_tables instead.
Therefore: remove column detection entirely - always use fast extract_text().
"""
import re

path = '/opt/autosparefinder/backend/supplier_pdf_import.py'
text = open(path, encoding='utf-8').read()

# Match the entire function body from def to the final return statement
fn_pattern = re.compile(
    r'^def _parse_text_fallback\(pdf_path, manufacturer\):\n'
    r'.*?'
    r'^# --- DB operations ---',
    re.MULTILINE | re.DOTALL
)

m = fn_pattern.search(text)
if not m:
    print("ERROR: function not found")
    raise SystemExit(1)

new_fn = '''def _parse_text_fallback(pdf_path, manufacturer):
    """
    Fast text extraction for large PDFs (>150 pages).
    Uses pdfminer extract_text() - no layout analysis, ~10x faster than extract_pages().
    Column-layout PDFs (e.g. Mitsubishi) are handled by _extract_tables() since
    they are typically small (<150 pages) and never reach this function.
    """
    from pdfminer.pdfpage import PDFPage as _PDFPage
    from pdfminer.high_level import extract_text as _extract_text

    try:
        with open(pdf_path, "rb") as _f:
            n_pages = sum(1 for _ in _PDFPage.get_pages(_f))
    except Exception:
        n_pages = 0

    log.info("Text fallback: %d pages, using fast extract_text", n_pages)

    try:
        full_text = _extract_text(pdf_path)
    except Exception as e:
        log.warning("extract_text failed: %s", e)
        full_text = ""

    rows = []
    for line in full_text.splitlines():
        line_up = line.upper().strip()
        if not line_up:
            continue
        oem_matches = list(_OEM_PAT.finditer(line_up))
        if not oem_matches:
            continue
        oem_raw = oem_matches[-1].group(1)
        if not (re.search(r\'[A-Z]\', oem_raw) and re.search(r\'[0-9]\', oem_raw)):
            continue
        nk = normalize_key(oem_raw)
        if not nk or len(nk) < 4:
            continue

        price = None
        for pm in _PRICE_PAT.finditer(line):
            try:
                price = parse_price(pm.group(1))
                if price and price > 0:
                    break
            except Exception:
                pass

        avail = True
        if "\\u05dc\\u05d0 \\u05d6\\u05de\\u05d9\\u05df" in line or "unavail" in line_up:
            avail = False

        rows.append(PdfRow(
            oem_number=oem_raw,
            oem_norm=nk,
            name=f"{manufacturer.upper()} {nk}",
            price=price,
            available=avail,
        ))

    seen, unique = set(), []
    for r in rows:
        if r.oem_norm not in seen:
            seen.add(r.oem_norm)
            unique.append(r)
    log.info("Text fallback: %d unique keys from %d lines", len(unique), len(full_text.splitlines()))
    return unique


# --- DB operations ---'''

fixed = text[:m.start()] + new_fn + text[m.end() - len('# --- DB operations ---'):]
open(path, 'w', encoding='utf-8').write(fixed)
print("Function replaced OK")
