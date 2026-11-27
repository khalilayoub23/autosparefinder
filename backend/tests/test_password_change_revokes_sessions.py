"""A password change / reset ends everything opened with the old password (FIXES_TRACKER #162).

Before the fix, change_password and use_password_reset_token only replaced the hash: every session opened with
the old password (access AND refresh token) kept working, as did pending reset links, pending 2FA codes and the
72-hour abandoned-cart login link. Now (single enforcement point: invalidate_after_password_change):
  * change-password revokes every OTHER session; the session of the request that changed it stays valid;
  * reset-password confirm revokes ALL sessions;
  * other unused reset links and pending 2FA codes are consumed/expired;
  * cart-recovery links are bound to the password and stop working after a change;
  * a refresh or a login racing the change cannot leave a surviving session (row lock + hash re-check);
  * long-lived connections (SSE, WebSocket) see the revocation (session_is_active).

DB tests run only against the isolated sandbox PII database (tests/isolated_db_guard.py); every row they create
is removed afterwards. The AST guard runs everywhere.
"""
import ast
import asyncio
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import isolated_db_guard  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
needs_db = pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                              reason=f"isolated DB only: {isolated_db_guard.static_refusal()}")


# ---------------------------------------------------------------------------------------------- helpers
async def _new_user(db, sec, tag, pw="OldPassw0rd!"):
    from BACKEND_DATABASE_MODELS import User
    user = User(email=f"pwrevoke-{tag}-{secrets.token_hex(5)}@test.invalid", full_name="pw revoke test",
                password_hash=sec.hash_password(pw), role="customer", is_active=True, is_verified=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _open_session(db, user, sec, trusted=False):
    sid = secrets.token_hex(16)
    access = sec.create_access_token(str(user.id), sid)
    refresh = sec.create_refresh_token(str(user.id), sid)
    await sec.create_session(user, access, refresh, "fp-" + sid, "127.0.0.1", "pytest", trusted, db)
    return access, refresh


async def _access_ok(db, sec, token):
    try:
        await sec.get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
        return True
    except HTTPException as exc:
        assert exc.status_code == 401
        return False


async def _refresh_ok(db, sec, refresh):
    try:
        await sec.refresh_access_token(refresh, db)
        return True
    except HTTPException as exc:
        assert exc.status_code == 401
        return False


def _run(coro_fn):
    async def wrapper():
        import BACKEND_AUTH_SECURITY as sec
        from BACKEND_DATABASE_MODELS import User, pii_engine, pii_session_factory
        from sqlalchemy import delete
        assert pii_engine.url.host == "sandbox_postgres", f"REFUSED: PII host {pii_engine.url.host!r}"
        created = []
        try:
            await coro_fn(sec, pii_session_factory, created)
        finally:
            async with pii_session_factory() as db:
                for uid in created:
                    await db.execute(delete(User).where(User.id == uid))   # sessions / resets / 2FA cascade
                await db.commit()
            await pii_engine.dispose()
    asyncio.run(wrapper())


# ---------------------------------------------------------------------------------------------- tests
@needs_db
def test_change_keeps_current_revokes_others_and_scopes_to_user():
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import UserSession
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "chg"); created.append(user.id)
            other_user = await _new_user(db, sec, "bystander"); created.append(other_user.id)
            cur_a, cur_r = await _open_session(db, user, sec)
            oth_a, oth_r = await _open_session(db, user, sec)
            tru_a, _ = await _open_session(db, user, sec, trusted=True)
            by_a, by_r = await _open_session(db, other_user, sec)
            # an already-revoked row keeps its original revoked_at
            old_a, _ = await _open_session(db, user, sec)
            await sec.revoke_session(old_a, db)
            old_rev = (await db.execute(select(UserSession.revoked_at).where(UserSession.token == old_a))).scalar_one()

            # wrong current password: nothing changes, nothing is revoked
            with pytest.raises(HTTPException):
                await sec.change_password(user, "WrongPass1!", "Other1234!", db, current_token=cur_a)
            assert await _access_ok(db, sec, oth_a)

            await sec.change_password(user, "OldPassw0rd!", "NewPassw0rd!", db, current_token=cur_a)
            assert sec.verify_password("NewPassw0rd!", user.password_hash)
            assert await _access_ok(db, sec, cur_a), "the session that changed the password must stay valid"
            assert not await _access_ok(db, sec, oth_a) and not await _refresh_ok(db, sec, oth_r)
            assert not await _access_ok(db, sec, tru_a), "a trusted device must be revoked too"
            trusted_left = (await db.execute(select(UserSession.id).where(
                UserSession.user_id == user.id, UserSession.is_trusted_device == True,   # noqa: E712
                UserSession.revoked_at.is_(None)))).first()
            assert trusted_left is None, "no trusted-device row may survive (it would skip 2FA)"
            assert (await db.execute(select(UserSession.revoked_at).where(UserSession.token == old_a))).scalar_one() == old_rev
            assert await _access_ok(db, sec, by_a) and await _refresh_ok(db, sec, by_r), "another user is untouched"

            # without a current token every session is revoked (fails closed)
            await sec.change_password(user, "NewPassw0rd!", "Newer1234!", db, current_token=None)
            assert not await _access_ok(db, sec, cur_a)
    _run(body)


