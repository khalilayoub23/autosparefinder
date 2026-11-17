"""2026-11-17 final-relay reliability: every silent path found by the harvester audit is closed.
 1 refused final chunk -> not 'done' (test_cpie_harvester_clearance)   2 importer failure reaches the harvester
 3 'done' response is validated   4 chunks are durable (survive a backend restart)   5 per-run logs, never overwritten
 6 retries are idempotent."""
import json
import sys
import pytest

import relay_store as rs
import harvesters.car_parts_ie_flaresolverr_harvester as h


@pytest.fixture(autouse=True)
def relay_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "RELAY_DIR", tmp_path / "relay")
    yield


def test_chunks_are_durable_and_done_needs_every_chunk():
    rs.store_chunk("renault_x", "r1", 0, [{"sku": "a"}, {"sku": "b"}])
    rs.store_chunk("renault_x", "r1", 2, [{"sku": "d"}])
    with pytest.raises(rs.RelayIncomplete) as e:                    # chunk 1 never arrived -> 409 with the seq to re-send
        rs.missing_or_parts("renault_x", "r1", 3, 4)
    assert e.value.missing == [1]
    rs.store_chunk("renault_x", "r1", 1, [{"sku": "c"}])
    # "restart": nothing is held in process memory - a fresh read sees all the data
    assert [p["sku"] for p in rs.missing_or_parts("renault_x", "r1", 3, 4)] == ["a", "b", "c", "d"]


def test_chunk_retry_is_idempotent_and_count_mismatch_is_refused():
    for _ in range(3):
        rs.store_chunk("t", "r", 0, [{"sku": "a"}])
    assert len(rs.missing_or_parts("t", "r", 1, 1)) == 1            # a re-sent chunk never duplicates parts
    with pytest.raises(rs.RelayIncomplete):
        rs.missing_or_parts("t", "r", 1, 2)                         # expected_parts mismatch -> never imported partial


def test_torn_chunk_counts_as_missing(tmp_path):
    rs.store_chunk("t", "r", 0, [{"sku": "a"}])
    (rs.RELAY_DIR / "chunks" / "t__r" / "00001.json").write_text("{not json")
    with pytest.raises(rs.RelayIncomplete) as e:
        rs.missing_or_parts("t", "r", 2, 2)
    assert e.value.missing == [1]


def test_importer_exit_code_and_per_run_logs_reach_the_result():
    ok = rs.start_import([sys.executable, "-c", "print('imported')"], "t", "run-ok", rs.RELAY_DIR)
    fail = rs.start_import([sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"], "t", "run-bad", rs.RELAY_DIR)
    import time
    for _ in range(100):
        a, b = rs.import_state("t", "run-ok"), rs.import_state("t", "run-bad")
        if a["state"] != "running" and b["state"] != "running":
            break
        time.sleep(0.1)
    assert a["state"] == "ok" and b["state"] == "failed" and b["exit_code"] == 3 and "boom" in b["tail"]
    assert ok["log"] != fail["log"]                                 # one log per run, not a shared overwritten file
    again = rs.start_import([sys.executable, "-c", "print('x')"], "t", "run-ok", rs.RELAY_DIR)
    assert again["state"] == "ok"                                   # a repeated 'done' does not launch a second import


# ---------------- harvester side ----------------
def _patch(monkeypatch, replies, status):
    it = iter(replies)
    monkeypatch.setattr(h, "_relay_json", lambda *a, **k: next(it))
    monkeypatch.setattr(h, "_import_status", lambda *a, **k: status.pop(0) if len(status) > 1 else status[0])
    monkeypatch.setattr(h.time, "sleep", lambda s: None)


def test_done_refused_is_not_done(monkeypatch):
    _patch(monkeypatch, [(500, None)], [{"state": "ok"}])
    with pytest.raises(h.HarvestFetchFailed):
        h.relay_done("a/b", "r", {0: [{"sku": "1"}]})
    _patch(monkeypatch, [(0, None)], [{"state": "ok"}])             # transport failure of the final POST: also not done
    with pytest.raises(h.HarvestFetchFailed):
        h.relay_done("a/b", "r", {0: [{"sku": "1"}]})


def test_incomplete_chunks_are_resent_then_importer_waited_for(monkeypatch):
    sent = []
    monkeypatch.setattr(h, "post_relay", lambda v, p, done=False, run_id="", seq=None: sent.append(seq) or True)
    _patch(monkeypatch, [(409, {"status": "incomplete", "missing": [1]}), (200, {"status": "saved"})],
           [{"state": "running"}, {"state": "ok"}])
    h.relay_done("a/b", "r", {0: [{"sku": "1"}], 1: [{"sku": "2"}]})
    assert sent == [1]


def test_importer_failure_or_timeout_releases_the_model(monkeypatch):
    _patch(monkeypatch, [(200, {"status": "saved"})], [{"state": "failed", "exit_code": 1, "log": "/x"}])
    with pytest.raises(h.HarvestFetchFailed, match="FAILED"):
        h.relay_done("a/b", "r", {0: [{"sku": "1"}]})
    _patch(monkeypatch, [(200, {"status": "saved"})], [{"state": "running"}])
    monkeypatch.setattr(h, "IMPORT_WAIT_S", -1)
    with pytest.raises(h.HarvestFetchFailed, match="did not finish"):
        h.relay_done("a/b", "r", {0: [{"sku": "1"}]})


def test_empty_model_is_accepted(monkeypatch):
    _patch(monkeypatch, [(200, {"status": "empty"})], [{"state": "ok"}])
    h.relay_done("a/b", "r", {})


def test_chunk_ack_must_match_run_and_seq(monkeypatch):
    monkeypatch.setattr(h, "_relay_json", lambda *a, **k: (200, {"status": "ok", "seq": 9}))
    assert h.post_relay("a/b", [{"sku": "1"}], run_id="r", seq=0) is False
    monkeypatch.setattr(h, "_relay_json", lambda *a, **k: (200, {"status": "ok", "seq": 0}))
    assert h.post_relay("a/b", [{"sku": "1"}], run_id="r", seq=0) is True
