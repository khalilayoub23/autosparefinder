#!/usr/bin/env bash
# Render the CURRENT WhatsApp link QR as a PNG you can open in VS Code.
#
# Why this exists: the bridge prints the QR as terminal blocks, which are
# unreadable in some terminals/clients. WhatsApp rotates the payload roughly
# every 20-60s, so this is meant to be re-run right before you scan rather than
# generated once.
#
# The QR is written to a FILE on purpose and never served over HTTP: whoever
# scans it links THEIR phone to this bridge, so it must not be reachable over
# the network.
#
# Usage:  bash /opt/autosparefinder/whatsapp-bridge/show_qr.sh
# Then open /opt/autosparefinder/WHATSAPP_QR.png  (click it in the VS Code tree)
set -euo pipefail

ROOT=/opt/autosparefinder
QR_TXT="$ROOT/whatsapp-bridge/qr.txt"
OUT="$ROOT/WHATSAPP_QR.png"

state=$(curl -sS -m 8 http://127.0.0.1:3001/health 2>/dev/null || echo '{}')
if echo "$state" | grep -q '"connected":true'; then
  echo "✅ WhatsApp is already linked — no QR needed."
  echo "   $state"
  rm -f "$OUT"
  exit 0
fi

if [ ! -s "$QR_TXT" ]; then
  echo "No pending QR found ($QR_TXT is missing/empty)."
  echo "Restart the bridge to request one:  docker restart whatsapp_bridge"
  exit 1
fi

# Render inside the backend container: it already has the qrcode + Pillow libs.
# /app is the bind-mounted ./backend, so the PNG lands on the host filesystem.
QR_PAYLOAD=$(cat "$QR_TXT")
# -i is REQUIRED: without it docker exec does not attach stdin and `python3 -`
# silently reads an empty program, producing no file and no error.
docker exec -i -e QR_PAYLOAD="$QR_PAYLOAD" autospare_backend python3 - <<'PY'
import os
import qrcode

img = qrcode.make(os.environ["QR_PAYLOAD"], box_size=10, border=3)
img.save("/app/_wa_qr.png")
print(f"rendered {img.size[0]}x{img.size[1]}px")
PY

mv -f "$ROOT/backend/_wa_qr.png" "$OUT"

# Also print it straight into THIS terminal, large and using plain full-block
# characters. The bridge's own output uses half-height blocks, which several
# terminals render squashed or blank — unscannable even when present.
docker exec -i -e QR_PAYLOAD="$QR_PAYLOAD" autospare_backend python3 - <<'PY'
import os
import qrcode

qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_L)
qr.add_data(os.environ["QR_PAYLOAD"])
qr.make(fit=True)
m = qr.get_matrix()
h, w = len(m), len(m[0])

# HALF-BLOCK rendering: each terminal LINE carries two QR rows (upper half /
# lower half of the cell). A terminal cell is about twice as tall as it is wide,
# so one character per module in x and two modules per line in y comes out
# roughly SQUARE — which is what a scanner needs.
#
# The earlier "██ per module" version was geometrically correct too, but a
# WhatsApp payload is ~237 chars => a 65-module QR => 130 columns, which wraps
# in a normal terminal and then cannot be scanned at all. This is ~67 columns
# and ~34 lines.
print()
for y in range(0, h, 2):
    line = []
    for x in range(w):
        top = m[y][x]
        bot = m[y + 1][x] if y + 1 < h else False
        line.append("█" if top and bot else "▀" if top else "▄" if bot else " ")
    print("".join(line))
print()
print(f"({w} modules wide — if the code wraps onto two lines it will NOT scan;")
print(" widen the terminal, reduce the font size, or open WHATSAPP_QR.png)")
PY
age=$(( $(date +%s) - $(stat -c %Y "$QR_TXT") ))
echo
echo "📱 QR written to: $OUT   (payload is ${age}s old)"
echo "   Open that file in VS Code, then on your phone:"
echo "   WhatsApp → Settings → Linked Devices → Link a Device → scan"
echo
echo "   The code rotates every ~20-60s. If it fails, just run this again."
