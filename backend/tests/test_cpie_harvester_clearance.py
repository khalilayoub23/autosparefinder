"""Harvester Cloudflare-clearance + failed-collection safety (2026-11-17).
Concurrent processes share one cookie; idle never mints; expiry refreshes; solve failures are logged and surfaced;
a model whose fetches all failed is RELEASED to pending, never recorded 'empty'; 404 stays a legitimate empty."""
import json, logging, multiprocessing as mp, os, stat, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "harvesters"))
import car_parts_ie_flaresolverr_harvester as h


def _reset(tmp_path):
    h._CLEARANCE_CACHE_FILE = tmp_path / "c.json"
    h._CLEARANCE.update(cookie="", ua=h._DEFAULT_UA, ts=0.0, session_id=None, mode="cookie")
    h._CLEARANCE_FAILED_TS = 0.0


def _proc(cache, counter, q):
    h._CLEARANCE_CACHE_FILE = __import__("pathlib").Path(cache)
    h._CLEARANCE.update(cookie="", ts=0.0, session_id=None, mode="cookie")
    h._CLEARANCE_FAILED_TS = 0.0

    def fake_solve():
        with open(counter, "a") as f:
            f.write("x")
        time.sleep(1.5)
        with h._CLEARANCE_LOCK:
            h._CLEARANCE.update(cookie="cf_clearance=zzz; a=b", ua="UA", ts=time.time(), mode="cookie")
        h._save_clearance_cache()
        return True
    h._solve_clearance = fake_solve
    q.put(h.ensure_clearance())


def test_concurrent_processes_share_one_mint(tmp_path):
    cache, counter = str(tmp_path / "c.json"), str(tmp_path / "n")
    q = mp.get_context("fork").Queue()
    ps = [mp.get_context("fork").Process(target=_proc, args=(cache, counter, q)) for _ in range(3)]
    [p.start() for p in ps]; [p.join(60) for p in ps]
    assert [q.get(timeout=5) for _ in ps] == [True, True, True]
    assert len(open(counter).read()) == 1            # exactly ONE solve for three processes
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o600


def test_idle_queue_never_mints(tmp_path, monkeypatch):
    _reset(tmp_path)
    monkeypatch.setattr(h, "_solve_clearance", lambda: (_ for _ in ()).throw(AssertionError("minted while idle")))
    assert h.maybe_mint(0) is True


def test_expired_cookie_triggers_one_controlled_refresh(tmp_path, monkeypatch):
    _reset(tmp_path)
    h._CLEARANCE.update(cookie="cf_clearance=old", ts=time.time() - h.CLEARANCE_TTL_S - 5)
    calls = []
    def solve():
        calls.append(1); h._CLEARANCE.update(cookie="cf_clearance=new", ts=time.time()); return True
    monkeypatch.setattr(h, "_solve_clearance", solve)
    assert h.maybe_mint(5) is True and calls == [1] and h._CLEARANCE["cookie"] == "cf_clearance=new"
    assert h.maybe_mint(5) is True and calls == [1]  # fresh now: no second solve


def test_solve_timeout_is_logged_and_surfaced(tmp_path, monkeypatch, caplog):
    _reset(tmp_path)
    monkeypatch.setattr(h, "FLARESOLVERR", "http://fs"); monkeypatch.setattr(h, "FLARESOLVERR_2", "")
    monkeypatch.setattr(h, "_fs_call", lambda host, payload: {"status": "error", "message": "Timeout after 60 seconds"})
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    with caplog.at_level(logging.WARNING):
        assert h.ensure_clearance(force=True) is False
    assert "FlareSolverr error solving clearance" in caplog.text and "could not mint" in caplog.text


def test_failed_collection_is_released_not_recorded_empty(monkeypatch):
    done, released = [], []
    monkeypatch.setattr(h, "claim_next_model", lambda: ("q1", "bmw/x5"))
    monkeypatch.setattr(h, "complete_model", lambda q, n, partial=False: done.append((q, n)))
    monkeypatch.setattr(h, "release_model", lambda q: released.append(q))
    monkeypatch.setattr(h, "INTER_MODEL", 0)
    def failing(sid, vehicle, state):
        h._FETCH_TL.c = {"ok": 0, "blocked": 7}
        raise h.HarvestFetchFailed("all blocked")
    monkeypatch.setattr(h, "harvest_model", failing)
    h.worker(1, 5, {"runs": []})
    assert released == ["q1"] and done == []


