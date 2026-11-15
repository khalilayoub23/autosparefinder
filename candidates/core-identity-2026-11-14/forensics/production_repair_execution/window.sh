#!/bin/bash
# One production window: SAFE -> verify -> canary -> accept -> fresh SAFE -> rest -> accept -> final verify+fulldiff.
# env: CT (container), PF (1 = require in-container preflight SAFE), EXCL (--exclude-offer args), EXPECT (eligible count), REF, DL (deadline epoch)
set -u
CT=${CT:-autospare_backend}; PF=${PF:-1}; P=/app/state/cpie_ownership/overwrite_repair
SHA=655952659c60ff9824c8d9921662114776e749303265d0894f53202f62223158
T="docker exec $CT python3 /app/maintenance/cpie_overwrite_repair.py"
A="--manifest $P/repair_manifest.json.gz --expect-sha256 $SHA $EXCL"
safe() { [ "$PF" = 0 ] && return 0; for i in $(seq 1 ${1:-10}); do docker exec $CT python3 /app/scripts/restart_preflight.py 2>&1 | grep -q "PREFLIGHT: SAFE" && { echo "SAFE $(date -Is)"; return 0; }; sleep 3; done; return 1; }
until safe 2; do [ $(date +%s) -ge ${DL:-0} ] && { echo "HOLD: no SAFE before deadline"; exit 9; }; sleep 2; done
V=$($T --verify $A); echo "$V"
echo "$V" | python3 -c "import sys,json;c=json.load(sys.stdin)['counts'];sys.exit(0 if c=={'NOT_APPLIED':$EXPECT} else 1)" || { echo "ABORT: scope != $EXPECT NOT_APPLIED"; exit 1; }
CAN=$($T --apply --phase canary $A --approval-ref "$REF") || { echo "ABORT canary: $CAN"; exit 1; }; echo "$CAN"
CR=$(echo "$CAN" | python3 -c "import sys,json;print(json.loads(sys.stdin.read().strip().splitlines()[-1])['result'])")
$T --accept --phase-check canary --result $CR $A || { echo "ABORT: canary acceptance FAILED - rest NOT run"; exit 1; }
echo "canary accepted $(date -Is)"
safe 10 || { echo "HOLD: rest needs a fresh SAFE (result $CR)"; exit 8; }
RES=$($T --apply --phase rest $A --approval-ref "$REF" --canary-result $CR) || { echo "ABORT rest: $RES"; exit 1; }; echo "$RES"
RR=$(echo "$RES" | python3 -c "import sys,json;print(json.loads(sys.stdin.read().strip().splitlines()[-1])['result'])")
$T --accept --phase-check rest --result $RR $A || { echo "REST ACCEPTANCE FAILED"; exit 1; }
$T --verify --rows $A | head -c 400; echo; echo "WINDOW DONE $(date -Is) canary=$CR rest=$RR"
