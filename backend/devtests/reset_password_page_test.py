"""
Script: devtests/reset_password_page_test.py
Purpose: Real-browser test of the password-reset page (FIXES_TRACKER #163/#164), mobile (Pixel 5) and desktop.
         Mode "local" (default): serves a built frontend dist/ and answers /api/v1/auth/* with fixed responses
         through Playwright request interception - no server, no database, no real account.
         Mode "live": the same page checks against a deployed site with REAL disposable links whose tokens are
         read from a JSON file ({"valid": ..., "used": ..., "expired": ...}); never pass a real customer's link.
         Checks per link state:
           1. used / expired / malformed / unknown link -> the Hebrew "link not valid" panel BEFORE any password
              field; the "send me a new link" button leads to the email request form;
           2. valid link -> two new-password fields, no text field, no token anywhere in the DOM or inputs;
           3. the token is gone from the address bar after load (fragment AND legacy ?token= links);
           4. a submit after the link died while the page was open -> the invalid panel (local mode);
           5. validate endpoint unreachable -> "cannot check right now" + retry (local mode);
           6. a valid submit -> success panel (local mode; live mode only with --live-submit).
Usage:
  python3 devtests/reset_password_page_test.py --dist /path/to/frontend/dist
  python3 devtests/reset_password_page_test.py --live https://autosparefinder.co.il --tokens tokens.json [--live-submit]
Data Imported/Modified: none in local mode; live mode with --live-submit sets the password of the disposable
         account that owns the "valid" token (and so consumes its links).
Last Updated: 2026-11-28
"""
import argparse
import json
import mimetypes
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

INVALID_TITLES = {
    "used": "הקישור כבר אינו בתוקף",
    "expired": "פג תוקף הקישור",
    "invalid": "הקישור אינו תקין",
    "unavailable": "לא ניתן לבדוק את הקישור כרגע",
}
NEW_LINK_BUTTON = "שלח לי קישור חדש"
REQUEST_FORM_TITLE = "שכחת סיסמה?"
RESET_FORM_TITLE = "בחירת סיסמה חדשה"
DONE_TITLE = "הסיסמה שונתה בהצלחה!"

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  - ' + detail) if detail and not ok else ''}")


def no_token_visible(page, token):
    values = page.eval_on_selector_all("input", "els => els.map(e => e.value)")
    return token not in page.content() and all(token not in v for v in values) and token not in page.url


class LocalSite:
    """Serves dist/ for every non-API path (SPA fallback) and fixed answers for the reset API."""

    def __init__(self, dist, states):
        self.dist = Path(dist)
        self.states = states          # token -> validate status
        self.confirm_reason = {}      # token -> reason for a 400 (simulates a link that dies while open)
        self.validate_down = False
        self.validate_missing = False   # backend without /validate (rollout order / rollback)
        self.confirm_bodies = []

    def handle(self, route):
        req = route.request
        url = urlparse(req.url)
        if url.hostname != "reset.test":
            return route.abort()          # no external request in local mode (fonts, analytics)
        path = url.path
        if path == "/api/v1/auth/reset-password/validate":
            if self.validate_down:
                return route.fulfill(status=503, body="{}", content_type="application/json")
            if self.validate_missing:
                return route.fulfill(status=404, body='{"detail":"Not Found"}', content_type="application/json")
            tok = json.loads(req.post_data or "{}").get("token")
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"status": self.states.get(tok, "invalid")}))
        if path == "/api/v1/auth/reset-password/confirm":
            body = json.loads(req.post_data or "{}")
            self.confirm_bodies.append(body)
            tok = body.get("token")
            if tok in self.confirm_reason or self.states.get(tok) != "valid":
                reason = self.confirm_reason.get(tok) or self.states.get(tok, "invalid")
                return route.fulfill(status=400, content_type="application/json", body=json.dumps(
                    {"error": "הקישור לאיפוס הסיסמה אינו בתוקף. יש לבקש קישור חדש.", "reason": reason, "status_code": 400}))
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"message": "ok"}))
        if path.startswith("/api/"):
            return route.fulfill(status=401, body="{}", content_type="application/json")
        f = self.dist / path.lstrip("/")
        if not f.is_file():
            f = self.dist / "index.html"
        return route.fulfill(status=200, body=f.read_bytes(),
                             content_type=mimetypes.guess_type(str(f))[0] or "application/octet-stream")


