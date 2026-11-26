"""Resume Interaction (#157, 2026-11-26): a Change/Edit/More-info tap returns the owner's NEXT private text to the
ORIGINATING agent and task, the revised proposal comes back with NEW buttons bound to the new version, and every
unsafe case (two pending requests, expiry, duplicate delivery, non-owner, group, console command, failure) is safe.
No network: MemoryStore, fake senders, injected LLM, fake Gmail transport."""
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import owner_interactions as oi
import owner_interaction_flow as flow
import owner_poll as op
from owner_interactions_store import MemoryStore
from agents import owner_continuations as oc

sys.path.insert(0, str(Path(__file__).resolve().parent))
UTC = timezone.utc


def run(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


class Sender:
    def __init__(self):
        self.sent, self.n = [], 0

    async def __call__(self, to, text, **kw):
        self.n += 1
        self.sent.append({"to": to, "text": text})
        return {"ok": True, "key": {"id": f"MSG{self.n}"}}


def tap(store, ix, action, owner=True, **kw):
    return run(oi.resolve_response(store, {"interaction_id": ix.id, "action_id": action, **kw}, sender_is_owner=owner))


async def _console_prompt(cmd):
    return f"PROMPT[{cmd}]"


# ── NOA: post Change -> NOA rewrites -> revised proposal with NEW buttons ─────────────────────────────────────────
@pytest.fixture
def noa(monkeypatch):
    from agents import owner_console as con
    posts = {"ab12cd34-0000-0000-0000-000000000000": {"id": "ab12cd34-0000-0000-0000-000000000000",
                                                      "content": "רפידות בלם לקורולה מ-₪198. מחיר מעולה!", "platforms": ["facebook"]}}
    edits, llm = [], []

    async def resolve(db, token):
        return next((p for p in posts.values() if con._short(p["id"]).startswith(token)), None) if token else None

    async def edit(db, pid, text, source="whatsapp", actor="owner"):
        edits.append({"id": pid, "text": text, "source": source, "actor": actor})
        posts[pid]["content"] = text
        return True, "", len(edits) + 1

    async def guidelines(db):
        return "בלי אימוג'י"

    async def fake_llm(prompt, system):
        llm.append({"prompt": prompt, "system": system})
        return "רפידות בלם לקורולה מ-₪198 — קצר ולעניין."

    monkeypatch.setattr(con, "_resolve_post", resolve)
    monkeypatch.setattr(con, "_edit_post", edit)
    monkeypatch.setattr(con, "_noa_guidelines_get", guidelines)
    monkeypatch.setattr(oc, "LLM", fake_llm)
    monkeypatch.setattr(oc, "NOA_FINALIZE", lambda raw, plats: raw.strip() + "\n#רכב")
    return {"posts": posts, "edits": edits, "llm": llm}


async def _runner(cmd):
    return await oc.run(cmd, db=None)


def test_noa_change_reaches_noa_and_returns_revised_proposal_with_new_buttons(noa):
    s, send = MemoryStore(), Sender()
    v1_text = "פוסט v1"
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body=v1_text))
    assert [a.id for a in ix.actions] == ["approve", "change", "edit", "reject"]
    # tap "change" -> read-only prompt from NOA's own handler + hint; interaction awaits, prompt id recorded
    cmd = run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "change"}, sender_is_owner=True,
                              owner_dest="OWNER", send=send, console=_prompt_text))
    assert cmd is None and ix.state == "awaiting_input" and ix.prompt_message_id == "MSG1"
    assert "שינוי פוסט" in send.sent[0]["text"] and "NOA" in send.sent[0]["text"]
    assert not noa["edits"], "the tap itself changes nothing"
    # the owner's next private text -> NOA receives the ACTUAL request with the current post
    handled = run(flow.handle_owner_text(s, "תקצרי ותורידי את הסימן קריאה", "", owner_dest="OWNER", send=send,
                                         run_continuation=_runner))
    assert handled and ix.state == "completed"
    assert "תקצרי ותורידי את הסימן קריאה" in noa["llm"][0]["prompt"] and "₪198" in noa["llm"][0]["prompt"]
    assert "בלי אימוג'י" in noa["llm"][0]["system"], "NOA's saved guidelines apply"
    assert noa["edits"] == [{"id": "ab12cd34-0000-0000-0000-000000000000", "text": "רפידות בלם לקורולה מ-₪198 — קצר ולעניין.\n#רכב",
                             "source": "whatsapp_change", "actor": "noa"}], "G8 finalizer output, attributed to NOA"
    clean, poll = op.split(send.sent[-1]["text"])
    assert "גרסה 2" in clean and "קצר ולעניין" in clean and poll and poll["commands"][0] == "אשר ab12cd34"
    # the chokepoint creates the v2 interaction from that message: v1 is superseded
    ix2 = run(oi.create_from_choices(s, list(zip(poll["values"], poll["commands"])), body=clean))
    assert ix2.id != ix.id and tap(s, ix, "approve").status == "reply", "a v1 tap can never approve v2"
    assert tap(s, ix2, "approve").command == "אשר ab12cd34"


