"""Group mentions + participant discovery (#157, 2026-11-26): the configured AutoSpareFinder group only (identity check
on the LIVE subject), cache invalidation on membership change, UI-safe members, real mention JIDs, and webhook gating:
group traffic and button taps never reach the customer path, a group mention never runs a console command."""
import asyncio
import types

import pytest

import owner_interactions as oi
from routes import wa_group as g

GJID = "120363428683447639@g.us"
DATA = {"ok": True, "subject": "AutoSpareFinder — עדכונים מערכת", "participants": [
    {"jid": "98058566160397@lid", "lid": "98058566160397@lid", "phone": "972586050155@s.whatsapp.net", "admin": "admin"},
    {"jid": "11111111111111@lid", "lid": "11111111111111@lid", "phone": None, "admin": None, "is_me": True},
    {"jid": "972500000001@s.whatsapp.net", "admin": None}]}


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


def test_identity_check_rejects_a_wrong_group():
    g.check_identity(DATA)
    with pytest.raises(Exception) as e:
        g.check_identity({"subject": "משפחה"})
    assert getattr(e.value, "status_code", 0) == 409


def test_members_exclude_our_account_and_label_owner_from_config(monkeypatch):
    from agents import owner_console as con
    monkeypatch.setattr(con, "OWNER_PHONE", "+972586050155")
    m = g.shape_members(DATA, ["972532426920"])
    assert [x["name"] for x in m] == ["הבעלים", "משתתף 3"], "is_me excluded; the owner labelled from the configured phone"
    assert "98058566160397" not in str(m) and "972500000001" not in str(m)
    text, jids = g.render_mentions(f"@{{{m[0]['token']}}} בדוק", DATA)
    assert jids == ["98058566160397@lid"] and text == "@98058566160397 בדוק"


def test_cache_invalidation_on_membership_change(monkeypatch):
    calls = []

    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            calls.append(url)
            return types.SimpleNamespace(status_code=200, json=lambda: DATA)
    monkeypatch.setattr(g.httpx, "AsyncClient", C)
    g.invalidate()
    run(g._participants(GJID)); run(g._participants(GJID))
    assert len(calls) == 1, "cached"
    assert g.invalidate(GJID) and not g.invalidate("other@g.us")
    run(g._participants(GJID))
    assert len(calls) == 2, "membership change drops the cache"


def test_bridge_unavailable_is_503_and_not_cached(monkeypatch):
    class C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url):
            return types.SimpleNamespace(status_code=503, json=lambda: {"ok": False, "error": "socket not connected"})
    monkeypatch.setattr(g.httpx, "AsyncClient", C)
    g.invalidate()
    with pytest.raises(Exception) as e:
        run(g._participants(GJID, force=True))
    assert e.value.status_code == 503 and g._CACHE["data"] is None


def test_configured_group_prefers_explicit_jid(monkeypatch):
    monkeypatch.setenv("WA_COMPOSER_GROUP_JID", GJID)
    assert run(g.configured_group_jid()) == GJID
    monkeypatch.setenv("WA_COMPOSER_GROUP_JID", "not-a-group")
    with pytest.raises(Exception):
        run(g.configured_group_jid())


# ── webhook gating ─────────────────────────────────────────────────────────────────────────────────────────────
class _Req:
    def __init__(self, payload):
        self._p, self.state = payload, types.SimpleNamespace()

    async def json(self):
        return self._p


class _NoDB:
    def __getattr__(self, name):
        raise AssertionError(f"customer path reached (db.{name})")


def _hook(payload):
    from routes import webhooks as wh
    return run(wh._whatsapp_webhook_impl(_Req(payload), _NoDB()))


def test_group_traffic_never_reaches_customer_path(monkeypatch):
    monkeypatch.delenv("WA_GROUP_INBOUND_ENABLED", raising=False)
    r = _hook({"from": "whatsapp:group", "body": "@972532426920 היי", "message_id": "",
               "group_message": {"group_jid": GJID, "sender_jid": "98058566160397@lid", "message_id": "g1"}})
    assert r.status_code == 200
    r = _hook({"from": "whatsapp:group", "body": "", "message_id": "", "group_update": {"group_jid": GJID, "action": "remove"}})
    assert r.status_code == 200


def test_tap_with_flag_off_is_dropped(monkeypatch):
    monkeypatch.delenv("WA_INTERACTIVE_ENABLED", raising=False)
    r = _hook({"from": "whatsapp:+972586050155", "body": "", "message_id": "",
               "interactive_response": {"interaction_id": "ix_abcdabcd", "action_id": "approve"}})
    assert r.status_code == 200


