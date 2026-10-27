"""#114: the owner gets EVERY waiting keyword, in numbered batches, and can decide a batch in one reply.

2026-10-27 (owner): "message all the words till we finish them" - 2,615 words were waiting and the list showed the
same top 15 forever, one word per command. Measured that day: rejecting one pending word ran undo_keyword, a full
sequential scan of parts_catalog (no index on category_by), although a pending word never moved a part.
No network, no database, no Redis: storage, the catalogue and the LLM are replaced by in-memory fakes.
"""
import asyncio
import sys
import types

import pytest

from agents import owner_console as oc
import category_learning as cl


# ── which words a batch holds ─────────────────────────────────────────────────────────────────────
W = [f"w{i:02d}" for i in range(10)]


def test_first_batch_is_the_top_waiting_words():
    assert oc._kw_pick(W, set(), [], 4, advance=False) == (W[:4], False)


def test_the_current_batch_stays_while_any_of_its_words_waits():
    assert oc._kw_pick(W[1:], set(W[:4]), W[:4], 4, advance=False) == (W[:4], False)   # numbers keep meaning


def test_advance_sends_words_not_shown_yet_and_covers_the_whole_backlog():
    shown, seen, batch = set(), [], []
    for _ in range(3):
        batch, new_pass = oc._kw_pick(W, shown, batch, 4, advance=True)
        assert not new_pass
        seen += batch
        shown |= set(batch)
    assert seen == W                                                # every word once, in order, no repeats


def test_after_every_word_was_shown_a_new_pass_starts_over_what_is_left():
    left = ["w03", "w07"]
    assert oc._kw_pick(left, set(W), ["w08", "w09"], 4, advance=True) == (left, True)


def test_a_fully_decided_batch_is_replaced_without_advance():
    assert oc._kw_pick(W[4:], set(W[:4]), W[:4], 4, advance=False) == (W[4:8], False)


def _catalog_scraper(monkeypatch):
    """The real module when it imports (the test environments set DATABASE_URL); otherwise a stand-in, so these
    database-free tests also run in a bare container."""
    try:
        import catalog_scraper as mod
    except RuntimeError:
        mod = sys.modules.get("catalog_scraper") or types.ModuleType("catalog_scraper")
        mod.scrape_motorstore = getattr(mod, "scrape_motorstore", None)
        mod.scraper_session_factory = getattr(mod, "scraper_session_factory", None)
        monkeypatch.setitem(sys.modules, "catalog_scraper", mod)
    return mod


# ── commands, end to end through the console with in-memory fakes ────────────────────────────────
class FakeRedis:
    def __init__(self):
        self.kv, self.sets = {}, {}

    async def get(self, k):
        return self.kv.get(k)

    async def set(self, k, v, ex=None):
        self.kv[k] = str(v)

    async def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, 0)) + 1)
        return int(self.kv[k])

    async def smembers(self, k):
        return set(self.sets.get(k, set()))

    async def sadd(self, k, *v):
        self.sets.setdefault(k, set()).update(v)

    async def expire(self, k, s):
        return True

    async def delete(self, k):
        self.kv.pop(k, None)
        self.sets.pop(k, None)


class FakeCatalog:
    """category_learned_keywords in memory: status per token. `ok` words pass the evidence gate."""
    def __init__(self, tokens, ok):
        self.status = {t: "pending" for t in tokens}
        self.ok = set(ok)
        self.undo_calls = []


@pytest.fixture
def env(monkeypatch):
    tokens = [f"tok{i:02d}" for i in range(7)]
    cat = FakeCatalog(tokens, ok={"tok00", "tok02"})
    r = FakeRedis()

    class _Db:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, stmt, params=None):
            q, t = str(stmt), params["t"]
            if isinstance(t, list):                          # the batch outcome query
                rows = [(x, cat.status[x] == "approved", cat.status[x] == "rejected") for x in t]
                val = None
            else:                                            # the single-word handlers' stored-outcome queries
                rows = []
                val = int(cat.status[t] != "rejected") if "count(*)" in q else cat.status[t]

            class R:
                def fetchall(_s):
                    return rows

                def scalar(_s):
                    return val
            return R()

    async def waiting():
        return [{"token": t, "category": "engine", "observations": 5, "agreement": 1.0}
                for t in tokens if cat.status[t] == "pending"]

    async def evidence(db, t, proposed=None):
        return {"verdict": "ok"} if t in cat.ok else {"verdict": "category_mismatch", "top": "brakes"}

    async def approve(db, t, force=False):
        if t in cat.ok or force:
            cat.status[t] = "approved"
            return {"ok": True, "token": t, "category": "engine"}
        return {"ok": False, "error": "category_mismatch", "evidence": {}}

    async def reject(db, t):
        cat.status[t] = "rejected"
        return {"ok": True, "token": t}

    async def get_r():
        return r

    async def exists(db, t):
        return t in cat.status
    monkeypatch.setattr(cl, "token_exists", exists)

    monkeypatch.setattr(_catalog_scraper(monkeypatch), "scraper_session_factory", lambda: _Db())
    monkeypatch.setattr(oc, "_kw_waiting", waiting)
    monkeypatch.setattr(oc, "_kw_redis", get_r)
    monkeypatch.setattr(cl, "evidence_profile", evidence)
    monkeypatch.setattr(cl, "approve", approve)
    monkeypatch.setattr(cl, "reject", reject)
    monkeypatch.setenv("OWNER_KEYWORD_BATCH", "5")

    async def no_agent():
        return None
    monkeypatch.setattr(oc, "_get_active_agent", no_agent)
    return tokens, cat, r


def say(text):
    return asyncio.run(oc._process_owner_message(text, db=None))


