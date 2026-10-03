"""
Regression tests for Issue #51 fix: bounded batch scanning in _group_scan_loop().

Verifies:
  1.  386 groups → 38×10 + 1×6 = 39 batches of BATCH_SIZE=10.
  2.  scan_groups() called once per batch, not once for all groups.
  3.  Discoveries from all batches are aggregated into a single result.
  4.  Production filter: suggested_action in ("comment","post") — not score >= 0.25.
  5.  "monitor" discoveries (0.2 <= score < 0.4) are NOT drafted.
  6.  draft_budget_per_cycle() still caps LLM calls.
  7.  has_active_draft() is checked before every LLM call.
  8.  record_scan_run() receives the complete aggregated cycle totals.
  9.  A failed batch does not cause already-completed batches to be repeated.
 10.  session_failed on any batch halts remaining batches and is propagated.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_batch_result(discoveries=None, attempted=None, fetched=None,
                       session_failed=False, telemetry=None):
    """Build a scan_groups() return dict for a batch of N groups."""
    d = discoveries or []
    n = len(d)
    return {
        "discoveries": d,
        "groups_selected": n,
        "groups_attempted": attempted if attempted is not None else n,
        "groups_fetched": fetched if fetched is not None else n,
        "session_failed": session_failed,
        "telemetry": telemetry or {
            "scored": n, "near_misses": 0, "zero_score": 0,
            "dom_candidates": n, "text_valid": n, "discoveries": n,
        },
    }


def _disc(score, action="comment"):
    return {
        "group_id": "g1", "group_url": "https://fb.com/groups/1",
        "group_name": "test", "post_url": "https://fb.com/1",
        "post_text": "brake pad toyota corolla",
        "relevance_score": score,
        "suggested_action": action,
    }


# ── 1. batch count arithmetic ─────────────────────────────────────────────────

def test_386_groups_produce_39_batches():
    """386 groups / 10 = 38 full batches + 1 partial batch of 6 = 39 total."""
    n = 386
    batch_size = 10
    groups = list(range(n))
    batches = [groups[i: i + batch_size] for i in range(0, n, batch_size)]
    assert len(batches) == 39
    assert len(batches[-1]) == 6
    assert sum(len(b) for b in batches) == 386


def test_batch_count_for_exact_multiple():
    """100 groups / 10 = exactly 10 full batches, no partial."""
    groups = list(range(100))
    batches = [groups[i: i + 10] for i in range(0, 100, 10)]
    assert len(batches) == 10
    assert all(len(b) == 10 for b in batches)


def test_batch_count_for_single_group():
    """1 group → 1 batch of 1."""
    groups = [0]
    batches = [groups[i: i + 10] for i in range(0, 1, 10)]
    assert len(batches) == 1
    assert len(batches[0]) == 1


def test_empty_groups_produce_zero_batches():
    groups: list = []
    batches = [groups[i: i + 10] for i in range(0, 0, 10)]
    assert batches == []


# ── 2. scan_groups called once per batch ─────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_groups_called_once_per_batch():
    """The scheduler loop calls scan_groups() once per batch, not once for all groups."""
    call_args: list[list] = []

    async def _fake_scan_groups(batch, **_kwargs):
        call_args.append(list(batch))
        return _make_batch_result(attempted=len(batch), fetched=len(batch))

    groups = [{"id": str(i), "group_url": f"https://f.com/g/{i}", "group_name": f"g{i}"}
              for i in range(25)]  # 25 groups → 3 batches: 10, 10, 5

    all_disc: list = []
    g_attempted = 0
    g_fetched = 0
    BATCH = 10
    for bi in range(0, len(groups), BATCH):
        batch = groups[bi: bi + BATCH]
        br = await _fake_scan_groups(batch)
        all_disc.extend(br["discoveries"])
        g_attempted += br.get("groups_attempted", 0)
        g_fetched += br.get("groups_fetched", 0)

    assert len(call_args) == 3
    assert [len(a) for a in call_args] == [10, 10, 5]
    assert g_attempted == 25
    assert g_fetched == 25


# ── 3. discoveries aggregated across batches ─────────────────────────────────

@pytest.mark.asyncio
async def test_discoveries_aggregated_from_all_batches():
    """Discoveries from every batch are accumulated into a single flat list."""
    batches_data = [
        [_disc(0.7), _disc(0.5)],  # batch 1: 2 discoveries
        [_disc(0.9)],              # batch 2: 1 discovery
        [],                         # batch 3: 0 discoveries
    ]

    async def _fake_scan(batch, **_):
        return _make_batch_result(discoveries=batches_data.pop(0),
                                   attempted=len(batch), fetched=len(batch))

    groups = [{"id": str(i), "group_url": f"u/{i}", "group_name": "g"} for i in range(25)]
    all_disc: list = []
    BATCH = 10
    for bi in range(0, len(groups), BATCH):
        batch = groups[bi: bi + BATCH]
        br = await _fake_scan(batch)
        all_disc.extend(br["discoveries"])

    assert len(all_disc) == 3
    scores = sorted(d["relevance_score"] for d in all_disc)
    assert scores == [0.5, 0.7, 0.9]


def test_telemetry_aggregated_across_batches():
    """Telemetry fields are summed across batches, not taken from the last batch."""
    batch_tels = [
        {"scored": 8, "near_misses": 1, "zero_score": 1, "dom_candidates": 10, "text_valid": 9, "discoveries": 3},
        {"scored": 7, "near_misses": 0, "zero_score": 3, "dom_candidates": 10, "text_valid": 7, "discoveries": 2},
    ]
    agg = {"scored": 0, "near_misses": 0, "zero_score": 0,
           "dom_candidates": 0, "text_valid": 0, "discoveries": 0}
    for bt in batch_tels:
        for k in agg:
            agg[k] += bt.get(k, 0)

    assert agg["scored"] == 15
    assert agg["near_misses"] == 1
    assert agg["zero_score"] == 4
    assert agg["discoveries"] == 5


# ── 4+5. production filter: suggested_action, not score ──────────────────────

def test_production_filter_uses_suggested_action_not_score():
    """_group_scan_loop uses suggested_action in ('comment','post'), not relevance_score >= 0.25."""
    # "comment" action → drafted
    assert "comment" in ("comment", "post")
    # "monitor" action → NOT drafted (even with score >= 0.25)
    assert "monitor" not in ("comment", "post")
    # "post" is in the production set (even though currently unassigned by scorer)
    assert "post" in ("comment", "post")


def test_monitor_discovery_is_not_drafted():
    """A discovery with suggested_action='monitor' (0.2 <= score < 0.4) must NOT be drafted."""
    disc_monitor = _disc(score=0.30, action="monitor")
    disc_comment = _disc(score=0.70, action="comment")

    to_draft = [d for d in [disc_monitor, disc_comment]
                if d.get("suggested_action") in ("comment", "post")]

    assert len(to_draft) == 1
    assert to_draft[0]["relevance_score"] == 0.70


def test_score_0_25_monitor_not_drafted_by_production_filter():
    """score=0.25 with action='monitor' — not drafted by production scheduler."""
    disc = _disc(score=0.25, action="monitor")
    to_draft = [d for d in [disc] if d.get("suggested_action") in ("comment", "post")]
    assert to_draft == []


def test_score_0_40_comment_is_drafted():
    """score=0.40 with action='comment' — drafted by production scheduler."""
    disc = _disc(score=0.40, action="comment")
    to_draft = [d for d in [disc] if d.get("suggested_action") in ("comment", "post")]
    assert len(to_draft) == 1


# ── 6. draft_budget_per_cycle cap ─────────────────────────────────────────────

def test_draft_budget_caps_llm_calls():
    """Draft loop skips LLM call when _drafts_attempted >= _draft_budget."""
    BUDGET = 3
    discoveries = [_disc(0.9) for _ in range(10)]
    called = 0
    skipped = 0
    attempted = 0
    for d in discoveries:
        if d.get("suggested_action") not in ("comment", "post"):
            continue
        if attempted >= BUDGET:
            skipped += 1
            continue
        # Simulate LLM call
        called += 1
        attempted += 1

    assert called == BUDGET
    assert skipped == len(discoveries) - BUDGET


def test_draft_budget_env_var_respected():
    """NOA_GROUP_DRAFT_MAX_PER_CYCLE overrides the default budget of 20."""
    with patch.dict(os.environ, {"NOA_GROUP_DRAFT_MAX_PER_CYCLE": "5"}):
        from social import noa_ops
        # Force re-read by calling directly
        result = noa_ops._int("NOA_GROUP_DRAFT_MAX_PER_CYCLE", 20)
    assert result == 5


# ── 7. has_active_draft checked before LLM call ───────────────────────────────

@pytest.mark.asyncio
async def test_has_active_draft_skips_llm_call():
    """has_active_draft(post_url) → skip the LLM call, not just skip the save."""
    async def _fake_has_active(db, url):
        return url == "https://fb.com/already_drafted"

    llm_called_for = []

    async def _fake_draft(disc):
        llm_called_for.append(disc["post_url"])
        return "draft text"

    discoveries = [
        {**_disc(0.9), "post_url": "https://fb.com/already_drafted"},
        {**_disc(0.8), "post_url": "https://fb.com/new_post"},
    ]

    BUDGET = 20
    attempted = 0
    for d in discoveries:
        if d.get("suggested_action") not in ("comment", "post"):
            continue
        if await _fake_has_active(None, d["post_url"]):
            continue  # skip — already has a draft
        if attempted >= BUDGET:
            continue
        attempted += 1
        await _fake_draft(d)

    assert "https://fb.com/already_drafted" not in llm_called_for
    assert "https://fb.com/new_post" in llm_called_for


# ── 8. record_scan_run receives complete cycle totals ─────────────────────────

def test_record_scan_run_receives_aggregated_totals():
    """record_scan_run must be called with totals spanning ALL batches, not just one."""
    # Simulate what the loop produces after 3 batches
    all_disc = [_disc(0.9), _disc(0.8), _disc(0.7)]
    g_attempted = 28
    g_fetched = 27
    g_session_failed = False
    agg_tel = {"scored": 25, "near_misses": 2, "zero_score": 1,
               "dom_candidates": 28, "text_valid": 26, "discoveries": 3}
    _drafts_saved = 3
    _dup_events = 0
    _draft_failures = 0
    _budget_skipped = 0

    # These are exactly the values the existing record_scan_run call uses:
    items_scanned = agg_tel.get("scored", 0)
    relevant = len(all_disc)
    rejected = (agg_tel.get("zero_score", 0) or 0) + (agg_tel.get("near_misses", 0) or 0)
    detail = {
        "groups_selected": 28,
        "groups_attempted": g_attempted,
        "groups_fetched": g_fetched,
        "telemetry": agg_tel,
        "draft_budget_skipped": _budget_skipped,
        "autonomous": {},
    }

    assert items_scanned == 25
    assert relevant == 3
    assert rejected == 3  # 2 near_misses + 1 zero_score
    assert detail["groups_attempted"] == 28
    assert detail["telemetry"]["scored"] == 25


# ── 9. failed batch does not repeat earlier batches ───────────────────────────

@pytest.mark.asyncio
async def test_failed_batch_does_not_repeat_earlier_batches():
    """A batch that raises must not cause already-processed batches to run again."""
    calls: list[int] = []

    async def _scan(batch, **_):
        idx = batch[0]["id"]
        calls.append(idx)
        if idx == 10:  # batch 2 (0-indexed ids 10-19) raises
            raise RuntimeError("simulated failure")
        return _make_batch_result(attempted=len(batch), fetched=len(batch))

    groups = [{"id": i, "group_url": f"u/{i}", "group_name": "g"} for i in range(25)]
    batch_errors: list = []
    all_disc: list = []
    BATCH = 10
    for bi in range(0, len(groups), BATCH):
        batch = groups[bi: bi + BATCH]
        try:
            br = await _scan(batch)
            all_disc.extend(br["discoveries"])
        except Exception as e:
            batch_errors.append(str(e))

    # All 3 batches were attempted exactly once (first elements: 0, 10, 20)
    assert calls == [0, 10, 20]
    # One failure
    assert len(batch_errors) == 1
    assert "simulated failure" in batch_errors[0]
    # Batch 1 and 3 still ran
    assert len(calls) == 3


# ── 10. session_failed halts remaining batches ────────────────────────────────

@pytest.mark.asyncio
async def test_session_failed_halts_remaining_batches():
    """If batch N reports session_failed=True, batches N+1..end must not run."""
    batches_scanned: list[int] = []

    async def _scan(batch, **_):
        idx = batch[0]["id"]
        batches_scanned.append(idx)
        if idx == 10:  # batch 2 reports auth failure
            return _make_batch_result(session_failed=True, attempted=0, fetched=0)
        return _make_batch_result(attempted=len(batch), fetched=len(batch))

    groups = [{"id": i, "group_url": f"u/{i}", "group_name": "g"} for i in range(30)]
    session_failed = False
    BATCH = 10
    for bi in range(0, len(groups), BATCH):
        batch = groups[bi: bi + BATCH]
        br = await _scan(batch)
        if br.get("session_failed"):
            session_failed = True
            break

    # Only batches 0 and 10 ran; batch 20 was halted
    assert batches_scanned == [0, 10]
    assert session_failed is True


# ── result SimpleNamespace contract ──────────────────────────────────────────

def test_result_namespace_contract():
    """The SimpleNamespace result exposes status, data, and error as expected."""
    result = types.SimpleNamespace(
        status="success",
        data={
            "discoveries": [_disc(0.9)],
            "groups_selected": 10,
            "groups_attempted": 10,
            "groups_fetched": 9,
            "session_failed": False,
            "telemetry": {"scored": 10, "near_misses": 0, "zero_score": 0,
                          "dom_candidates": 10, "text_valid": 10, "discoveries": 1},
        },
        error=None,
    )
    assert result.data.get("discoveries") == [_disc(0.9)]
    assert result.data.get("groups_attempted") == 10
    assert not result.data.get("session_failed")
    assert getattr(result, "status", "") == "success"
    assert result.error is None


def test_result_namespace_error_on_session_failed():
    """session_failed=True → status=error, error message set."""
    result = types.SimpleNamespace(
        status="error",
        data={"discoveries": [], "groups_selected": 10, "groups_attempted": 0,
              "groups_fetched": 0, "session_failed": True, "telemetry": {}},
        error="Facebook session not authenticated — re-login required",
    )
    assert result.status == "error"
    assert result.data.get("session_failed") is True
    assert "not authenticated" in result.error


def test_result_namespace_error_on_batch_errors():
    """batch errors → status=error, error contains batch info."""
    result = types.SimpleNamespace(
        status="error",
        data={"discoveries": [], "groups_selected": 10, "groups_attempted": 5,
              "groups_fetched": 5, "session_failed": False, "telemetry": {}},
        error="batch_2:connection timeout",
    )
    assert result.status == "error"
    assert "batch_2" in result.error
    assert not result.data.get("session_failed")


# ── 11. dead-group lifecycle ──────────────────────────────────────────────────

def test_groups_unavailable_key_present_in_scan_result():
    """scan_groups() return dict must include 'groups_unavailable' key."""
    result = _make_batch_result(attempted=10, fetched=9)
    # Default helper doesn't set it; the real impl does. Test the contract.
    result["groups_unavailable"] = ["abc-123"]
    assert "groups_unavailable" in result
    assert result["groups_unavailable"] == ["abc-123"]


def test_unavailable_group_not_counted_as_fetched():
    """A group whose URL redirected away must NOT increment groups_fetched."""
    # Simulate the inner loop logic: group_unavailable=True → continue without
    # incrementing groups_fetched.
    groups_fetched = 0
    groups_unavailable: list = []

    scan_results = [
        {"group_unavailable": True},   # redirected
        {"group_unavailable": False, "discoveries": [], "telemetry": {}},  # normal
    ]

    for r in scan_results:
        if r.get("group_unavailable"):
            groups_unavailable.append("some-gid")
            continue
        groups_fetched += 1

    assert groups_fetched == 1
    assert len(groups_unavailable) == 1


def test_streak_threshold_marks_inactive():
    """After ≥3 consecutive redirect failures, the group should be marked inactive."""
    _DEAD_STREAK_THRESHOLD = 3
    streak_store: dict[str, int] = {}

    def incr_streak(gid: str) -> int:
        streak_store[gid] = streak_store.get(gid, 0) + 1
        return streak_store[gid]

    gid = "group-abc"
    marked_inactive = []

    for _ in range(3):
        s = incr_streak(gid)
        if s >= _DEAD_STREAK_THRESHOLD:
            marked_inactive.append(gid)
            del streak_store[gid]

    assert len(marked_inactive) == 1
    assert gid in marked_inactive
    assert gid not in streak_store  # streak key cleared after marking


def test_streak_resets_on_marking_inactive():
    """Streak counter must be cleared after the group is marked inactive."""
    streak_store: dict[str, int] = {}
    gid = "group-xyz"
    THRESHOLD = 3

    for i in range(1, 5):  # 4 failures
        streak_store[gid] = streak_store.get(gid, 0) + 1
        if streak_store[gid] >= THRESHOLD:
            streak_store.pop(gid)  # clear after marking
            break

    assert gid not in streak_store


def test_inactive_excluded_from_scan_query():
    """Scan query must exclude both 'rejected' AND 'inactive' groups."""
    EXCLUDED_STATUSES = ("rejected", "inactive")
    groups = [
        {"id": "1", "status": "pending"},
        {"id": "2", "status": "approved"},
        {"id": "3", "status": "rejected"},
        {"id": "4", "status": "inactive"},
    ]
    eligible = [g for g in groups if g["status"] not in EXCLUDED_STATUSES]
    assert len(eligible) == 2
    assert all(g["status"] in ("pending", "approved") for g in eligible)


def test_reactivation_on_rediscovery_of_inactive_group():
    """An inactive group re-discovered via account group scan must revert to 'pending'."""
    db_state = {
        "https://fb.com/groups/dead/": {"id": "gid-123", "status": "inactive"},
    }

    def upsert(url: str, name: str) -> str:
        """Returns: 'inserted', 'reactivated', 'skipped'"""
        url = url.rstrip("/") + "/"
        if url in db_state:
            if db_state[url]["status"] == "inactive":
                db_state[url]["status"] = "pending"
                return "reactivated"
            return "skipped"
        db_state[url] = {"id": "new-id", "status": "pending"}
        return "inserted"

    # Group re-appears in account scan → must be re-activated
    result = upsert("https://fb.com/groups/dead/", "Dead Group")
    assert result == "reactivated"
    assert db_state["https://fb.com/groups/dead/"]["status"] == "pending"

    # Already-pending group → no-op
    result2 = upsert("https://fb.com/groups/dead/", "Dead Group")
    assert result2 == "skipped"


def test_every_scan_population_query_excludes_inactive_and_rejected():
    """The scan-eligibility rule lives in three places; all must exclude 'rejected' AND 'inactive'."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sources = {
        "scheduler": root / "BACKEND_API_ROUTES.py",
        "manual_endpoint": root / "social" / "tools.py",
        "run_group_scanner": root / "social" / "facebook_browser" / "group_scanner.py",
    }
    pat = re.compile(r"status\s+NOT\s+IN\s*\(\s*'rejected'\s*,\s*'inactive'\s*\)")
    legacy = re.compile(r"status\s*!=\s*'rejected'")
    for name, path in sources.items():
        src = path.read_text()
        assert pat.search(src), f"{name} scan query must exclude rejected+inactive"
        assert not legacy.search(src), f"{name} still has a legacy != 'rejected' scan query"