def _prompt_text(cmd):
    async def go():
        r = await oc.run(cmd, db=None)
        return r.render()
    return go()


def test_noa_edit_owner_text_becomes_the_new_version(noa):
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="v1"))
    run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "edit"}, sender_is_owner=True, owner_dest="O",
                        send=send, console=_prompt_text))
    assert "הנוסח הנוכחי" in send.sent[0]["text"]
    # #158: a replacement is a real post (keeps the post's price); text without any of the post's facts is notes
    new = "רפידות בלם מקוריות לקורולה, עכשיו מ-₪198"
    run(flow.handle_owner_text(s, new, "", owner_dest="O", send=send, run_continuation=_runner))
    assert noa["edits"][-1]["text"] == new and noa["edits"][-1]["actor"] == "owner" and not noa["llm"]


# ── Email Agent: draft edit -> a NEW verified Gmail draft, never a send ───────────────────────────────────────────
def test_email_edit_creates_verified_new_draft_and_never_sends(monkeypatch):
    from test_email_agent import FakeGmail, gmsg, cfg, MAILBOX
    from email_agent import owner_flow as of
    from email_agent.gmail_client import GmailClient
    from email_agent.store import InMemoryStore
    monkeypatch.setattr(of._control, "read_control", lambda *a, **k: {"running": True, "reason": "test"})
    src = gmsg("m1", "t1", "Dana <dana@shop.example>", "Order 1234", text="where is my order?")
    fake = FakeGmail(messages=[src])
    import json as _json
    fake.raw_override["/messages/m1"] = _json.dumps(src).encode()
    client = GmailClient(cfg(gmail_drafts=True), transport=fake, sleep=lambda s: None)
    store = InMemoryStore()
    rid = str(uuid.uuid4())
    store.rows["m1"] = {"id": rid, "gmail_message_id": "m1", "status": "processed", "sender_email": "dana@shop.example",
                        "subject": "Order 1234", "draft_subject": "Re: Order 1234", "draft_body": "old",
                        "gmail_draft_id": "draft-old", "owner_closed_at": None, "sent_at": None,
                        "send_approved_at": "earlier", "send_approved_by": "x", "send_error": None,
                        "created_at": datetime(2026, 11, 26, 8, tzinfo=UTC), "requires_human": True,
                        "classification": "order_shipping", "task_ids": []}
    ref = rid[:8]
    res = run(oc.email_edit(ref, "שלום דנה, ההזמנה יצאה היום.", store=store, client=client, mailbox=MAILBOX))
    assert res.ok, res.text
    row = store.rows["m1"]
    assert row["gmail_draft_id"] == "draft-1" and row["draft_body"] == "שלום דנה, ההזמנה יצאה היום."
    assert row["send_approved_at"] is None, "an earlier approval approved other text"
    d = fake.drafts["draft-1"]["message"]
    hdr = {h["name"]: h["value"] for h in d["payload"]["headers"]}
    assert d["threadId"] == "t1" and "dana@shop.example" in hdr["To"] and hdr["Subject"] == "Re: Order 1234"
    assert not [p for p in fake.paths("POST") if "send" in p], "no send endpoint was called"
    assert res.choices and res.choices[1] == ("✅ שלח טיוטה", f"שלחמייל {ref}")
    # a sent / closed mail cannot be edited
    row["sent_at"] = "now"
    assert not run(oc.email_edit(ref, "x", store=store, client=client, mailbox=MAILBOX)).ok