def test_list_is_numbered_marks_evidence_and_offers_batch_actions(env):
    tokens, cat, r = env
    out = say("מילים")
    assert "רשימה 1" in out and "7 ממתינות בסך הכול" in out
    assert "1. *tok00* →" in out and "✅" in out.split("\n")[2]
    assert "2. *tok01* →" in out and "⚠️" in out.split("\n")[3]
    assert "6. *tok05*" not in out                                  # batch size 5
    assert "*אשרהכל 1*" in out and "*דחההשאר 1*" in out and "עוד לא הוצגו: 2" in out
    assert say("מילים").split("\n")[0] == out.split("\n")[0]         # asking again keeps the same batch


def test_approve_all_approves_only_the_evidence_backed_words_of_that_batch(env):
    tokens, cat, r = env
    say("מילים")
    out = say("אשרהכל")
    assert cat.status["tok00"] == "approved" and cat.status["tok02"] == "approved"
    assert all(cat.status[t] == "pending" for t in ("tok01", "tok03", "tok04", "tok05", "tok06"))
    assert "אושרו 2 — tok00, tok02" in out and "נשארו 3" in out


def test_reject_rest_rejects_the_batch_and_sends_the_next_one(env):
    tokens, cat, r = env
    say("מילים")
    say("אשר הכל")
    out = say("דחה השאר")
    assert [t for t in tokens if cat.status[t] == "rejected"] == ["tok01", "tok03", "tok04"]
    assert cat.status["tok05"] == "pending" and cat.status["tok06"] == "pending"     # never shown: untouched
    assert "נדחו 3" in out and "רשימה 2" in out and "1. *tok05*" in out


def test_an_old_batch_id_acts_on_the_words_that_message_showed(env):
    tokens, cat, r = env
    say("מילים")                       # batch 1: tok00-04
    say("מילים הבא")                   # batch 2: tok05-06
    say("דחההשאר 1")
    assert {t for t in tokens if cat.status[t] == "rejected"} == {"tok00", "tok01", "tok02", "tok03", "tok04"}
    assert cat.status["tok05"] == "pending"


def test_numbers_pick_words_from_the_batch_shown(env):
    tokens, cat, r = env
    say("מילים")
    say("דחהמילה 2 4")
    assert cat.status["tok01"] == "rejected" and cat.status["tok03"] == "rejected"
    say("אשרמילה 2 בכוח")                    # already decided: nothing changes
    say("אשרמילה 4 5 בכוח")                  # 4 already rejected, 5 forced
    assert cat.status["tok04"] == "approved"
    assert "לא קיים" in say("אשרמילה 9")


def test_bulk_words_are_not_read_as_post_commands(env):
    assert oc._KW_BULK_RE.match("אשר הכל") and oc._KW_BULK_RE.match("דחההשאר 12")
    assert not oc._KW_BULK_RE.match("אשר 81a6b13e")


# ── rejecting a word that was never live does not scan the catalogue ─────────────────────────────
class _RejDb:
    def __init__(self, approved):
        self.approved, self.sql = approved, []

    async def execute(self, stmt, params=None):
        q = str(stmt)
        self.sql.append(q)
        val = 1 if ("status = 'approved'" in q and self.approved) else None

        class R:
            rowcount = 1

            def scalar(_s):
                return val
        return R()

    async def commit(self):
        return None


@pytest.mark.parametrize("was_approved,undo", [(False, 0), (True, 1)])
def test_reject_undoes_only_a_keyword_that_was_live(monkeypatch, was_approved, undo):
    calls = []

    async def fake_undo(db, t):
        calls.append(t)
        return 0

    async def ok(*a, **k):
        return True
    monkeypatch.setattr(cl, "undo_keyword", fake_undo)
    monkeypatch.setattr(cl, "ensure_table", ok)
    monkeypatch.setattr(cl, "token_exists", ok)
    res = asyncio.run(cl.reject(_RejDb(was_approved), "zzpending"))
    assert res["ok"] and len(calls) == undo
    cl._rejected_tokens.discard("zzpending")


# ── the daily message ─────────────────────────────────────────────────────────────────────────────
def test_daily_message_sends_the_next_batch_once_per_israel_day(monkeypatch):
    _catalog_scraper(monkeypatch)
    import db_cleanup_agent as dca
    import BACKEND_API_ROUTES as app
    sent, lists = [], []

    async def fake_notify(category, title, body="", **kw):
        sent.append((title, body))
        return {"ok": True}

    async def fake_list(advance=False):
        lists.append(advance)
        return f"BATCH-{len(lists)}"
    monkeypatch.setattr(app, "notify_owner", fake_notify)
    monkeypatch.setattr(oc, "_keywords_list", fake_list)
    monkeypatch.setenv("OWNER_WHATSAPP_PHONE", "+972500000001")
    monkeypatch.delenv("OWNER_KEYWORD_DAILY_BATCH", raising=False)
    r = FakeRedis()
    pend = [{"token": "a"}, {"token": "b"}]
    t0 = 1_792_530_000.0                                   # 2026-10-21 00:00 Israel time

    async def go(ts):
        return await dca._notify_owner_pending_keywords(pend, now_ts=ts, redis_client=r)
    p1 = asyncio.run(go(t0 + 9 * 3600))
    p2 = asyncio.run(go(t0 + 15 * 3600))                   # same day
    p3 = asyncio.run(go(t0 + 33 * 3600))                   # next day
    assert [p1["send"], p2["send"], p3["send"]] == ["batch", None, "batch"]
    assert [b for _, b in sent] == ["BATCH-1", "BATCH-2"] and lists == [True, True]
    assert sent[0][0] == "מילות סיווג לאישורך — 2 ממתינות"