def open_link(page, base, token, legacy=False):
    url = f"{base}/reset-password?token={token}" if legacy else f"{base}/reset-password#token={token}"
    page.goto(url, wait_until="networkidle")
    page.wait_for_selector("[data-testid=reset-link-invalid], #resetpassword-f3", timeout=15000)


def check_dead_link(page, base, label, token, want_title, legacy=False):
    open_link(page, base, token, legacy)
    panel = page.locator("[data-testid=reset-link-invalid]")
    check(f"{label}: invalid panel shown", panel.count() == 1)
    check(f"{label}: Hebrew title '{want_title}'", page.get_by_text(want_title, exact=True).count() == 1)
    check(f"{label}: no password field", page.locator("input[type=password]").count() == 0)
    check(f"{label}: no token in DOM/inputs/URL", no_token_visible(page, token))
    page.get_by_text(NEW_LINK_BUTTON, exact=True).click()
    check(f"{label}: 'new link' button -> email request form",
          page.get_by_text(REQUEST_FORM_TITLE, exact=True).count() == 1 and page.locator("input[type=email]").count() == 1)


def check_valid_link(page, base, label, token, legacy=False):
    open_link(page, base, token, legacy)
    check(f"{label}: reset form shown", page.get_by_text(RESET_FORM_TITLE, exact=True).count() == 1)
    pw = page.locator("input[type=password]")
    check(f"{label}: exactly two password fields", pw.count() == 2)
    check(f"{label}: both autocomplete=new-password",
          pw.evaluate_all("els => els.every(e => e.autocomplete === 'new-password')"))
    check(f"{label}: no text/email input (nothing a password manager can save as username)",
          page.locator("input:not([type=password])").count() == 0)
    check(f"{label}: no token in DOM/inputs/URL", no_token_visible(page, token))
    check(f"{label}: URL is /reset-password with no query/fragment", page.url.rstrip("/").endswith("/reset-password"))


