"""Mission 4 (2026-11-25): crash injection at every boundary of the digest send/checkpoint lifecycle.

`FakeBridge` mirrors the REAL contract of whatsapp-bridge/send_journal.js (journal keyed by client_msg_id, 'pending'
written before the wire, 'sent' after; the node tests in whatsapp-bridge/test_send_journal.mjs prove the journal itself).
`owner_visible` counts distinct provider message ids: it assumes WhatsApp drops a re-send of the SAME message id. That
assumption is NOT provable offline - `wire_sends` is the honest count of provider calls, and the one residual window
(bridge dies after WhatsApp accepted, before 'sent' is on disk) is asserted explicitly as wire_sends == 2, owner_visible == 1.
A 'crash' is a BaseException at a chosen Redis write / bridge step; the next Store is built on the same (persistent) Redis."""
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
for p in (BACKEND, Path(__file__).resolve().parent):
    sys.path.insert(0, str(p))

import owner_digest as od  # noqa: E402
from fake_async_redis import FakeAsyncRedis  # noqa: E402

MORNING = datetime(2026, 11, 25, 9, 40, tzinfo=od.TZ)
T = MORNING.timestamp()


class Crash(BaseException):
    """Process death: not an Exception, so nothing in the code under test can swallow it."""


class CrashRedis(FakeAsyncRedis):
    """Raises Crash ONCE when `trigger(op, key, value)` is true. State persists across 'restarts'."""
    trigger = None

    async def set(self, k, v, ex=None, nx=False):
        self._maybe("set", k, v)
        return await super().set(k, v, ex=ex, nx=nx)

    def _maybe(self, op, k, v):
        if self.trigger and self.trigger(op, k, v):
            self.trigger = None
            raise Crash(f"{op} {k}")


class FakeBridge:
    """journal: id -> 'pending'|'sent'. Survives restarts (it is the bridge's disk file)."""
    def __init__(self):
        self.journal, self.wire, self.up = {}, [], True
        self.die_after_wire = False      # bridge dies after WhatsApp accepted, before 'sent' is journalled
        self.die_before_wire = False

    async def send(self, text, client_msg_id=""):
        if not self.up:
            return {"ok": False, "error": "WhatsApp socket closed — reconnecting"}
        if self.journal.get(client_msg_id) == "sent":
            return {"ok": True, "key": {"id": client_msg_id}, "deduplicated": True}
        self.journal[client_msg_id] = "pending"
        if self.die_before_wire:
            self.die_before_wire = False
            raise Crash("bridge died before wire")
        self.wire.append((client_msg_id, text))
        if self.die_after_wire:
            self.die_after_wire = False
            raise Crash("bridge died after provider accepted")
        self.journal[client_msg_id] = "sent"
        return {"ok": True, "key": {"id": client_msg_id}, "deduplicated": False}

    @property
    def wire_sends(self):
        return len(self.wire)

    @property
    def owner_visible(self):
        return len({i for i, _ in self.wire})


class World:
    def __init__(self):
        self.r, self.bridge, self.t = CrashRedis(), FakeBridge(), T - 8 * 3600
        self.recorded = []

    def store(self):
        return od.Store(self.r, lambda: self.t)

    def rec(self, title, **kw):
        out = asyncio.run(self.store().record(category="health", title=title, body="b " + title, **kw))
        self.recorded.append(out["event_id"])
        return out

    def deliver(self, at=MORNING, **kw):
        self.t = at.timestamp()
        return asyncio.run(self.store().deliver(self.bridge.send, now_local=at, **kw))

    def restart(self):
        """Process death: the in-flight pass lock is left behind and expires (180 s) - model the expiry."""
        self.r.d.pop(od.K_SENDING, None)
        self.r.d.pop(od.K_LOCK, None)

    def events_accounted(self):
        """Every recorded identity is pending, frozen, or already checkpointed - never lost."""
        pend = {e["id"] for e in asyncio.run(self.store().pending())}
        fz = asyncio.run(self.store().frozen())
        frozen = {e["id"] for e in (fz or {}).get("events", [])}
        done = {e for raw in self.r.d.get(od.K_LOG, []) for e in json.loads(raw)["events"]}   # every checkpointed digest
        return set(self.recorded) <= pend | frozen | done