def test_group_mention_refuses_commands_and_answers_only_owner_in_the_group(monkeypatch):
    from routes import webhooks as wh
    sent, asked = [], []

    async def fake_send(to, text, **kw):
        sent.append((to, text)); return {"ok": True}

    async def cfg():
        return GJID
    import social.whatsapp_provider as wp
    monkeypatch.setattr(wp, "send_message", fake_send)
    monkeypatch.setattr(g, "configured_group_jid", cfg)
    owner = lambda x: x == "98058566160397"
    ok_gm = {"group_jid": GJID, "sender_jid": "98058566160397@lid", "message_id": "m-a"}
    run(wh._handle_group_mention({**ok_gm, "message_id": "m-1"}, "@972532426920 שנהפוסט ab12 קצר", owner, oi, None))
    assert sent and "רק מהצ'אט הפרטי" in sent[-1][1]
    n = len(sent)
    run(wh._handle_group_mention({**ok_gm, "sender_jid": "972500000001@s.whatsapp.net", "message_id": "m-2"}, "נועה היי", owner, oi, None))
    run(wh._handle_group_mention({**ok_gm, "group_jid": "999@g.us", "message_id": "m-3"}, "נועה היי", owner, oi, None))
    assert len(sent) == n, "non-owner and other groups are ignored"


# ── positive paths: the webhook actually reaches the handlers ───────────────────────────────────────────────────
def test_group_update_reaches_cache_invalidation():
    g._CACHE.update(at=1e12, jid=GJID, data=DATA)
    _hook({"from": "whatsapp:group", "body": "", "message_id": "", "group_update": {"group_jid": GJID, "action": "add"}})
    assert g._CACHE["data"] is None, "membership change dropped the cached participants"


def test_group_mention_reaches_handler_when_enabled(monkeypatch):
    from routes import webhooks as wh
    seen = []

    async def rec(gm, body, is_owner, oi_, store):
        seen.append((gm["group_jid"], body))
        from starlette.responses import Response
        return Response(content="<Response/>", media_type="text/xml")
    monkeypatch.setenv("WA_GROUP_INBOUND_ENABLED", "1")
    monkeypatch.setattr(wh, "_handle_group_mention", rec)
    _hook({"from": "whatsapp:group", "body": "@972532426920 נועה היי", "message_id": "",
           "group_message": {"group_jid": GJID, "sender_jid": "98058566160397@lid", "message_id": "g9"}})
    assert seen == [(GJID, "@972532426920 נועה היי")]


def test_tap_reaches_handle_tap_and_executes_as_typed_owner_command(monkeypatch):
    import owner_interaction_flow as oif
    from agents import owner_console as con
    taps, ran = [], []

    async def fake_tap(store, ir, *, sender_is_owner, owner_dest, send, console):
        taps.append((ir["interaction_id"], sender_is_owner))
        return "בדיקתאישור T1"

    async def fake_pom(msg, source="whatsapp", quoted_text=""):
        ran.append(msg)
        return "ok"

    async def fake_send(to, text, **kw):
        return {"ok": True}
    monkeypatch.setenv("WA_INTERACTIVE_ENABLED", "1")
    monkeypatch.setattr(oif, "handle_tap", fake_tap)
    monkeypatch.setattr(oif, "pg_store", lambda: object())
    monkeypatch.setattr(con, "OWNER_PHONE", "+972586050155")
    monkeypatch.setattr(con, "process_owner_message", fake_pom)
    import social.whatsapp_provider as wp
    monkeypatch.setattr(wp, "send_message", fake_send)
    monkeypatch.setattr(wp, "send_typing", fake_send, raising=False)

    async def go():
        from routes import webhooks as wh
        r = await wh._whatsapp_webhook_impl(_Req({"from": "whatsapp:+972586050155", "body": "", "message_id": "",
                                                  "interactive_response": {"interaction_id": "ix_abcdabcd", "action_id": "approve"}}), _NoDB())
        for t in list(wh._OWNER_REPLY_TASKS):
            await t
        return r
    run(go())
    assert taps == [("ix_abcdabcd", True)] and ran == ["בדיקתאישור T1"], "the tap ran the registered command as typed"


def test_parse_incoming_keeps_group_sender():
    from social.whatsapp_provider import parse_incoming
    assert parse_incoming({"from": "whatsapp:group", "body": ""})["from"] == "whatsapp:group"


def test_group_conversation_is_isolated_from_the_private_console(monkeypatch):
    """#157: a group mention never sees or extends the owner's PRIVATE history and never moves the private sticky agent."""
    from agents import owner_console as con
    import hf_client
    touched, prompts = [], []

    async def no(*a, **k):
        touched.append(a or k)
        return [] if not a else None

    async def snap(db, brief=False):
        return "status"

    async def fake_hf(prompt, system="", **kw):
        prompts.append(prompt)
        return "תשובה"
    for name in ("_load_history", "_save_history", "_get_active_agent", "_set_active_agent", "_noa_guidelines_save"):
        monkeypatch.setattr(con, name, no)
    monkeypatch.setattr(con, "build_status_snapshot", snap)
    monkeypatch.setattr(hf_client, "hf_text", fake_hf)

    async def guide(db):
        return ""
    monkeypatch.setattr(con, "_noa_guidelines_get", guide)
    out = run(con._process_owner_message("נועה מה דעתך על רעיון לפוסט חורף?", db=None, source="whatsapp_group"))
    assert "תשובה" in out and touched == [], "no private history read/write, no sticky-agent change"
    assert prompts and prompts[0].startswith("user:"), "the prompt carries no earlier private turns"
