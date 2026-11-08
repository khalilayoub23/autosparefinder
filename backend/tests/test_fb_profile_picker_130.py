"""#130: the Facebook account picker. The stored profile is selected deterministically, a login that ends as another
c_user is rejected, another tile is never clicked, and a rejected picker login is never retried by the legacy login."""
import asyncio
import json

_real_sleep = asyncio.sleep

import pytest

from social.facebook_browser import session as S


class _Btn:
    def __init__(self, page, name): self.page, self.name = page, name
    @property
    def first(self): return self
    async def click(self): self.page.clicked.append(self.name)


class _Pass:
    async def fill(self, v): pass
    async def press(self, k): pass


class _Page:
    url = "https://www.facebook.com/"
    def __init__(self, tiles): self.tiles, self.clicked = tiles, []
    async def evaluate(self, js): return self.tiles
    def get_by_role(self, role, name=None, exact=False): return _Btn(self, name)
    async def wait_for_selector(self, sel, **kw): return _Pass()


class _Ctx:
    def __init__(self, cookies): self._c, self.cleared = cookies, False
    async def cookies(self): return [{"name": k, "value": v, "domain": ".facebook.com"} for k, v in self._c.items()]
    async def clear_cookies(self): self.cleared = True


def _session(tiles, cookies):
    s = S.FacebookSession()
    s._page, s._context = _Page(tiles), _Ctx(cookies)
    return s


@pytest.fixture(autouse=True)
def _profile(tmp_path, monkeypatch):
    f = tmp_path / "sel.json"
    f.write_text(json.dumps({"name": "Khalil Ayoub", "uid": "100003155460373"}))
    monkeypatch.setattr(S, "_SELECTED_PROFILE_FILE", f)
    monkeypatch.setenv("FB_PASSWORD", "pw")
    monkeypatch.setattr(S, "_REJECTED_FILE", tmp_path / "rej.json")
    monkeypatch.delenv("FB_PROFILE_NAME", raising=False)
    monkeypatch.delenv("FB_EXPECTED_UID", raising=False)
    monkeypatch.setattr(S.asyncio, "sleep", lambda *_: _real_sleep(0))


def test_selected_profile_and_uid_guard():
    assert S.selected_profile() == {"name": "Khalil Ayoub", "uid": "100003155460373"}
    assert S.uid_matches_selected("100003155460373") and not S.uid_matches_selected("999")


def test_clicks_only_the_stored_tile_and_accepts_matching_uid():
    s = _session(["Khalil Ayoub", "Fast Epost"], {"c_user": "100003155460373", "xs": "x"})
    assert asyncio.run(s._login_via_picker()) is True
    assert s._page.clicked == ["Khalil Ayoub"]


def test_never_falls_back_to_another_tile():
    s = _session(["Fast Epost"], {})
    assert asyncio.run(s._login_via_picker()) is False
    assert s._page.clicked == []


def test_login_as_a_different_profile_is_rejected():
    s = _session(["Khalil Ayoub", "Fast Epost"], {"c_user": "555", "xs": "x"})
    assert asyncio.run(s._login_via_picker()) is False
    assert s._context.cleared


def test_rejected_password_gives_false_not_exception():
    s = _session(["Khalil Ayoub"], {"datr": "d"})
    assert asyncio.run(s._login_via_picker()) is False


def test_picker_failure_never_reaches_legacy_login():
    import inspect
    src = inspect.getsource(S.FacebookSession._start)
    assert src.index("_login_via_picker") < src.index("not retrying with the legacy login") < src.index("_try_auto_relogin()")


def test_rejected_password_is_not_sent_twice():
    s = _session(["Khalil Ayoub"], {"datr": "d"})
    assert asyncio.run(s._login_via_picker()) is False and S.password_already_rejected("pw")
    s2 = _session(["Khalil Ayoub"], {"c_user": "100003155460373", "xs": "x"})
    assert asyncio.run(s2._login_via_picker()) is False and s2._page.clicked == []   # skipped, nothing clicked