@pytest.fixture
def w():
    x = World()
    for i in range(4):
        x.rec(f"event {chr(65 + i)}")
    return x


def crash_on(w_, pred):
    w_.r.trigger = pred


def is_frozen_write(k, v):
    return k == od.K_FROZEN


# ── 1. before send ────────────────────────────────────────────────────────────────────────────────────────
def test_crash_before_send_loses_nothing_and_sends_once_after_restart(w):
    crash_on(w, lambda op, k, v: k == od.K_FROZEN and '"inflight"' in str(v))    # the write-ahead intent
    with pytest.raises(Crash):
        w.deliver()
    assert w.bridge.wire_sends == 0 and w.events_accounted()
    w.restart()
    res = w.deliver()
    assert res["ok"] and w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1
    assert asyncio.run(w.store().frozen()) is None and w.events_accounted()


# ── 2. during send ────────────────────────────────────────────────────────────────────────────────────────
def test_bridge_dies_before_the_wire_nothing_was_sent_then_exactly_one_send(w):
    w.bridge.die_before_wire = True
    with pytest.raises(Crash):
        w.deliver()
    assert w.bridge.wire_sends == 0
    w.restart()
    assert w.deliver()["ok"] and w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1


def test_RESIDUAL_bridge_dies_after_provider_accepted_resends_same_message_id(w):
    """The window no journal can close: WhatsApp accepted, the bridge died before writing 'sent'. The retry re-sends with
    the SAME message id (provider drops it). wire_sends == 2 is stated, not hidden; owner_visible == 1 relies on the
    provider-side id dedup, which cannot be proven offline."""
    w.bridge.die_after_wire = True
    with pytest.raises(Crash):
        w.deliver()
    assert w.bridge.wire_sends == 1
    w.restart()
    res = w.deliver()
    assert res["ok"]
    assert w.bridge.wire_sends == 2 and w.bridge.owner_visible == 1
    assert asyncio.run(w.store().status())["stats"].get("resend_after_unknown_outcome") == 1


# ── 3. provider accepted, acknowledgement not persisted ──────────────────────────────────────────────────
def test_crash_after_acceptance_before_ack_persisted_is_deduplicated_by_the_bridge(w):
    crash_on(w, lambda op, k, v: k == od.K_FROZEN and '"sent_parts": 1' in str(v))
    with pytest.raises(Crash):
        w.deliver()
    assert w.bridge.wire_sends == 1 and w.bridge.journal and w.events_accounted()
    # the dead pass left its lock behind: a second pass must wait for it to expire, not race
    w.r.d[od.K_SENDING] = "1"
    w.r.exp[od.K_SENDING] = 0
    res = w.deliver()
    assert res["ok"]
    assert w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1, "the retry must not reach the wire again"
    fz_last = json.loads(w.r.d[od.K_LAST])
    assert fz_last["acks"]["0"]["deduplicated"] is True


# ── 4. acknowledged, checkpoint not advanced ─────────────────────────────────────────────────────────────
def test_crash_after_ack_before_checkpoint_does_not_resend_and_checkpoints_once(w):
    crash_on(w, lambda op, k, v: k == od.K_LAST)
    with pytest.raises(Crash):
        w.deliver()
    assert w.bridge.wire_sends == 1
    fz = asyncio.run(w.store().frozen())
    assert fz and fz["sent_parts"] == len(fz["parts"])
    w.restart()
    res = w.deliver()
    assert res["ok"] and w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1
    stats = asyncio.run(w.store().status())["stats"]
    assert stats["digests_delivered"] == 1 and asyncio.run(w.store().frozen()) is None
    assert asyncio.run(w.store().r.get(od.NS + "seen:" + w.recorded[0])) is not None   # 'seen' memory written


def test_crash_in_the_middle_of_the_checkpoint_is_finished_on_recovery(w):
    crash_on(w, lambda op, k, v: k.startswith(od.NS + "seen:") and k.endswith(w.recorded[2]))
    with pytest.raises(Crash):
        w.deliver()
    w.restart()
    assert w.deliver()["ok"] and w.bridge.wire_sends == 1
    for ev in w.recorded:
        assert w.r.d.get(od.NS + "seen:" + ev) is not None


