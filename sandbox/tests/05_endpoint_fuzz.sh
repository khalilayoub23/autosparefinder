#!/usr/bin/env bash
# Endpoint discovery fuzzing using ffuf + gobuster
# Run from inside sandbox_tools: bash /workspace/tests/05_endpoint_fuzz.sh

TARGET=${TARGET:-http://sandbox_backend:8000}
RESULTS=/workspace/results
WORDLIST=/workspace/wordlists/api_endpoints.txt

mkdir -p "$RESULTS"

echo "=== AutoSpareFinder Endpoint Fuzzer ==="
echo "Target: $TARGET"
echo ""

# ── 1. ffuf — known API wordlist ──────────────────────────────────────────────
echo "--- 1. ffuf scan with API wordlist ---"
ffuf \
    -u "$TARGET/FUZZ" \
    -w "$WORDLIST" \
    -mc 200,201,204,301,302,401,403,405 \
    -fc 404 \
    -t 20 \
    -timeout 10 \
    -o "$RESULTS/ffuf_api.json" \
    -of json \
    2>&1 | grep -E "^\[|Status:|200|401|403|405"

echo ""

# ── 2. ffuf — common admin/debug paths ────────────────────────────────────────
echo "--- 2. Hidden / debug path scan ---"
cat > /tmp/debug_paths.txt << 'PATHS'
admin
admin/login
dashboard
debug
metrics
prometheus
actuator
actuator/health
actuator/env
api/admin
api/debug
api/v1/admin
api/v1/debug
api/v1/internal
api/v1/system/debug
api/v1/system/env
docs
redoc
openapi.json
swagger.json
api-docs
graphql
PATHS

ffuf \
    -u "$TARGET/FUZZ" \
    -w /tmp/debug_paths.txt \
    -mc 200,201,301,302 \
    -t 10 \
    -timeout 8 \
    2>&1 | grep -E "^\[|Status:|:: Progress"

echo ""

# ── 3. gobuster — DNS subdomain check (if external) ──────────────────────────
if [[ "$TARGET" == "https://"* ]]; then
    DOMAIN=$(echo "$TARGET" | sed 's|https://||;s|/.*||')
    echo "--- 3. gobuster vhost scan on $DOMAIN ---"
    gobuster vhost \
        -u "$TARGET" \
        --domain "$DOMAIN" \
        -w /usr/share/nikto/databases/db_subdomains 2>/dev/null || \
    echo "    (skipped — no subdomain wordlist)"
fi

echo ""
echo "=== Results saved to $RESULTS/ffuf_api.json ==="