def test_all_pages_blocked_raises_but_404_is_a_legitimate_empty(tmp_path, monkeypatch):
    _reset(tmp_path)
    h._CLEARANCE.update(cookie="cf_clearance=ok", ts=time.time())
    import urllib.error
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "post_relay", lambda *a, **k: True)
    monkeypatch.setattr(h, "relay_done", lambda *a, **k: None)
    def op403(req, timeout=0):
        raise urllib.error.HTTPError("u", 403, "forbidden", {}, None)
    monkeypatch.setattr(h, "ensure_clearance", lambda force=False: False)
    monkeypatch.setattr(h.urllib.request, "urlopen", op403)
    try:
        h.harvest_model("", "bmw/x5", {}); assert False, "must raise"
    except h.HarvestFetchFailed:
        pass
    def op404(req, timeout=0):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    monkeypatch.setattr(h.urllib.request, "urlopen", op404)
    assert h.harvest_model("", "bmw/x5", {}) == 0     # definitive "no such page" => empty is correct


def test_transient_403_recovers_after_refresh(tmp_path, monkeypatch):
    _reset(tmp_path)
    h._CLEARANCE.update(cookie="cf_clearance=old", ts=time.time())
    import urllib.error
    n = {"c": 0}
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html>ok</html>"
    def op(req, timeout=0):
        n["c"] += 1
        if n["c"] == 1: raise urllib.error.HTTPError("u", 403, "f", {}, None)
        return R()
    monkeypatch.setattr(h.urllib.request, "urlopen", op)
    monkeypatch.setattr(h, "ensure_clearance", lambda force=False: h._CLEARANCE.update(cookie="cf_clearance=new") or True)
    h._FETCH_TL.c = {"ok": 0, "blocked": 0}
    assert h.http_get("https://www.car-parts.ie/x") == "<html>ok</html>" and h._FETCH_TL.c == {"ok": 1, "blocked": 0}


def test_blocked_page_or_undelivered_parts_with_nothing_sent_is_not_empty(tmp_path, monkeypatch):
    _reset(tmp_path)
    import urllib.error
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "post_relay", lambda *a, **k: False)            # relay refuses everything
    h._CLEARANCE.update(cookie="cf_clearance=ok", ts=time.time())
    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"<html></html>"
    calls = {"n": 0}
    def op(req, timeout=0):
        calls["n"] += 1
        if "/car-parts/" in req.full_url:          # category pages blocked, the model page itself loads
            raise urllib.error.HTTPError("u", 503, "x", {}, None)
        return R()
    monkeypatch.setattr(h, "ensure_clearance", lambda force=False: False)
    monkeypatch.setattr(h.urllib.request, "urlopen", op)
    try:
        h.harvest_model("", "bmw/x5", {}); assert False, "must raise"
    except h.HarvestFetchFailed:
        pass


def test_session_mode_empty_body_counts_as_blocked(monkeypatch):
    h._CLEARANCE.update(mode="session", session_id="s1")
    monkeypatch.setattr(h, "_fs_get_via_session", lambda u, s, t: "")
    h._FETCH_TL.c = {"ok": 0, "blocked": 0}
    assert h.http_get("https://www.car-parts.ie/x") == "" and h._FETCH_TL.c["blocked"] == 1
    h._CLEARANCE.update(mode="cookie", session_id=None)


def test_released_model_is_cooled_down_for_this_process(monkeypatch):
    h._RELEASED_AT.clear()
    monkeypatch.setattr(h, "_db_conn", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    h.release_model("q-fail")
    assert "q-fail" in h._cooling_ids()
    h._RELEASED_AT["q-fail"] = time.time() - h.RELEASE_COOLDOWN_S - 1
    assert "q-fail" not in h._cooling_ids()


def test_final_relay_refusal_after_partial_delivery_is_not_done(tmp_path, monkeypatch):
    _reset(tmp_path)
    h._CLEARANCE.update(cookie="cf_clearance=ok", ts=time.time())
    monkeypatch.setattr(h.time, "sleep", lambda s: None)
    monkeypatch.setattr(h, "BASE_SLUGS", ["a"])
    html = "<html></html>"
    monkeypatch.setattr(h, "http_get", lambda *a, **k: (setattr(h._FETCH_TL, "c", {"ok": 9, "blocked": 0}) or html))
    monkeypatch.setattr(h, "get_additional_slugs", lambda *a, **k: [])
    monkeypatch.setattr(h, "BATCH_SIZE", 2)
    monkeypatch.setattr(h, "parse_parts", lambda html, path: [{"sku": str(i)} for i in range(3)])
    calls = []
    def relay(v, b, done=False, **kw):
        calls.append(len(b)); return len(calls) == 1          # first chunk delivered, the final chunk refused
    monkeypatch.setattr(h, "post_relay", relay)
    try:
        h.harvest_model("", "bmw/x5", {}); assert False, "must raise"
    except h.HarvestFetchFailed as e:
        assert "final relay" in str(e)
