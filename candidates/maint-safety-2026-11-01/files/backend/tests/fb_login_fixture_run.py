"""Helper (NOT a test): drives the REAL fb_browser_login.login() in real headless Chromium against a CONTROLLED fixture - no network,
no Facebook, no credentials. Every request to *.facebook.com is answered locally by a Playwright route:
  mode=authenticated   -> a page without a login form + Set-Cookie c_user / xs  (what a successful Facebook login looks like to login())
  mode=unauthenticated -> a page WITH the login form and no session cookies   (failed / checkpointed login)
This exercises login()'s real health check and its real cookie-writing branch. It is NOT a Facebook authentication: the cookie
values are fixture markers, never real. Prints ONE JSON line: login()'s result + cookie file facts (names/count/mode, never values).
usage: python3 fb_login_fixture_run.py <app_root> <authenticated|unauthenticated>   (STATE_DIR / FB_* come from the environment)"""
import asyncio
import importlib.util
import json
import os
import sys
import types

APP, MODE = sys.argv[1], sys.argv[2]
sys.path.insert(0, APP)
os.environ.pop("FB_EMAIL", None)
os.environ.pop("FB_PASSWORD", None)

SECRET = "FIXTURE-COOKIE-VALUE-DO-NOT-LOG"
_OK_PAGE = "<html><body><h1>fixture feed</h1><a href='/groups'>groups</a></body></html>"
_LOGIN_PAGE = "<html><body><form name='login'><input id='email'><input id='pass' type='password'></form></body></html>"

from playwright.async_api import Browser  # noqa: E402

_orig_new_context = Browser.new_context


async def _new_context(self, *a, **k):
    ctx = await _orig_new_context(self, *a, **k)

    async def handler(route):
        url = route.request.url
        if "facebook.com" not in url:
            await route.abort()
            return
        headers = {"content-type": "text/html"}
        body = _LOGIN_PAGE
        if MODE == "authenticated":
            body = _OK_PAGE
            headers["set-cookie"] = "\n".join([
                f"c_user={SECRET}; Domain=.facebook.com; Path=/; Secure",
                f"xs={SECRET}; Domain=.facebook.com; Path=/; Secure",
                f"datr={SECRET}; Domain=.facebook.com; Path=/",
            ])
        await route.fulfill(status=200, headers=headers, body=body)

    await ctx.route("**/*", handler)
    return ctx


Browser.new_context = _new_context

spec = importlib.util.spec_from_file_location("fb_browser_login_under_test", os.path.join(APP, "social/facebook_browser/fb_browser_login.py"))
bl = importlib.util.module_from_spec(spec)
sys.modules["fb_browser_login_under_test"] = bl
spec.loader.exec_module(bl)

# login() waits 120 s for a human when it has no credentials and no TTY: skip only that wait, keep everything else real.
_fast = types.SimpleNamespace(**{k: getattr(asyncio, k) for k in dir(asyncio) if not k.startswith("__")})


async def _sleep(_s, *a, **k):
    await asyncio.sleep(0)


_fast.sleep = _sleep
bl.asyncio = _fast

res = asyncio.run(bl.login())
cf = bl._COOKIES_FILE
info = {"login_returned": res, "cookies_file": str(cf), "exists": cf.exists()}
if cf.exists():
    data = json.loads(cf.read_text(encoding="utf-8"))
    info.update(count=len(data), names=sorted(c["name"] for c in data), mode=oct(cf.stat().st_mode & 0o777), uid=cf.stat().st_uid,
                has_secret_marker=SECRET in cf.read_text(encoding="utf-8"))
print(json.dumps(info))
