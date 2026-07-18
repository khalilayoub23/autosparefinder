import re

path = '/opt/autosparefinder/backend/supplier_pdf_import.py'
text = open(path, encoding='utf-8').read()

pattern = re.compile(
    r'    # .{0,80}probe first 5 pages.{0,120}\n'
    r'    is_column = False\n'
    r'    try:\n'
    r'.*?'
    r'    except Exception:\n'
    r'        pass\n',
    re.DOTALL
)

m = pattern.search(text)
if not m:
    print("ERROR: probe block not found")
    idx = text.find('is_column = False')
    print(repr(text[max(0,idx-200):idx+200]))
    raise SystemExit(1)

new_probe = (
    '    # Step 1: probe first 5 pages - detect TWO-COLUMN layout by x-spread\n'
    '    # Column layout (Mitsubishi): text at x0<150 AND x0>250 on same page.\n'
    '    # Plain-text layout (Suzuki): all text near left margin only.\n'
    '    is_column = False\n'
    '    try:\n'
    '        for page_layout in _extract_pages(pdf_path, page_numbers=range(0, min(5, n_pages))):\n'
    '            x_vals = [\n'
    '                el.x0\n'
    '                for el in page_layout\n'
    '                if isinstance(el, _LTText) and el.get_text().strip()\n'
    '            ]\n'
    '            if len(x_vals) >= 4:\n'
    '                has_left  = any(x < 150 for x in x_vals)\n'
    '                has_right = any(x > 250 for x in x_vals)\n'
    '                if has_left and has_right:\n'
    '                    is_column = True\n'
    '                    break\n'
    '    except Exception:\n'
    '        pass\n'
)

fixed = text[:m.start()] + new_probe + text[m.end():]
open(path, 'w', encoding='utf-8').write(fixed)
print("Probe replaced OK - old %d chars -> new %d chars" % (m.end()-m.start(), len(new_probe)))