# ── multipart ────────────────────────────────────────────────────────────────────────────────────────────
def test_crash_between_parts_resumes_at_the_unsent_part_with_stable_ids(monkeypatch):
    real = od.render_digest
    monkeypatch.setattr(od, "render_digest", lambda *a, **k: real(*a, **{**k, "limit": 330}))
    x = World()
    cats = ["health", "email", "supplier", "harvest", "social", "orders"]
    for i in range(80):                                   # letters, not digits: digits are removed from the identity
        title = f"multipart event {chr(97 + i // 26)}{chr(97 + i % 26)} " + "ש" * 50
        asyncio.run(x.store().record(category=cats[i % 6], title=title, body="b"))
        x.recorded.append(od.identity_for(cats[i % 6], title))
    crash_on(x, lambda op, k, v: k == od.K_FROZEN and '"sent_parts": 1' in str(v))
    with pytest.raises(Crash):
        x.deliver()
    first_ids = [i for i, _ in x.bridge.wire]
    x.restart()
    res = x.deliver()
    assert res["ok"] and res["parts"] >= 2 and len(x.recorded) == 80 == len(asyncio.run(x.store().pending())) + len(json.loads(x.r.d[od.K_LAST])["events"])
    ids = [i for i, _ in x.bridge.wire]
    assert len(set(ids)) == res["parts"], "one distinct provider message per part"
    assert x.bridge.wire_sends == res["parts"], "part 1 was deduplicated, not re-sent"
    assert ids[0] == first_ids[0]


# ── 5. during retry ──────────────────────────────────────────────────────────────────────────────────────
def test_crash_during_a_retry_after_a_failed_attempt(w):
    w.bridge.up = False
    first = w.deliver()
    assert not first["ok"] and w.bridge.wire_sends == 0
    fz0 = asyncio.run(w.store().frozen())
    crash_on(w, lambda op, k, v: k == od.K_FROZEN and '"inflight"' in str(v))
    w.bridge.up = True
    with pytest.raises(Crash):
        w.deliver(at=MORNING.replace(hour=11), force=True)
    w.restart()
    res = w.deliver(at=MORNING.replace(hour=12), force=True)
    assert res["ok"] and res["digest"] == fz0["id"] and res["attempts"] >= 1
    assert w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1 and w.events_accounted()


# ── 6. outage recovery ──────────────────────────────────────────────────────────────────────────────────
def test_recovery_after_a_long_outage_is_one_message_not_a_storm(w):
    w.bridge.up = False
    for h in (9, 10, 11, 12, 13, 14):                    # six passes while the bridge is down
        r = w.deliver(at=MORNING.replace(hour=h, minute=45))
        assert not r["ok"] or r.get("skipped")
    assert w.bridge.wire_sends == 0
    w.rec("event arrived during the outage")             # still routed into the SAME pending set
    w.bridge.up = True
    res = w.deliver(at=MORNING.replace(hour=15), force=True)
    assert res["ok"] and w.bridge.wire_sends == 1 and w.bridge.owner_visible == 1
    again = w.deliver(at=MORNING.replace(hour=15, minute=10), force=True)
    assert w.bridge.wire_sends == 2                      # only the one event that arrived after the freeze
    assert w.events_accounted()


def test_message_id_is_stable_for_same_text_and_changes_with_text():
    a = od.client_msg_id("D20261125", 0, "x")
    assert a == od.client_msg_id("D20261125", 0, "x")
    assert a != od.client_msg_id("D20261125", 0, "y") and a != od.client_msg_id("D20261125", 1, "x")
    assert a.startswith("3EB0") and len(a) == 32 and a.isalnum()


def test_note_is_frozen_into_the_part_so_retries_are_byte_identical(w):
    w.bridge.up = False
    w.deliver(note="WhatsApp היה מנותק")
    fz = asyncio.run(w.store().frozen())
    assert "WhatsApp היה מנותק" in fz["parts"][0]
    w.bridge.up = True
    w.deliver(at=MORNING.replace(hour=12), force=True)
    assert w.bridge.wire[0][1] == fz["parts"][0]