def test_email_edit_refused_while_agent_halted(monkeypatch):
    from email_agent import owner_flow as of
    from email_agent.store import InMemoryStore
    monkeypatch.setattr(of._control, "read_control", lambda *a, **k: {"running": False, "reason": "x"})
    store = InMemoryStore()
    store.rows["m1"] = {"id": "abcd1234-0000", "gmail_message_id": "m1", "gmail_draft_id": "d", "sent_at": None,
                        "owner_closed_at": None}
    ok, msg = run(of.revise_draft(store, object(), "abcd1234", "x", mailbox="m"))
    assert not ok and "halt" in msg


# ── NIR: More info -> NIR answers from the candidate's record only ────────────────────────────────────────────────
def test_nir_question_answered_from_the_record_without_state_change(monkeypatch):
    calls = []

    async def lookup(ref):
        return {"ok": True, "record": {"id": "5f3e2a1b-0000", "name": "Parts Co", "website": "parts.example",
                                       "country": "DE", "reliability_score": 0.7, "is_active": False,
                                       "credentials": {"status": "pending_review", "reasons": ["ships to IL"]}}}

    async def fake_llm(prompt, system):
        calls.append((prompt, system))
        return "לפי הרשומה הם שולחים לישראל; אין מידע על זמני משלוח."
    monkeypatch.setattr(oc, "LLM", fake_llm)
    r = run(oc.nir_ask("5f3e2a1b", "הם שולחים לישראל?", lookup=lookup))
    assert r.ok and "שולחים לישראל" in r.text and "ships to IL" in calls[0][0] and "הם שולחים לישראל?" in calls[0][0]
    assert "אל תמציא" in calls[0][1]
    assert r.choices == op.supplier_candidate_choices("5f3e2a1b")


# ── two simultaneous interactions, quote-reply, ambiguity ────────────────────────────────────────────────────────
def _two(s):
    a = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="post"))
    b = run(oi.create_from_choices(s, op.email_choices("e1e2e3e4", has_draft=True), body="mail"))
    assert tap(s, a, "change").status == "followup" and tap(s, b, "edit").status == "followup"
    run(s.set_prompt_message_id(a.id, "PA")); run(s.set_prompt_message_id(b.id, "PB"))
    return a, b


def test_two_pending_requests_never_guess():
    s = MemoryStore()
    a, b = _two(s)
    got = run(oi.bind_followup(s, "קצר יותר"))
    assert got.status == "ambiguous" and a.state == b.state == "awaiting_input", "nothing executed"
    assert "NOA" in got.reply and "EMAIL" in got.reply
    got = run(oi.bind_followup(s, "נוסח חדש למייל", quoted_message_id="PB"))
    assert got.status == "bound" and got.command == "ערוךמייל e1e2e3e4 נוסח חדש למייל" and b.state == "completed"
    assert a.state == "awaiting_input", "the other agent's request is untouched (no leakage)"
    got = run(oi.bind_followup(s, "קצר יותר"))
    assert got.status == "bound" and got.command == "שנהפוסט ab12cd34 קצר יותר"


def test_new_request_never_overwrites_another_pending_one():
    s = MemoryStore()
    a, b = _two(s)
    assert a.continuation["verb"] == "שנהפוסט" and b.continuation["verb"] == "ערוךמייל"
    assert tap(s, a, "change").status == "reply", "a second tap on a pending request does not reset it"
    assert a.continuation["ref"] == "ab12cd34"


