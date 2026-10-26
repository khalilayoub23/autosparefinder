"""
Script: tests/test_nginx_upstream.py
Purpose: Regression guard for FIXES_TRACKER #64 — nginx must follow the backend container
         to a new address without a manual reload. A plain `server backend:8000;` inside an
         upstream block is resolved once at config load (the `resolver` directive does not
         apply to it); after a backend recreate nginx kept proxying to the old address and
         the public API returned 502.
Method: static check of deploy/nginx.conf. deploy/ is not mounted in the backend container,
        so the test skips there and runs on the host (python3 backend/tests/test_nginx_upstream.py).
Last Updated: 2026-10-04
"""
from __future__ import annotations

import os
import re

CONF = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "deploy", "nginx.conf")


def _conf() -> str:
    if not os.path.isfile(CONF):
        import pytest
        pytest.skip("deploy/nginx.conf is not available in this environment (host-side test)")
    return re.sub(r"#[^\n]*", "", open(CONF, encoding="utf-8").read())


def test_backend_upstream_is_re_resolved_at_runtime():
    conf = _conf()
    blocks = re.findall(r"upstream\s+(\w+)\s*\{([^}]*)\}", conf)
    assert [n for n, _ in blocks] == ["backend"]
    body = blocks[0][1]
    servers = re.findall(r"server\s+([^;]+);", body)
    assert servers and all(re.search(r"\bresolve\b", s) for s in servers), servers   # runtime DNS
    assert re.search(r"\bzone\s+\w+\s+\d+k;", body)                                    # required by `resolve`


def test_a_resolver_with_a_short_validity_is_configured():
    m = re.search(r"\bresolver\s+127\.0\.0\.11\b[^;]*\bvalid=(\d+)s", _conf())
    assert m and int(m.group(1)) <= 30


def test_no_proxy_pass_bypasses_the_upstream_with_a_literal_container_address():
    conf = _conf()
    targets = re.findall(r"proxy_pass\s+http://([^/;\s]+)", conf)
    assert targets and set(targets) == {"backend"}, set(targets)
    assert not re.search(r"proxy_pass\s+http://172\.", conf)


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1; print("PASS", name)
    print(f"{n} passed")
