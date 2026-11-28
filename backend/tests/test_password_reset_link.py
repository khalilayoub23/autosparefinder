"""Reset links: state is known before the form, each link sets a password once, the token never leaks (#163/#164).

#163: a used / expired / unknown link still showed the password form; the server refused only the submit, with the
English "Invalid or expired token". Now POST /api/v1/auth/reset-password/validate reports the link state (read-only,
no account data) and a failed confirm answers 400 with a Hebrew message + a reason code. The consume is one
conditional UPDATE under the user row lock, so two concurrent submits cannot both set a password.
#164: the token was shown in a visible text field (a password manager saved it as the "username") and rode in the
URL query (nginx access log, Referer). New links carry it in the fragment; a 422 never echoes submitted values.

DB tests run only against the isolated sandbox PII database (tests/isolated_db_guard.py); every row they create is
removed afterwards.
"""
import asyncio
import secrets
from datetime import datetime, timedelta

import pytest

from test_password_change_revokes_sessions import _access_ok, _new_user, _open_session, _run, needs_db

HEBREW = range(0x0590, 0x0600)


def _has_hebrew(text):
    return any(ord(ch) in HEBREW for ch in text)


async def _add_link(db, user, *, hours=1, used=False):
    from BACKEND_DATABASE_MODELS import PasswordReset
    tok = secrets.token_urlsafe(32)
    now = datetime.utcnow()
    db.add(PasswordReset(user_id=user.id, token=tok, expires_at=now + timedelta(hours=hours),
                         used_at=now if used else None))
    await db.commit()
    return tok


def _app():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from api_errors import validation_exception_handler
    from routes.auth import router
    app = FastAPI()
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    return app


def _client(app):
    import httpx
    # a fresh client IP per test, so the per-IP rate limits of other tests never interfere
    ip = f"10.{secrets.randbelow(250)}.{secrets.randbelow(250)}.{secrets.randbelow(250)}"
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t", headers={"X-Real-IP": ip})


# ---------------------------------------------------------------------------------------------- link state
@needs_db
def test_status_of_valid_used_expired_and_invalid_links_is_read_only():
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import PasswordReset
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "state"); created.append(user.id)
            valid = await _add_link(db, user)
            used = await _add_link(db, user, used=True)
            expired = await _add_link(db, user, hours=-1)
            used_and_expired = await _add_link(db, user, hours=-1, used=True)
            assert await sec.password_reset_token_status(valid, db) == "valid"
            assert await sec.password_reset_token_status(used, db) == "used"
            assert await sec.password_reset_token_status(expired, db) == "expired"
            assert await sec.password_reset_token_status(used_and_expired, db) == "used", "used wins over expired"
            for bad in ["", "short", "x" * 2000, "has spaces in it 1234567890", "a/b+c=d" * 5, None, 12345,
                        secrets.token_urlsafe(32)]:   # malformed ... and well-formed but unknown
                assert await sec.password_reset_token_status(bad, db) == "invalid", repr(bad)[:20]
            # reading the state changes nothing: the valid link is still unused and still works
            assert (await db.execute(select(PasswordReset.used_at).where(PasswordReset.token == valid))).scalar_one() is None
            assert await sec.use_password_reset_token(valid, "Reset1234!", db)
            assert await sec.password_reset_token_status(valid, db) == "used"
    _run(body)


@needs_db
def test_validate_endpoint_returns_only_the_state():
    async def body(sec, factory, created):
        async with factory() as db:
            user = await _new_user(db, sec, "validate"); created.append(user.id)
            links = {"valid": await _add_link(db, user), "used": await _add_link(db, user, used=True),
                     "expired": await _add_link(db, user, hours=-1), "invalid": secrets.token_urlsafe(32)}
        async with _client(_app()) as c:
            for want, tok in links.items():
                r = await c.post("/api/v1/auth/reset-password/validate", json={"token": tok})
                assert r.status_code == 200, r.text
                assert r.json() == {"status": want}, "the answer is the link state and nothing else"
                assert user.email not in r.text and str(user.id) not in r.text and tok not in r.text
            r = await c.post("/api/v1/auth/reset-password/validate", json={"token": "x" * 1025})
            assert r.status_code == 422 and "xxxx" not in r.text, "an oversized token is refused without an echo"
        async with factory() as db:   # validating did not consume the valid link
            assert await sec.password_reset_token_status(links["valid"], db) == "valid"
    _run(body)


