#!/usr/bin/env bash
# SQL injection + XSS surface scan using sqlmap and manual payloads
# Run from inside sandbox_tools: bash /workspace/tests/04_injection_test.sh

TARGET=${TARGET:-http://sandbox_backend:8000}
RESULTS=/workspace/results

mkdir -p "$RESULTS/sqlmap"

echo "=== AutoSpareFinder Injection Tests ==="
echo "Target: $TARGET"
echo ""

# ── 1. Manual XSS probes on search ───────────────────────────────────────────
echo "--- 1. XSS reflection check ---"
XSS_PAYLOADS=(
    '<script>alert(1)</script>'
    '"><img src=x onerror=alert(1)>'
    'javascript:alert(1)'
    '$((7*7))'
    '{{7*7}}'
)
for payload in "${XSS_PAYLOADS[@]}"; do
    encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$payload")
    response=$(wget -qO- "$TARGET/api/v1/parts/search?q=$encoded" 2>/dev/null)
    ct=$(wget -qS --spider "$TARGET/api/v1/parts/search?q=$encoded" 2>&1 | grep -i "content-type" | head -1)
    if echo "$response" | grep -qF "$payload"; then
        echo "  ⚠️  REFLECTED in JSON (Content-Type: $ct): ${payload:0:40}"
        echo "      Check: does the frontend render external_suppliers URLs unescaped?"
    else
        echo "  ✅ Not reflected: ${payload:0:40}"
    fi
done

echo ""

# ── 2. SQLmap against search endpoint ────────────────────────────────────────
echo "--- 2. SQLmap scan on /parts/search?q= ---"
echo "    (this may take 1-2 min)"
sqlmap -u "$TARGET/api/v1/parts/search?q=brake&limit=10" \
    --level=2 --risk=1 \
    --batch \
    --output-dir="$RESULTS/sqlmap/search" \
    --technique=BEU \
    --timeout=10 \
    --retries=1 \
    2>&1 | tail -20
echo ""

# ── 3. SQLmap against login (JSON body) ───────────────────────────────────────
echo "--- 3. SQLmap scan on /auth/login (POST JSON) ---"
sqlmap -u "$TARGET/api/v1/auth/login" \
    --data='{"email":"*","password":"testpass1"}' \
    -H 'Content-Type: application/json' \
    --level=2 --risk=1 \
    --batch \
    --output-dir="$RESULTS/sqlmap/login" \
    --technique=BEU \
    --timeout=10 \
    --retries=1 \
    2>&1 | tail -20
echo ""

# ── 4. Manual SSTI probes ─────────────────────────────────────────────────────
echo "--- 4. SSTI (template injection) probes ---"
for payload in "{{7*7}}" "\${7*7}" "#{7*7}" "<%= 7*7 %>"; do
    encoded=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "$payload")
    response=$(curl -sf "$TARGET/api/v1/parts/search?q=$encoded" 2>/dev/null)
    if echo "$response" | grep -q "49"; then
        echo "  ❌ SSTI: $payload evaluated to 49"
    else
        echo "  ✅ SSTI not triggered: $payload"
    fi
done

echo ""
echo "=== Done. SQLmap full reports in $RESULTS/sqlmap/ ==="
