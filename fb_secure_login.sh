#!/usr/bin/env bash
# fb_secure_login.sh — One-time Facebook session login for AutoSpareFinder
#
# Run this script via SSH on the server. It uses `docker exec -it` to give
# a real TTY to the login script, so Python's getpass can prompt for the
# password securely (no echo, never stored in shell history or process list).
#
# Usage:  ssh user@161.97.158.177 -p 63159
#         bash /opt/autosparefinder/fb_secure_login.sh
#
# After success the authenticated cookies.json is in the Docker named volume
# autosparefinder_worker_state → /app/state/fb_browser_session/cookies.json

set -e

echo ""
echo "================================================================"
echo "  AutoSpareFinder — Facebook Playwright Session Login"
echo "  (One-time setup — cookies persist until Facebook revokes them)"
echo "================================================================"
echo ""

# Verify container is running
if ! docker ps --format '{{.Names}}' | grep -q autospare_backend; then
    echo "ERROR: autospare_backend container is not running."
    exit 1
fi

# Verify Xvfb is available in container
XVFB_PID=$(docker exec autospare_backend bash -c "pgrep -f 'Xvfb :99' || true")
if [ -z "$XVFB_PID" ]; then
    echo "INFO: Xvfb not running — fb_browser_login.py will start it automatically."
else
    echo "INFO: Xvfb is already running on :99 (pid $XVFB_PID)"
fi

echo ""
echo "Launching interactive login in the container..."
echo "(You will be prompted for the Facebook email and password)"
echo ""

# -it allocates a TTY so getpass.getpass() works (no echo)
# DISPLAY=:99 uses the existing Xvfb virtual framebuffer
# No credentials are passed as arguments — they are entered at the TTY prompt
docker exec -it -e DISPLAY=:99 autospare_backend \
    python3 /app/social/facebook_browser/fb_browser_login.py

STATUS=$?
echo ""
if [ $STATUS -eq 0 ]; then
    echo "================================================================"
    echo "  Login SUCCEEDED — Facebook session is authenticated."
    echo "  The production pipeline is now ready for Group publishing."
    echo "================================================================"
else
    echo "================================================================"
    echo "  Login FAILED (exit code $STATUS)."
    echo "  Check /app/state/fb_browser_session/ for screenshots."
    echo "================================================================"
fi
exit $STATUS