# ── console commands, cancel, expiry, duplicates, authorization ─────────────────────────────────────────────────
def test_console_commands_pass_through_and_cancel_restores_buttons():
    s = MemoryStore()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="p"))
    tap(s, ix, "edit")
    for cmd in ("סטטוס", "מיילים", "אשר ab12cd34", "@נועה מה קורה", "ספקים"):
        assert run(oi.bind_followup(s, cmd)) is None, cmd
    assert ix.state == "awaiting_input"
    got = run(oi.bind_followup(s, "בטל"))
    assert got.status == "cancelled" and ix.state == "open"
    assert tap(s, ix, "approve").status == "execute", "cancel returns to the previous state; buttons still usable"


def test_lapsed_followup_routes_normally_and_expired_interaction_refused():
    s = MemoryStore()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="p"))
    tap(s, ix, "edit")
    ix.followup_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert run(oi.bind_followup(s, "טקסט מאוחר")) is None and ix.state == "open"
    assert any(k == "followup_lapsed" for _, k, _ in s.events)
    ix.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert tap(s, ix, "edit").status == "reply"


def test_duplicate_delivery_resumes_once():
    s = MemoryStore()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="p"))
    tap(s, ix, "edit")

    async def both():
        return await asyncio.gather(oi.bind_followup(s, "x1"), oi.bind_followup(s, "x1"))
    got = run(both())
    assert sorted(g.status if g else "none" for g in got) in (["bound", "duplicate"], ["bound", "none"])


def test_non_owner_and_group_taps_never_resume():
    s = MemoryStore()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="p"))
    assert tap(s, ix, "edit", owner=False).status == "ignored"
    assert tap(s, ix, "edit", is_group=True).status == "ignored"
    assert ix.state == "open"


def test_failed_continuation_releases_and_tells_owner():
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="p"))
    tap(s, ix, "change")

    async def failing(cmd):
        return oc.Result(False, "⚠️ NOA לא הצליחה")
    assert run(flow.handle_owner_text(s, "שנה", "", owner_dest="O", send=send, run_continuation=failing))
    assert ix.state == "open" and "עדיין פתוחה" in send.sent[-1]["text"]
    assert tap(s, ix, "approve").status == "execute"


def test_no_pending_followup_keeps_free_text_behaviour():
    s, send = MemoryStore(), Sender()
    assert run(flow.handle_owner_text(s, "שלום אבי", "", owner_dest="O", send=send, run_continuation=_runner)) is False
    assert send.sent == []


def test_resumable_verbs_never_include_approvals():
    for v in oi.RESUMABLE:
        assert oi._VERBS[v][0] in ("edit", "change", "details"), v
    assert not ({"אשר", "שלחמייל", "אשרספק", "אשררכש", "אשרתשלוםספק", "ענה", "אשרמילה"} & oi.RESUMABLE)


def test_selftest_change_continuation_is_inert():
    r = run(oc.run("בדיקתשינוי T1 טקסט כלשהו", db=None))
    assert r.ok and "לא בוצעה שום פעולה" in r.text and r.choices == op.selftest_choices("T1")


# ── decision coverage: read-only list buttons never make a routine alert immediate ───────────────────────────────
def test_has_decision():
    assert oi.has_decision(op.post_choices("x1234567"))
    assert oi.has_decision(op.keyword_choices("bremse"))
    assert oi.has_decision(op.inbox_choices("i1234567"))
    assert not oi.has_decision(op.procurement_choices())
    assert not oi.has_decision(op.boaz_task_choices("m1"))
    assert not oi.has_decision(op.list_choices("פוסטים", "📝"))
    assert not oi.has_decision(None)


def test_choice_sets_are_record_bound():
    assert op.keyword_choices("") == [] and op.supplier_candidate_choices("") == []
    assert [c[1] for c in op.keyword_choices("brake  pad")][:2] == ["אשרמילה brake pad", "דחהמילה brake pad"]
    assert all(oi.classify(c[1])[0] != "approve" for c in op.procurement_choices()), "no commercial approval button"


