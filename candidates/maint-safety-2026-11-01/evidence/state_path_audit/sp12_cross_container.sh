#!/bin/bash
# SP12 - login cookie persistence across containers. usage: sp12_cross_container.sh <merged-candidate-tree>
# Container A (root) runs the REAL login() against the controlled fixture (no Facebook, no credentials, no network); container B (a NEW
# container, uid 1000, same state directory) reads the session through the real consumers. The shared state directory is RAM-backed
# (/dev/shm) because the host disk is full; it is removed at the end. Cookie VALUES are never printed (names/counts only).
set -u
TREE=$1; ST=/dev/shm/asf_sp12_final; IMG=autosparefinder-backend:latest
rm -rf $ST; mkdir -p $ST
D="docker run --rm --memory 1500m --network none -v $TREE:/app:ro -v $ST:/app/state -e PYTHONPATH=/app -e PYTHONDONTWRITEBYTECODE=1 -e PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright -w /"
echo "== A. container A (uid 0): real login() -> cookies.json, cwd=/"; $D $IMG python3 /app/tests/fb_login_fixture_run.py /app authenticated 2>&1 | grep -E "Health check|Saved|Cookie names|AUTHENTICATED|login_returned" | cut -c1-230
echo "== file on the shared directory"; stat -c "%a %u:%g %s bytes %n" $ST/fb_browser_session/cookies.json
echo "== B. NEW container B (uid 1000), same directory: consumers read the session"; chmod 755 $ST $ST/fb_browser_session
$D --user 1000:1000 $IMG python3 /app/tests/fb_session_reader_run.py /app
echo "== C. a failed login in a new container leaves the file byte-identical"; H1=$(sha256sum $ST/fb_browser_session/cookies.json | cut -c1-16)
$D $IMG python3 /app/tests/fb_login_fixture_run.py /app unauthenticated 2>&1 | grep -E "SKIPPED|FAILED" | cut -c1-200; H2=$(sha256sum $ST/fb_browser_session/cookies.json | cut -c1-16); echo "sha256 before=$H1 after=$H2"
echo "== D. STATE_DIR override honoured by fb_login_extended.py / default unchanged"
$D -e STATE_DIR=/srv/override $IMG python3 -c "
import sys, importlib.util; sys.path.insert(0,'/app')
s=importlib.util.spec_from_file_location('x','/app/fb_login_extended.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('override ->', m._STATE_DIR)"
$D $IMG python3 -c "
import sys, importlib.util; sys.path.insert(0,'/app')
s=importlib.util.spec_from_file_location('x','/app/fb_login_extended.py'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('default  ->', m._STATE_DIR, m._COOKIES_FILE)"
echo "== E. uid 1000 against the root-owned directory: login() refuses up front, names the path and uid, writes nothing"
$D --user 1000:1000 -e HOME=/tmp $IMG python3 /app/tests/fb_login_fixture_run.py /app authenticated 2>&1 | grep -E "not writable|login_returned" | cut -c1-230
echo "== F. uncreatable state dir"; $D -e STATE_DIR=/proc/nonexistent $IMG python3 /app/tests/fb_login_fixture_run.py /app authenticated 2>&1 | grep -E "cannot be created|login_returned" | cut -c1-230
echo "== no cookie value was printed above (only names / counts / hashes); the caller greps this file for the fixture's value string"
rm -rf $ST; echo "RAM-backed state removed"