@needs_db
def test_validate_endpoint_is_rate_limited_per_ip():
    async def body(sec, factory, created):
        codes = []
        async with _client(_app()) as c:
            for _ in range(21):
                r = await c.post("/api/v1/auth/reset-password/validate", json={"token": secrets.token_urlsafe(32)})
                codes.append(r.status_code)
        assert codes[:20] == [200] * 20 and codes[20] == 429, codes
    _run(body)


# ---------------------------------------------------------------------------------------------- confirm
@needs_db
def test_confirm_with_a_dead_link_answers_hebrew_with_the_reason_and_changes_nothing():
    async def body(sec, factory, created):
        async with factory() as db:
            user = await _new_user(db, sec, "dead"); created.append(user.id)
            session_a, _ = await _open_session(db, user, sec)
            links = {"used": await _add_link(db, user, used=True), "expired": await _add_link(db, user, hours=-1),
                     "invalid": secrets.token_urlsafe(32)}
            old_hash = user.password_hash
        new_pw = "Fresh-" + secrets.token_urlsafe(9)
        async with _client(_app()) as c:
            for reason, tok in links.items():
                r = await c.post("/api/v1/auth/reset-password/confirm", json={"token": tok, "new_password": new_pw})
                assert r.status_code == 400, r.text
                body_ = r.json()
                assert body_["reason"] == reason and _has_hebrew(body_["error"]), body_
                assert tok not in r.text and new_pw not in r.text and user.email not in r.text
            # malformed token -> the same refusal, reason "invalid"
            r = await c.post("/api/v1/auth/reset-password/confirm", json={"token": "nope", "new_password": new_pw})
            assert r.status_code == 400 and r.json()["reason"] == "invalid"
        async with factory() as db:
            from BACKEND_DATABASE_MODELS import User
            from sqlalchemy import select
            assert (await db.execute(select(User.password_hash).where(User.id == user.id))).scalar_one() == old_hash
            assert await _access_ok(db, sec, session_a), "a refused reset revokes nothing"
    _run(body)


@needs_db
def test_valid_link_resets_once_revokes_sessions_and_the_second_use_says_used():
    async def body(sec, factory, created):
        async with factory() as db:
            user = await _new_user(db, sec, "once"); created.append(user.id)
            session_a, _ = await _open_session(db, user, sec)
            tok = await _add_link(db, user)
        new_pw = "Fresh-" + secrets.token_urlsafe(9)
        async with _client(_app()) as c:
            r = await c.post("/api/v1/auth/reset-password/confirm", json={"token": tok, "new_password": new_pw})
            assert r.status_code == 200, r.text
            r2 = await c.post("/api/v1/auth/reset-password/validate", json={"token": tok})
            assert r2.json() == {"status": "used"}, "re-opening the link reports it as used BEFORE any form"
            r3 = await c.post("/api/v1/auth/reset-password/confirm", json={"token": tok, "new_password": "Other-1234567"})
            assert r3.status_code == 400 and r3.json()["reason"] == "used"
        async with factory() as db:
            from BACKEND_DATABASE_MODELS import User
            from sqlalchemy import select
            stored = (await db.execute(select(User.password_hash).where(User.id == user.id))).scalar_one()
            assert sec.verify_password(new_pw, stored), "the first submit set the password; the second did not"
            assert not await _access_ok(db, sec, session_a), "#162: a reset still revokes every session"
    _run(body)