def test_engagement_edit_replaces_the_draft_and_never_sends(monkeypatch):
    from social import engagement as eng
    item = {"id": "c0ffee12-0000-0000-0000-000000000000", "platform": "facebook", "external_id": "x",
            "reply_text": "טיוטה ישנה", "author": "Dana", "message": "יש לכם רפידות?"}
    saved, sent = [], []

    async def resolve(db, token):
        return item if token and item["id"].replace("-", "").startswith(token) else None

    async def set_draft(db, iid, text):
        saved.append((iid, text))

    async def send_reply(*a, **k):
        sent.append(a)
        return {"ok": True}
    monkeypatch.setattr(eng, "resolve_inbox", resolve)
    monkeypatch.setattr(eng, "set_draft", set_draft)
    monkeypatch.setattr(eng, "send_reply", send_reply)
    prompt = run(oc.run("ערוךתשובה c0ffee12", db=None))
    assert prompt.ok and "טיוטה ישנה" in prompt.text and not saved
    r = run(oc.run("ערוךתשובה c0ffee12 כן! יש במלאי", db=None))
    assert r.ok and saved == [(item["id"], "כן! יש במלאי")] and not sent
    assert r.choices == op.inbox_choices("c0ffee12") and "ענה c0ffee12" in r.text


# ── #158: notes sent after "אכתוב נוסח משלי" are notes, not the post (owner, 2026-11-26) ─────────────────────────
LONG_POST = ("רפידות בלם לקורולה מ-₪198. " * 12).strip()


def test_notes_after_edit_tap_are_rewritten_by_noa_not_pasted(noa):
    pid = "ab12cd34-0000-0000-0000-000000000000"
    noa["posts"][pid]["content"] = LONG_POST
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="v1"))
    run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "edit"}, sender_is_owner=True, owner_dest="O",
                        send=send, console=_prompt_text))
    notes = "הפוסט לא צריך את ההערות שלי בתוכו, תעשי אותו מצחיק יותר"
    run(flow.handle_owner_text(s, notes, "", owner_dest="O", send=send, run_continuation=_runner))
    assert noa["llm"] and notes in noa["llm"][0]["prompt"], "NOA received the notes as a change request"
    assert noa["edits"][-1]["actor"] == "noa" and noa["edits"][-1]["text"] != notes, "the notes were not pasted in"
    assert "נראתה כמו הערות" in send.sent[-1]["text"] and "ערוך ab12cd34" in send.sent[-1]["text"]


def test_full_replacement_text_is_still_word_for_word(noa):
    pid = "ab12cd34-0000-0000-0000-000000000000"
    noa["posts"][pid]["content"] = LONG_POST
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="v1"))
    run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "edit"}, sender_is_owner=True, owner_dest="O",
                        send=send, console=_prompt_text))
    new = ("מצאנו לכם רפידות בלם מקוריות לקורולה מ-₪198, עם התאמה מאומתת לרכב. " * 4).strip()
    run(flow.handle_owner_text(s, new, "", owner_dest="O", send=send, run_continuation=_runner))
    assert noa["edits"][-1] == {"id": pid, "text": new, "source": "whatsapp", "actor": "owner"} and not noa["llm"]


def test_looks_like_notes_rule():
    assert oc.looks_like_notes("תקצרי את זה", "פוסט קצר")
    assert oc.looks_like_notes("הערות: בלי אימוג'י", "x")
    assert not oc.looks_like_notes("פוסט חדש לגמרי באותו אורך בערך", "פוסט ישן באותו אורך בערך")
    assert oc.notes_verdict("יותר מצחיק", "א" * 400) == "ask" and oc.notes_verdict("ב" * 300, "א" * 400) == "post"
    assert not oc.looks_like_notes("", "א" * 400)


def test_completed_continuation_is_recorded_in_the_console_history(noa, monkeypatch):
    from agents import owner_console as con
    hist = []

    async def load():
        return list(hist)

    async def save(h):
        hist[:] = h

    monkeypatch.setattr(con, "_load_history", load)
    monkeypatch.setattr(con, "_save_history", save)
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="v1"))
    run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "change"}, sender_is_owner=True, owner_dest="O",
                        send=send, console=_prompt_text))
    run(flow.handle_owner_text(s, "תקצרי", "", owner_dest="O", send=send, run_continuation=_runner))
    assert hist[0]["role"] == "user" and "תקצרי" in hist[0]["content"] and "NOA" in hist[0]["content"]
    assert hist[1]["role"] == "assistant" and hist[1]["agent"] == "social_media_manager_agent" and "קצר ולעניין" in hist[1]["content"]


