#!/usr/bin/env bash
# Nikto web server scan — catches common misconfigs, headers, and known CVEs.
# Run from inside sandbox_tools: bash /workspace/tests/06_nikto_scan.sh [--external]

TARGET=${TARGET:-http://sandbox_backend:8000}
RESULTS=/workspace/results
mkdir -p "$RESULTS"

if [[ "$1" == "--external" ]]; then
    TARGET="${TARGET_EXTERNAL:-https://autosparefinder.co.il}"
    echo "=== Nikto — EXTERNAL target: $TARGET ==="
else
    echo "=== Nikto — SANDBOX target: $TARGET ==="
fi

nikto \
    -host "$TARGET" \
    -output "$RESULTS/nikto_$(date +%Y%m%d_%H%M).txt" \
    -Format txt \
    -nointeractive \
    -timeout 10 \
    -maxtime 300 \
    2>&1

echo ""
echo "Full report saved to: $RESULTS/nikto_*.txt"