@needs_db
def test_reset_revokes_all_and_consumes_other_links_and_2fa_codes():
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import PasswordReset, TwoFactorCode
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "reset"); created.append(user.id)
            a1, r1 = await _open_session(db, user, sec)
            a2, _ = await _open_session(db, user, sec)
            tok, other_tok = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            exp = datetime.utcnow() + timedelta(hours=1)
            db.add_all([PasswordReset(user_id=user.id, token=tok, expires_at=exp),
                        PasswordReset(user_id=user.id, token=other_tok, expires_at=exp),
                        TwoFactorCode(user_id=user.id, code="x" * 64, expires_at=exp)])
            await db.commit()
            assert await sec.use_password_reset_token(tok, "Reset1234!", db)
            await db.refresh(user)
            assert sec.verify_password("Reset1234!", user.password_hash)
            assert not await _access_ok(db, sec, a1) and not await _access_ok(db, sec, a2)
            assert not await _refresh_ok(db, sec, r1)
            assert not await sec.use_password_reset_token(other_tok, "Again1234!", db), "other reset links are consumed"
            live_codes = (await db.execute(select(TwoFactorCode.id).where(
                TwoFactorCode.user_id == user.id, TwoFactorCode.verified_at.is_(None),
                TwoFactorCode.expires_at > datetime.utcnow()))).first()
            assert live_codes is None, "pending 2FA codes are expired"
            new_a, _ = await _open_session(db, user, sec)
            assert await _access_ok(db, sec, new_a), "a fresh login after the reset works"
    _run(body)


@needs_db
def test_change_password_http_endpoint_keeps_the_calling_session():
    async def body(sec, factory, created):
        import httpx
        from fastapi import FastAPI
        from routes.auth import router
        app = FastAPI()
        app.include_router(router)
        async with factory() as db:
            user = await _new_user(db, sec, "http"); created.append(user.id)
            cur_a, _ = await _open_session(db, user, sec)
            oth_a, _ = await _open_session(db, user, sec)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/v1/auth/change-password", headers={"Authorization": f"Bearer {cur_a}"},
                             json={"current_password": "OldPassw0rd!", "new_password": "NewPassw0rd!"})
            assert r.status_code == 200, r.text
            assert (await c.get("/api/v1/auth/trusted-devices", headers={"Authorization": f"Bearer {cur_a}"})).status_code == 200
            assert (await c.get("/api/v1/auth/trusted-devices", headers={"Authorization": f"Bearer {oth_a}"})).status_code == 401
        assert await sec.session_is_active(cur_a) and not await sec.session_is_active(oth_a)
    _run(body)


@needs_db
def test_refresh_and_login_racing_a_change_leave_no_surviving_session():
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import User
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "race"); created.append(user.id)
            _, oth_r = await _open_session(db, user, sec)
        async with factory() as tx_change, factory() as tx_refresh, factory() as tx_login:
            u_change = (await tx_change.execute(select(User).where(User.id == user.id))).scalar_one()
            u_login = (await tx_login.execute(select(User).where(User.id == user.id))).scalar_one()  # "authenticated"
            # the change is in flight (hash written, sessions revoked) but NOT committed yet
            u_change.password_hash = sec.hash_password("NewPassw0rd!")
            await sec.invalidate_after_password_change(user.id, tx_change)
            refresh_task = asyncio.create_task(sec.refresh_access_token(oth_r, tx_refresh))
            sid = secrets.token_hex(16)
            login_task = asyncio.create_task(sec.create_session(
                u_login, sec.create_access_token(str(user.id), sid), sec.create_refresh_token(str(user.id), sid),
                "fp-race", "127.0.0.1", "pytest", False, tx_login))
            await asyncio.sleep(1.0)
            assert not refresh_task.done() and not login_task.done(), "both must wait for the in-flight change"
            await tx_change.commit()
            with pytest.raises(HTTPException) as e1:
                await asyncio.wait_for(refresh_task, 10)
            assert e1.value.status_code == 401, "the refresh must see the revocation"
            with pytest.raises(HTTPException) as e2:
                await asyncio.wait_for(login_task, 10)
            assert e2.value.status_code == 401, "a login authenticated with the old password must be refused"
    _run(body)