def test_owner_name_never_gets_the_definite_article():
    from agents import owner_console as con
    assert con._clean_wa_reply("הבנתי, החליל. נתחיל.") == "הבנתי, חליל. נתחיל."
    assert "חליל" in con._clean_wa_reply("חליל, הכל טוב") and "החליל" not in con._clean_wa_reply("החליל!")
    assert "לחליל" in con._clean_wa_reply("שלחתי לחליל")


# real revisions of post 3e253a49 (2026-11-26): v3 and v4 were the owner's NOTES pasted in by the old exact-edit path
V2 = ("הקפיצים של Peugeot 3008 מתנהלים לאסטרטגיה? קפיצי Coil Spring של MOTUL מבטיחים יציבות מלאה, במיוחד במזג אוויר קר.\n"
      "מחיר: 399 ₪ – חפשו לפי מספר רישוי ב autosparefinder.co.il.\n\n#קפיצים #AutoSpareFinder")
V3 = 'אהבתי את הרעיון והסדר אבל עדיין יש מילים שלא קשורות כמו : " מתנהלים לאסטרטגיה" איך המילים הללו קשורים למתלים ?!'
V4 = "פשוט תתייחסי להערה שרשמתי מקודם"


def test_the_two_real_pasted_notes_are_recognised_as_notes():
    assert oc.looks_like_notes(V3, V2), "no price/link/hashtag of the post, talks about its words"
    assert oc.looks_like_notes(V4, V3), "refers to 'the note'"


def test_rewrite_base_skips_pasted_notes_and_returns_them(monkeypatch):
    import social.campaign_manager as cm
    revs = [{"version": 1, "content": "v1 " + V2, "actor": "noa"}, {"version": 2, "content": V2, "actor": "noa"},
            {"version": 3, "content": V3, "actor": "owner"}, {"version": 4, "content": V4, "actor": "owner"}]

    async def fake_revs(db, pid):
        return revs

    monkeypatch.setattr(cm, "get_revisions", fake_revs)
    base, notes = run(oc._rewrite_base(None, {"id": "3e253a49", "content": V4}))
    assert base == V2 and notes == [V3, V4]
    # a genuine owner replacement (keeps the facts) is a real base, not a note
    revs.append({"version": 5, "content": V2.replace("מתנהלים לאסטרטגיה", "נשחקים עם הזמן"), "actor": "owner"})
    base, notes = run(oc._rewrite_base(None, {"id": "3e253a49", "content": revs[-1]["content"]}))
    assert "נשחקים עם הזמן" in base and notes == []


def test_noa_change_prompt_carries_the_earlier_notes_and_the_real_base(noa, monkeypatch):
    import social.campaign_manager as cm
    pid = "ab12cd34-0000-0000-0000-000000000000"
    noa["posts"][pid]["content"] = V4

    async def fake_revs(db, p):
        return [{"version": 1, "content": V2, "actor": "noa"}, {"version": 2, "content": V3, "actor": "owner"},
                {"version": 3, "content": V4, "actor": "owner"}]

    monkeypatch.setattr(cm, "get_revisions", fake_revs)
    res = run(oc.noa_change(None, "ab12cd34", "תתקני את הניסוח"))
    p = noa["llm"][0]["prompt"]
    assert res.ok and V2[:40] in p and V3 in p and V4 in p, "base = last real post, both notes included"
    assert p.index(V2[:40]) < p.index(V3), "the post comes first, then the notes"


def test_approve_refuses_to_publish_owner_notes(monkeypatch):
    from agents import owner_console as con
    import social.campaign_manager as cm
    calls = []

    async def fake_revs(db, pid):
        return [{"version": 1, "content": V2, "actor": "noa"}, {"version": 2, "content": V3, "actor": "owner", "source": "whatsapp"},
                {"version": 3, "content": V4, "actor": "owner", "source": "whatsapp"}]

    async def approve(db, post_id, approved_by):
        calls.append(post_id)
        return True

    monkeypatch.setattr(cm, "get_revisions", fake_revs)
    monkeypatch.setattr(cm, "approve_post_content", approve)
    out = run(con._approve_and_publish(None, {"id": "3e253a49-0000-0000-0000-000000000000", "content": V4}))
    assert out.startswith("⛔") and "שנהפוסט 3e253a49" in out and calls == [], "nothing approved or published"



