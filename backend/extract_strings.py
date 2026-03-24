import struct, zlib, re

OUTPUT = '/tmp/xlsx_info.txt'

with open('parts data base.xlsx', 'rb') as f:
    raw = f.read()

out = open(OUTPUT, 'w')
out.write(f"File size: {len(raw):,} bytes\n")

def read_local_entries(data):
    entries = {}
    offset = 0
    while True:
        idx = data.find(b'PK\x03\x04', offset)
        if idx == -1:
            break
        if idx + 30 > len(data):
            break
        try:
            header = struct.unpack_from('<4sHHHHHIIIHH', data, idx)
            compress = header[3]
            comp_sz = header[7]
            name_len = header[9]
            extra_len = header[10]
            if name_len < 200 and comp_sz > 0:
                name = data[idx+30:idx+30+name_len].decode('utf-8', errors='replace')
                if '\x00' not in name and name.startswith(('xl/', '[Content', '_rels')):
                    data_start = idx + 30 + name_len + extra_len
                    compressed = data[data_start:data_start+comp_sz]
                    if name not in entries:
                        entries[name] = (compress, compressed)
        except Exception:
            pass
        offset = idx + 4
    return entries

entries = read_local_entries(raw)
out.write(f"Entries found ({len(entries)}):\n")
for name in sorted(entries.keys()):
    out.write(f"  {name}\n")

def decompress_entry(compress, compressed):
    if compress == 0:
        return compressed
    elif compress == 8:
        try:
            return zlib.decompress(compressed, -15)
        except Exception:
            return None
    return None

if 'xl/sharedStrings.xml' in entries:
    c, d = entries['xl/sharedStrings.xml']
    xml = decompress_entry(c, d)
    if xml:
        xml_str = xml.decode('utf-8', errors='replace')
        strings = re.findall(r'<t(?:\s[^>]*)?>([^<]*)</t>', xml_str)
        out.write(f"\nShared strings count: {len(strings)}\n")
        out.write("First 60 strings:\n")
        for i, s in enumerate(strings[:60]):
            out.write(f"  [{i}] {repr(s)}\n")
    else:
        out.write("FAILED to decompress sharedStrings.xml\n")
else:
    out.write("sharedStrings.xml NOT FOUND\n")

# Also check workbook.xml for sheet names
if 'xl/workbook.xml' in entries:
    c, d = entries['xl/workbook.xml']
    xml = decompress_entry(c, d)
    if xml:
        wb_str = xml.decode('utf-8', errors='replace')
        sheets = re.findall(r'<sheet[^>]+name="([^"]+)"', wb_str)
        out.write(f"\nWorkbook sheets: {sheets}\n")

out.close()
print("Done")