def run(args):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # live: the submit (Pixel 5) consumes every link of the account (#162), so it must come last
        for device in (("Desktop Chrome", "Pixel 5") if args.live else ("Pixel 5", "Desktop Chrome")):
            print(f"\n=== {device}")
            ctx = browser.new_context(**p.devices[device], locale="he-IL")
            page = ctx.new_page()
            shots = Path(args.shots) if args.shots else None
            if args.live:
                base = args.live.rstrip("/")
                tokens = json.loads(Path(args.tokens).read_text())
                for state in ("used", "expired"):
                    check_dead_link(page, base, f"{device} live {state}", tokens[state], INVALID_TITLES[state])
                check_dead_link(page, base, f"{device} live unknown", secrets.token_urlsafe(32), INVALID_TITLES["invalid"])
                check_dead_link(page, base, f"{device} live legacy ?token= used", tokens["used"], INVALID_TITLES["used"], legacy=True)
                check_valid_link(page, base, f"{device} live valid", tokens["valid"])
                if shots:
                    page.screenshot(path=str(shots / f"live_{device.replace(' ', '_')}_valid.png"), full_page=True)
                if args.live_submit and device == "Pixel 5":
                    new_pw = "Probe-" + secrets.token_urlsafe(12)
                    page.fill("#resetpassword-f3", new_pw); page.fill("#resetpassword-f4", new_pw)
                    page.click("button[type=submit]")
                    page.wait_for_selector(f"text={DONE_TITLE}", timeout=15000)
                    check(f"{device} live valid submit -> success", True)
                    open_link(page, base, tokens["valid"])
                    check(f"{device} live: re-opening the used link -> 'used' panel before any form",
                          page.get_by_text(INVALID_TITLES["used"], exact=True).count() == 1
                          and page.locator("input[type=password]").count() == 0)
                    if shots:
                        page.screenshot(path=str(shots / "live_Pixel_5_reopened_used.png"), full_page=True)
            else:
                base = "http://reset.test"
                tok = {s: secrets.token_urlsafe(32) for s in ("valid", "used", "expired", "dies")}
                site = LocalSite(args.dist, {tok["valid"]: "valid", tok["used"]: "used",
                                             tok["expired"]: "expired", tok["dies"]: "valid"})
                page.route("**/*", site.handle)
                for state in ("used", "expired"):
                    check_dead_link(page, base, f"{device} {state}", tok[state], INVALID_TITLES[state])
                    if shots:
                        open_link(page, base, tok[state])
                        page.screenshot(path=str(shots / f"{device.replace(' ', '_')}_{state}.png"), full_page=True)
                check_dead_link(page, base, f"{device} unknown", secrets.token_urlsafe(32), INVALID_TITLES["invalid"])
                check_dead_link(page, base, f"{device} malformed", "abc", INVALID_TITLES["invalid"])
                check_dead_link(page, base, f"{device} legacy ?token= used", tok["used"], INVALID_TITLES["used"], legacy=True)
                check_valid_link(page, base, f"{device} legacy ?token= valid", tok["valid"], legacy=True)
                check_valid_link(page, base, f"{device} valid", tok["valid"])
                if shots:
                    page.screenshot(path=str(shots / f"{device.replace(' ', '_')}_valid.png"), full_page=True)
                # the link dies while the form is open (used on another device) -> invalid panel, not a bare toast
                check_valid_link(page, base, f"{device} dies-while-open", tok["dies"])
                site.confirm_reason[tok["dies"]] = "used"
                pw = "Local-" + secrets.token_urlsafe(9)
                page.fill("#resetpassword-f3", pw); page.fill("#resetpassword-f4", pw); page.click("button[type=submit]")
                page.wait_for_selector("[data-testid=reset-link-invalid]", timeout=10000)
                check(f"{device} dies-while-open: 'used' panel after submit",
                      page.get_by_text(INVALID_TITLES["used"], exact=True).count() == 1)
                check(f"{device} dies-while-open: password not shown anywhere", pw not in page.content())
                # validate endpoint down -> explicit "cannot check" state with retry
                site.validate_down = True
                open_link(page, base, tok["valid"])
                check(f"{device} validate down: 'cannot check' panel",
                      page.get_by_text(INVALID_TITLES["unavailable"], exact=True).count() == 1
                      and page.get_by_text("נסה שוב", exact=True).count() == 1
                      and page.locator("input[type=password]").count() == 0)
                site.validate_down = False
                page.get_by_text("נסה שוב", exact=True).click()
                page.wait_for_selector("#resetpassword-f3", timeout=10000)
                check(f"{device} validate down: retry recovers to the form", page.locator("input[type=password]").count() == 2)
                # backend without /validate (frontend deployed first, or backend rollback) -> the form, as before #163
                site.validate_missing = True
                check_valid_link(page, base, f"{device} validate 404 fallback", tok["valid"])
                site.validate_missing = False
                # valid submit -> success; the token is sent only in the POST body
                pw2 = "Local-" + secrets.token_urlsafe(9)
                page.fill("#resetpassword-f3", pw2); page.fill("#resetpassword-f4", pw2); page.click("button[type=submit]")
                page.wait_for_selector(f"text={DONE_TITLE}", timeout=10000)
                check(f"{device} valid submit -> success panel", True)
                check(f"{device} confirm carried the token in the body",
                      site.confirm_bodies and site.confirm_bodies[-1].get("token") == tok["valid"])
                # the page never puts the token in browser storage
                stored = page.evaluate("() => JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})")
                check(f"{device} no token in local/session storage", all(t not in stored for t in tok.values()))
            ctx.close()
        browser.close()
    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default=str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))
    ap.add_argument("--live")
    ap.add_argument("--tokens")
    ap.add_argument("--live-submit", action="store_true")
    ap.add_argument("--shots")
    sys.exit(run(ap.parse_args()))
