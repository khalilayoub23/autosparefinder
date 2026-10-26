"""deploy/nginx.conf must never write OAuth secrets (authorization code, state, tokens) to its logs.

Regression for 2026-10-09: with nginx's default "combined" format, a Google OAuth redirect to
https://autosparefinder.co.il/?state=..&code=.. put the code in the access log request line, in the
Referer of every follow-up request from that page, and in the error log ("referrer:" on a static 404).

Host-side test (deploy/ and docker live outside the backend bind mount): it skips inside the container
and runs on the host:  python3 backend/tests/test_nginx_oauth_log_redaction.py
The functional part runs the REAL nginx.conf in a throwaway container with no network and read-only
mounts, sends fake values, and reads that container's own logs. Production nginx is not touched.
"""
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(os.path.dirname(BACKEND_DIR), "deploy", "nginx.conf")
FAKE = {"code": "4/0FAKEcodeVALUE123", "state": "FAKEstateVALUE456", "ref_code": "4/0REFcodeVALUE789",
        "access_token": "ya29.FAKEaccessTOKEN"}


def _skip(reason):
    import pytest
    pytest.skip(reason)


def _conf():
    if not os.path.exists(CONF):
        _skip("deploy/nginx.conf is not available in this environment (host-side test)")
    with open(CONF, encoding="utf-8") as f:
        return f.read()


def test_access_log_uses_the_redacted_format():
    s = _conf()
    assert re.search(r"^\s*access_log\s+/var/log/nginx/access\.log\s+redacted;", s, re.M)
    fmt = re.search(r"log_format\s+redacted\s+(.*?);", s, re.S).group(1)
    assert "$request_redacted" in fmt and "$http_referer_redacted" in fmt
    assert "$request\"" not in fmt and "$http_referer\"" not in fmt


def test_every_secret_parameter_is_redacted_in_request_and_referer():
    s = _conf()
    for param in ("code", "state", "access_token", "refresh_token", "id_token"):
        assert re.search(rf"map \$(request|oauth_rq_\d) \$oauth_rq_\d \{{\s*\"~[^\n]*\[\?&\]{param}=", s), f"request: {param}"
        assert re.search(rf"map \$(http_referer|oauth_rf_\d) \$oauth_rf_\d \{{\s*\"~[^\n]*\[\?&\]{param}=", s), f"referer: {param}"


def test_oauth_landing_page_sends_no_referrer_and_no_404_error_lines():
    s = _conf()
    block = re.search(r"location / \{(.*?)\n        \}", s, re.S).group(1)
    assert "add_header Referrer-Policy $oauth_referrer_policy always;" in block
    http_level = s[: s.index("server {")]                    # applies to every location, incl. the static regex
    assert re.search(r"^\s*log_not_found off;", http_level, re.M)


def test_existing_oauth_callback_locations_still_log_nothing():
    s = _conf()
    for loc in ("location /aliexpress/ {", "location = /api/aliexpress/callback {"):
        assert "access_log off;" in s[s.index(loc): s.index("}", s.index(loc))]


def _docker():
    if not shutil.which("docker") or not os.path.exists(CONF):
        _skip("docker / deploy/nginx.conf not available (host-side test)")


def test_live_throwaway_nginx_redacts_oauth_values_and_keeps_diagnostics():
    _docker()
    name = f"ngx-redaction-test-{uuid.uuid4().hex[:8]}"
    run = ["docker", "run", "-d", "--rm", "--name", name, "--network", "none",
           "-v", f"{CONF}:/etc/nginx/nginx.conf:ro", "-v", "autosparefinder_frontend_build:/usr/share/nginx/html:ro",
           "nginx:alpine"]
    subprocess.run(run, check=True, capture_output=True)
    try:
        time.sleep(1.5)

        def get(path, referer=None):
            cmd = ["docker", "exec", name, "wget", "-S", "-O", "/dev/null", "-T", "5"]
            if referer:
                cmd += ["--header", f"Referer: {referer}"]
            r = subprocess.run(cmd + [f"http://127.0.0.1{path}"], capture_output=True, text=True)
            return r.stderr                                   # busybox wget prints response headers on stderr

        landing = get(f"/?state={FAKE['state']}&iss=https://accounts.google.com&code={FAKE['code']}&scope=gmail.readonly")
        get("/missing-asset-for-test.svg", referer=f"https://autosparefinder.co.il/?state={FAKE['state']}&code={FAKE['ref_code']}")
        get(f"/?access_token={FAKE['access_token']}&x=1")
        # several sensitive parameters in one URL, and the same parameter twice
        get("/?access_token=ya29.FAKEmultiA&refresh_token=1//FAKEmultiB&id_token=FAKEmultiC&x=1")
        get("/?code=4/0FAKEdupA&code=4/0FAKEdupB&lang=he", referer="https://autosparefinder.co.il/?code=4/0FAKErefDupA&code=4/0FAKErefDupB")
        normal = get("/plain-page?lang=he")
        time.sleep(0.5)
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        out = logs.stdout + logs.stderr
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    for value in list(FAKE.values()) + ["FAKEmulti", "FAKEdup", "FAKErefDup"]:
        assert value not in out, f"a fake OAuth value reached the nginx logs: {value}"
    assert '"GET /?access_token=<redacted>&refresh_token=<redacted>&id_token=<redacted>&x=1 HTTP/1.1"' in out
    assert '"GET /?<query withheld> HTTP/1.1"' in out and "/index.html?<query withheld>" not in out and '"https://autosparefinder.co.il/?<query withheld>"' in out
    assert out.count("<redacted>") >= 5
    assert re.search(r'"GET /\?state=<redacted>&iss=https://accounts.google.com&code=<redacted>&scope=gmail.readonly HTTP/1.1"', out)
    assert '"https://autosparefinder.co.il/?state=<redacted>&code=<redacted>"' in out          # referer redacted, kept
    assert '"GET /plain-page?lang=he HTTP/1.1"' in out                                            # normal requests unchanged
    assert "missing-asset-for-test.svg" in out and "open()" not in out                            # 404 logged, no error line
    assert re.search(r"Referrer-Policy: no-referrer\b", landing, re.I)
    assert re.search(r"Referrer-Policy: strict-origin-when-cross-origin", normal, re.I)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                failures += 1
                print("FAIL", name, "-", e)
    sys.exit(1 if failures else 0)