@needs_db
def test_two_concurrent_submits_of_one_link_set_the_password_once():
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import User
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "race1"); created.append(user.id)
            tok = await _add_link(db, user)
        async with factory() as tx1, factory() as tx2:
            r = await asyncio.wait_for(asyncio.gather(
                sec.use_password_reset_token(tok, "RaceOne-12345", tx1),
                sec.use_password_reset_token(tok, "RaceTwo-12345", tx2)), 20)
        assert sorted(r) == [False, True], r
        async with factory() as db:
            stored = (await db.execute(select(User.password_hash).where(User.id == user.id))).scalar_one()
            winner = "RaceOne-12345" if r[0] else "RaceTwo-12345"
            assert sec.verify_password(winner, stored)
    _run(body)


@needs_db
def test_two_links_of_one_user_submitted_concurrently_one_wins_without_deadlock():
    async def body(sec, factory, created):
        async with factory() as db:
            user = await _new_user(db, sec, "race2"); created.append(user.id)
            tok_a = await _add_link(db, user)
            tok_b = await _add_link(db, user)
        async with factory() as tx1, factory() as tx2:
            r = await asyncio.wait_for(asyncio.gather(
                sec.use_password_reset_token(tok_a, "LinkA-1234567", tx1),
                sec.use_password_reset_token(tok_b, "LinkB-1234567", tx2)), 20)
        assert sorted(r) == [False, True], "the first reset consumes the other link (#162); no deadlock"
        async with factory() as db:
            assert {await sec.password_reset_token_status(t, db) for t in (tok_a, tok_b)} == {"used"}
    _run(body)


# ---------------------------------------------------------------------------------------------- leaks (#164)
def test_reset_link_carries_the_token_in_the_fragment(monkeypatch):
    import BACKEND_AUTH_SECURITY as sec
    monkeypatch.setenv("FRONTEND_URL", "https://example.test/")
    link = sec.password_reset_link("TOKEN123")
    assert link == "https://example.test/reset-password#token=TOKEN123"
    assert "?token=" not in link


@needs_db
def test_creating_a_link_never_prints_the_token_or_link(monkeypatch, capsys):
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import User
        import routes.email_utils as email_utils
        sent = {}

        async def fake_send(to, name, tpl):
            sent["html"] = tpl[1]
            raise RuntimeError("provider failure that quotes the message " + tpl[2])
        monkeypatch.setattr(email_utils, "send_template", fake_send)
        async with factory() as db:
            user = await _new_user(db, sec, "mail"); created.append(user.id)
            tok = await sec.create_password_reset_token(user.email, db)
        assert tok and "#token=" + tok in sent["html"], "the email carries the fragment link"
        out = capsys.readouterr()
        assert tok not in out.out and tok not in out.err, "the token must never reach stdout/stderr"
    _run(body)


@needs_db
def test_validation_errors_never_echo_submitted_values():
    import asyncio as _asyncio

    async def run():
        async with _client(_app()) as c:
            short_pw = "Sh0rt!"
            r = await c.post("/api/v1/auth/reset-password/confirm", json={"token": "tok-SECRET-value", "new_password": short_pw})
            assert r.status_code == 422
            assert short_pw not in r.text and "tok-SECRET-value" not in r.text, r.text
            detail = r.json()["detail"]
            assert detail and all("input" not in e and "ctx" not in e for e in detail)
            assert detail[0]["loc"][-1] == "new_password" and detail[0]["msg"], "shape kept: loc + msg"
    _asyncio.run(run())


def test_create_password_reset_token_source_logs_no_link():
    import ast
    import inspect
    import BACKEND_AUTH_SECURITY as sec
    tree = ast.parse(inspect.getsource(sec.create_password_reset_token))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "print":
            names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
            assert not names & {"token", "reset_link", "email"}, "a print in create_password_reset_token leaks secrets"
            # the exception may be named only by its type: its text can quote the message (and the link)
            only_type = {id(c.args[0]) for c in ast.walk(node) if isinstance(c, ast.Call)
                         and getattr(c.func, "id", "") == "type" and c.args}
            bare_exc = [n for n in ast.walk(node) if isinstance(n, ast.Name) and n.id == "exc" and id(n) not in only_type]
            assert not bare_exc, "print the exception type only, never str(exc)"
