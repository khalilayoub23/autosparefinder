import re

path = '/opt/autosparefinder/backend/supplier_pdf_import.py'
text = open(path, encoding='utf-8').read()

fn_pattern = re.compile(
    r'^def _parse_text_fallback\(pdf_path, manufacturer\):\n.*?^# --- DB operations ---',
    re.MULTILINE | re.DOTALL
)
m = fn_pattern.search(text)
if not m:
    print("ERROR: function not found"); raise SystemExit(1)

new_fn = (
    'def _parse_text_fallback(pdf_path, manufacturer):\n'
    '    """\n'
    '    Fast text extraction for large PDFs (>150 pages).\n'
    '    Uses PyMuPDF (fitz) for fast extraction - 20-50x faster than pdfminer.\n'
    '    Falls back to pdfminer extract_text() if fitz is unavailable.\n'
    '    """\n'
    '    lines = []\n'
    '    try:\n'
    '        import fitz  # PyMuPDF\n'
    '        doc = fitz.open(pdf_path)\n'
    '        log.info("Text fallback: %d pages via PyMuPDF", len(doc))\n'
    '        for page in doc:\n'
    '            lines.extend(page.get_text("text").splitlines())\n'
    '        doc.close()\n'
    '    except ImportError:\n'
    '        log.info("PyMuPDF not available, falling back to pdfminer")\n'
    '        try:\n'
    '            from pdfminer.high_level import extract_text as _extract_text\n'
    '            lines = _extract_text(pdf_path).splitlines()\n'
    '        except Exception as e:\n'
    '            log.warning("extract_text failed: %s", e)\n'
    '    except Exception as e:\n'
    '        log.warning("fitz extraction failed: %s", e)\n'
    '        try:\n'
    '            from pdfminer.high_level import extract_text as _extract_text\n'
    '            lines = _extract_text(pdf_path).splitlines()\n'
    '        except Exception:\n'
    '            pass\n'
    '\n'
    '    rows = []\n'
    '    for line in lines:\n'
    '        line_up = line.upper().strip()\n'
    '        if not line_up:\n'
    '            continue\n'
    '        oem_matches = list(_OEM_PAT.finditer(line_up))\n'
    '        if not oem_matches:\n'
    '            continue\n'
    '        oem_raw = oem_matches[-1].group(1)\n'
    '        if not (re.search(r\'[A-Z]\', oem_raw) and re.search(r\'[0-9]\', oem_raw)):\n'
    '            continue\n'
    '        nk = normalize_key(oem_raw)\n'
    '        if not nk or len(nk) < 4:\n'
    '            continue\n'
    '        price = None\n'
    '        for pm in _PRICE_PAT.finditer(line):\n'
    '            try:\n'
    '                price = parse_price(pm.group(1))\n'
    '                if price and price > 0:\n'
    '                    break\n'
    '            except Exception:\n'
    '                pass\n'
    '        avail = True\n'
    '        if "\\u05dc\\u05d0 \\u05d6\\u05de\\u05d9\\u05df" in line or "unavail" in line_up:\n'
    '            avail = False\n'
    '        rows.append(PdfRow(\n'
    '            oem_number=oem_raw,\n'
    '            oem_norm=nk,\n'
    '            name=f"{manufacturer.upper()} {nk}",\n'
    '            price=price,\n'
    '            available=avail,\n'
    '        ))\n'
    '\n'
    '    seen, unique = set(), []\n'
    '    for r in rows:\n'
    '        if r.oem_norm not in seen:\n'
    '            seen.add(r.oem_norm)\n'
    '            unique.append(r)\n'
    '    log.info("Text fallback: %d unique keys from %d lines", len(unique), len(lines))\n'
    '    return unique\n'
    '\n'
    '\n'
    '# --- DB operations ---'
)

fixed = text[:m.start()] + new_fn + text[m.end():]
open(path, 'w', encoding='utf-8').write(fixed)
print("Replaced with PyMuPDF version OK")
