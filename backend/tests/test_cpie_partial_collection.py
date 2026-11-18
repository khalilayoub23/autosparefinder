"""2026-11-18 CORE closure: a collection with pages that still fail after a retry is PARTIAL, never 'done'; every failed URL is
reported; a stale cf_clearance cookie fails closed instead of being used silently."""
import time

import pytest

import harvesters.car_parts_ie_flaresolverr_harvester as h


def _stub(monkeypatch, fail_paths, retry_ok=()):
    """fs_get stub: pages in fail_paths fail (blocked) on the first call; those in retry_ok succeed on the retry."""
    calls = {}
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "BASE_SLUGS", ["a", "b", "c"])
    monkeypatch.setattr(h, "get_additional_slugs", lambda *a, **k: [])
    monkeypatch.setattr(h, "ensure_clearance", lambda force=False: True)
    monkeypatch.setattr(h, "post_relay", lambda *a, **k: True)
    monkeypatch.setattr(h, "relay_done", lambda *a, **k: None)
    monkeypatch.setattr(h, "parse_parts", lambda html, path: [{"sku": path}])

    def fs(url, session_id="", timeout_ms=20000):
        path = url.replace(h.BASE, "")
        calls[path] = calls.get(path, 0) + 1
        if path in fail_paths and not (path in retry_ok and calls[path] > 1):
            h._fetch_outcome("blocked"); h._log_fetch_failure(url, "TimeoutError"); return ""
        h._fetch_outcome("ok")
        return "<html>" if "/car-parts/" in path else ""
    monkeypatch.setattr(h, "fs_get", fs)
    return calls


def test_transient_failure_is_retried_and_the_model_is_complete(monkeypatch):
    p = "/car-parts/talbot/express/b"
    calls = _stub(monkeypatch, {p}, retry_ok={p})
    n = h.harvest_model("", "talbot/express", {})
    assert n == 3 and calls[p] == 2                 # the failed page was fetched again and its parts delivered
    assert h._FETCH_TL.partial == []                # nothing missing -> may be recorded 'done'


def test_persistent_failure_is_partial_and_listed(monkeypatch, caplog):
    p = "/car-parts/talbot/express/b"
    _stub(monkeypatch, {p})
    with caplog.at_level("WARNING"):
        n = h.harvest_model("", "talbot/express", {})
    assert n == 2 and h._FETCH_TL.partial == [p]    # delivered what exists, but is NOT complete
    text = caplog.text
    assert "PARTIAL collection" in text and p in text
    assert "failed fetch (TimeoutError)" in text    # every failed URL is logged, not one per reason per process


def test_worker_records_partial_not_done(monkeypatch):
    got = []
    monkeypatch.setattr(h, "claim_next_model", lambda: ("q1", "talbot/express"))
    monkeypatch.setattr(h, "complete_model", lambda q, n, partial=False: got.append((q, n, partial)))
    monkeypatch.setattr(h, "release_model", lambda q: None)
    monkeypatch.setattr(h, "INTER_MODEL", 0)
    monkeypatch.setattr(h, "save_state", lambda s: None)

    def fake(session, vehicle, state):
        h._FETCH_TL.partial = ["/car-parts/talbot/express/b"]
        return 5
    monkeypatch.setattr(h, "harvest_model", fake)
    h.worker(1, 1, {"runs": []})
    assert got == [("q1", 5, True)]


def test_complete_model_status_mapping(monkeypatch):
    executed = []

    class Cur:
        def __enter__(s): return s
        def __exit__(s, *a): return False
        def execute(s, sql, params): executed.append(params)
    class Conn:
        autocommit = True
        def cursor(s): return Cur()
        def close(s): pass
    monkeypatch.setattr(h, "_db_conn", lambda: Conn())
    h.complete_model("q", 5, partial=True); h.complete_model("q", 5); h.complete_model("q", 0, partial=True)
    assert [e[0] for e in executed] == ["partial", "done", "empty"]


def test_stale_cookie_fails_closed(monkeypatch):
    monkeypatch.setattr(h, "_FETCH_TL", type("T", (), {})())
    h._FETCH_TL.c = {"ok": 0, "blocked": 0}; h._FETCH_TL.fails = []
    monkeypatch.setattr(h, "ensure_clearance", lambda force=False: False)   # re-mint keeps failing
    monkeypatch.setattr(h.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("used a stale cookie")))
    with h._CLEARANCE_LOCK:
        h._CLEARANCE.update(cookie="cf_clearance=old", ts=time.time() - h.CLEARANCE_HARD_MAX_S - 60, mode="cookie", session_id=None)
    assert h.http_get("https://www.car-parts.ie/x") == ""
    assert h._FETCH_TL.c["blocked"] == 1 and "too old" in h._FETCH_TL.fails[0][1]


def test_clearance_age_reported():
    with h._CLEARANCE_LOCK:
        h._CLEARANCE.update(cookie="cf_clearance=x", ts=time.time() - 100)
    assert 99 <= h.clearance_age_s() <= 110
    with h._CLEARANCE_LOCK:
        h._CLEARANCE.update(cookie="", ts=0.0, session_id=None)
    assert h.clearance_age_s() == float("inf")