@needs_db
def test_login_and_refresh_that_commit_first_are_revoked_by_the_change():
    """Reverse order of the race: the login / refresh commits first, the change (which then waits for their
    FOR SHARE lock) must still revoke the session they created."""
    async def body(sec, factory, created):
        from BACKEND_DATABASE_MODELS import User
        from sqlalchemy import select
        async with factory() as db:
            user = await _new_user(db, sec, "race2"); created.append(user.id)
            _, oth_r = await _open_session(db, user, sec)
        async with factory() as tx_refresh, factory() as tx_login, factory() as tx_change:
            new_access, _ = await sec.refresh_access_token(oth_r, tx_refresh)          # committed first
            u_login = (await tx_login.execute(select(User).where(User.id == user.id))).scalar_one()
            sid = secrets.token_hex(16)
            login_access = sec.create_access_token(str(user.id), sid)
            await sec.create_session(u_login, login_access, sec.create_refresh_token(str(user.id), sid),
                                     "fp-race2", "127.0.0.1", "pytest", False, tx_login)   # committed first
            u_change = (await tx_change.execute(select(User).where(User.id == user.id))).scalar_one()
            u_change.password_hash = sec.hash_password("NewPassw0rd!")
            await sec.invalidate_after_password_change(user.id, tx_change)
            await tx_change.commit()
        assert not await sec.session_is_active(new_access), "the refreshed session must be revoked by the change"
        assert not await sec.session_is_active(login_access), "the login session must be revoked by the change"
    _run(body)


@needs_db
def test_cart_recovery_link_dies_with_a_password_change():
    async def body(sec, factory, created):
        async with factory() as db:
            user = await _new_user(db, sec, "cart"); created.append(user.id)
            old_link = sec.create_cart_recovery_token(str(user.id), user.password_hash)
            uid, fp = sec.verify_cart_recovery_token(old_link)
            assert uid == str(user.id) and fp == sec.password_fingerprint(user.password_hash)
            await sec.change_password(user, "OldPassw0rd!", "NewPassw0rd!", db)
            uid, fp = sec.verify_cart_recovery_token(old_link)
            assert fp != sec.password_fingerprint(user.password_hash), "a link from before the change must not match"
            new_link = sec.create_cart_recovery_token(str(user.id), user.password_hash)
            assert sec.verify_cart_recovery_token(new_link)[1] == sec.password_fingerprint(user.password_hash)
    _run(body)


def test_legacy_cart_token_without_password_binding_is_refused():
    import base64
    import hashlib
    import hmac
    import time
    import BACKEND_AUTH_SECURITY as sec
    body = f"cart.00000000-0000-0000-0000-000000000000.{int(time.time()) + 3600}"
    sig = hmac.new(sec.JWT_SECRET_KEY.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    legacy = base64.urlsafe_b64encode(f"{body}.{sig}".encode()).decode().rstrip("=")
    assert sec.verify_cart_recovery_token(legacy) is None


def test_every_existing_user_password_write_goes_through_the_single_enforcement_point():
    """AST guard: an assignment `<obj>.password_hash = ...` (an EXISTING user) must sit in a function that calls
    invalidate_after_password_change. New users are built with User(password_hash=...) keywords - not covered."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        rel = path.relative_to(BACKEND).as_posix()
        if rel.startswith(("tests/", "legacy/", "devtests/")):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            writes = [n for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))
                      for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                      if isinstance(t, ast.Attribute) and t.attr == "password_hash"]
            if not writes:
                continue
            calls = {getattr(c.func, "id", getattr(c.func, "attr", None)) for c in ast.walk(fn) if isinstance(c, ast.Call)}
            if "invalidate_after_password_change" not in calls:
                offenders.append(f"{rel}:{fn.name}")
    assert not offenders, f"password_hash written without invalidate_after_password_change: {offenders}"
