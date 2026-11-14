"""TikTok publishing must use the owner's USER token with video.publish (2026-11-13).
Live failure: client_credentials token -> POST /post/publish/video/init/ -> 401 access_token_invalid."""
import asyncio

from social import tiktok_publisher as tp


def _run(coro):
    return asyncio.run(coro)


def test_authorize_url_requests_only_scopes_the_app_is_approved_for(monkeypatch):
    """The live app has user.info.basic + video.upload; video.publish/video.list give error=invalid_scope."""
    monkeypatch.delenv("TIKTOK_DIRECT_POST", raising=False)
    scope = tp.get_authorize_url("s").split("scope=")[1].split("&")[0].replace("%2C", ",")
    assert scope == "user.info.basic,video.upload"


def test_publish_uses_the_draft_inbox_endpoint(monkeypatch):
    seen = {}

    class _Resp:
        def __init__(self, code, data): self.status_code, self._d = code, data
        def json(self): return self._d

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            seen["url"], seen["json"] = url, kw["json"]
            return _Resp(200, {"data": {"publish_id": "P1", "upload_url": "https://up/x"}})
        async def put(self, url, **kw): return _Resp(200, {})
    monkeypatch.setattr(tp.httpx, "AsyncClient", _Client)
    r = _run(tp.publish_video(b"abc", "cap", [], access_token="T"))
    assert r == {"ok": True, "publish_id": "P1", "error": None, "draft": True}
    assert seen["url"].endswith("/post/publish/inbox/video/init/") and "post_info" not in seen["json"]


def test_no_user_token_is_a_reconnect_message_never_an_app_token_call(monkeypatch):
    async def none_token():
        return None, tp.needs_reconnect()
    monkeypatch.setattr(tp, "get_publish_token", none_token)

    assert not hasattr(tp, "get_access_token"), "the client_credentials publish path is gone"
    r = _run(tp.publish_video(b"x", "c", []))
    assert r["ok"] is False and r["error"].lower().startswith("no access token") and "טוקן טיקטוק" in r["error"]
    r2 = _run(tp.post_text_content("c", []))
    assert r2["ok"] is False and r2["error"].lower().startswith("no access token")


def test_token_without_publish_scope_is_refused(monkeypatch):
    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False
    import BACKEND_DATABASE_MODELS as m
    monkeypatch.setattr(m, "async_session_factory", lambda: _S())

    async def stored(db):
        return {"access_token": "a", "scope": "user.info.basic"}
    monkeypatch.setattr(tp, "get_stored_user_token", stored)
    token, reason = _run(tp.get_publish_token())
    assert token is None and "lacks video.upload" in reason


def test_token_with_publish_scope_is_used(monkeypatch):
    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False
    import BACKEND_DATABASE_MODELS as m
    monkeypatch.setattr(m, "async_session_factory", lambda: _S())

    async def stored(db):
        return {"access_token": "a", "scope": "user.info.basic,video.upload"}

    async def valid(db):
        return "USERTOKEN"
    monkeypatch.setattr(tp, "get_stored_user_token", stored)
    monkeypatch.setattr(tp, "get_valid_user_access_token", valid)
    assert _run(tp.get_publish_token()) == ("USERTOKEN", "")


def _fake_client(monkeypatch):
    seen = {}

    class _Resp:
        def __init__(self, code, data): self.status_code, self._d = code, data
        def json(self): return self._d

    class _Client:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, **kw):
            seen["url"], seen["json"] = url, kw["json"]
            return _Resp(200, {"data": {"publish_id": "P1", "upload_url": "https://up/x"}})
        async def put(self, url, **kw): return _Resp(200, {})
    monkeypatch.setattr(tp.httpx, "AsyncClient", _Client)
    return seen


def test_toggle_off_is_draft_upload_with_upload_scope(monkeypatch):
    monkeypatch.delenv("TIKTOK_DIRECT_POST", raising=False)
    assert tp.consent_scopes() == "user.info.basic,video.upload" and not tp.direct_post_enabled()


def test_direct_post_toggle_uses_publish_scope_endpoint_and_private_default(monkeypatch):
    monkeypatch.setenv("TIKTOK_DIRECT_POST", "1")
    monkeypatch.delenv("TIKTOK_PRIVACY_LEVEL", raising=False)
    assert tp.consent_scopes() == "user.info.basic,video.publish"
    seen = _fake_client(monkeypatch)
    r = _run(tp.publish_video(b"abc", "cap", ["x"], access_token="T"))
    assert r["ok"] and r["draft"] is False
    assert seen["url"].endswith("/post/publish/video/init/") and "/inbox/" not in seen["url"]
    assert seen["json"]["post_info"]["privacy_level"] == "SELF_ONLY", "unaudited apps may only post privately"


def test_privacy_level_is_validated(monkeypatch):
    monkeypatch.setenv("TIKTOK_DIRECT_POST", "1")
    monkeypatch.setenv("TIKTOK_PRIVACY_LEVEL", "public_to_everyone")
    assert tp.direct_post_privacy() == "PUBLIC_TO_EVERYONE"
    monkeypatch.setenv("TIKTOK_PRIVACY_LEVEL", "everyone-please")
    assert tp.direct_post_privacy() == "SELF_ONLY"


def test_old_upload_token_is_refused_once_direct_post_is_on(monkeypatch):
    monkeypatch.setenv("TIKTOK_DIRECT_POST", "1")

    class _S:
        async def __aenter__(self): return object()
        async def __aexit__(self, *a): return False
    import BACKEND_DATABASE_MODELS as m
    monkeypatch.setattr(m, "async_session_factory", lambda: _S())

    async def stored(db):
        return {"access_token": "a", "scope": "user.info.basic,video.upload"}
    monkeypatch.setattr(tp, "get_stored_user_token", stored)
    token, reason = _run(tp.get_publish_token())
    assert token is None and "video.publish" in reason and "טוקן טיקטוק" in reason