# expert review (#158): real-shaped replacement posts must never be treated as notes
REAL_POSTS = [
    "בלי כאב ראש: רפידות לקורולה ב-₪198 #בלמים",
    "שנה חדשה, בלמים חדשים! ₪198 #בלמים",
    "במקום לחפש שעות במוסכים – רפידות בלם לקורולה 2018 מ-₪198, התאמה מאומתת לפי מספר רישוי. autosparefinder.co.il #רפידות",
    "צריך מסנן שמן לקיה פיקנטו? ₪49 בלבד, משלוח מהיר. חפשו לפי מספר רישוי ב-autosparefinder.co.il #קיה #מסנן_שמן",
    "בלי הפתעות במוסך: מצמד מקורי למאזדה 3 ב-₪1,290, כולל אחריות 12 חודשים. #מאזדה #מצמד",
    "שנה חדשה, רכב בטוח יותר 🚗 דיסקים ורפידות לטויוטה RAV4 מ-₪420 – בדקו התאמה לפי מספר רישוי. #טויוטה",
]


@pytest.mark.parametrize("post", REAL_POSTS)
def test_real_shaped_replacement_posts_are_posts(post):
    assert oc.notes_verdict(post, LONG_POST) == "post"


def test_typed_edit_is_always_word_for_word_even_if_it_looks_like_notes(noa):
    pid = "ab12cd34-0000-0000-0000-000000000000"
    noa["posts"][pid]["content"] = LONG_POST
    res = run(oc.run("ערוך ab12cd34 " + V4, None))           # typed command: no button follow-up flag
    assert res.ok and noa["edits"][-1] == {"id": pid, "text": V4, "source": "whatsapp", "actor": "owner"} and not noa["llm"]


def test_ambiguous_text_after_the_button_changes_nothing_and_asks(noa):
    pid = "ab12cd34-0000-0000-0000-000000000000"
    noa["posts"][pid]["content"] = LONG_POST
    s, send = MemoryStore(), Sender()
    ix = run(oi.create_from_choices(s, op.post_choices("ab12cd34"), body="v1"))
    run(flow.handle_tap(s, {"interaction_id": ix.id, "action_id": "edit"}, sender_is_owner=True, owner_dest="O",
                        send=send, console=_prompt_text))
    run(flow.handle_owner_text(s, "יותר מצחיק ופחות רשמי", "", owner_dest="O", send=send, run_continuation=_runner))
    assert not noa["edits"] and not noa["llm"], "nothing changed"
    out = send.sent[-1]["text"]
    assert "לא שיניתי כלום" in out and "שנהפוסט ab12cd34" in out and "ערוך ab12cd34" in out
    assert ix.state != "completed", "the request stays usable"


def test_approve_force_and_web_edits_are_not_blocked(monkeypatch):
    from agents import owner_console as con
    import social.campaign_manager as cm
    calls = []
    revs = [{"version": 1, "content": V2, "actor": "noa", "source": "noa_draft"},
            {"version": 2, "content": V4, "actor": "owner", "source": "whatsapp"}]

    async def fake_revs(db, pid):
        return revs

    class Reached(Exception):
        pass

    async def approve(db, post_id, approved_by):
        calls.append(post_id)
        raise Reached()                                        # stop right at the approval step

    monkeypatch.setattr(cm, "get_revisions", fake_revs)
    monkeypatch.setattr(cm, "approve_post_content", approve)
    post = {"id": "3e253a49-0000-0000-0000-000000000000", "content": V4}
    with pytest.raises(Reached):
        run(con._approve_and_publish(None, post, force=True))   # the explicit override approves
    revs[1]["source"] = "web"                                  # an admin-panel edit is never treated as pasted notes
    with pytest.raises(Reached):
        run(con._approve_and_publish(None, post))
    assert len(calls) == 2
