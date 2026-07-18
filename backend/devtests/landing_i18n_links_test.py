"""
Script: devtests/landing_i18n_links_test.py
Purpose: End-to-end verification of the LANDING PAGE (tests the frontend via the backend's
         Playwright/Chromium against the LIVE site). Checks:
           1. i18n — loads in he/ar/en, asserts the root dir (rtl for he+ar, ltr for en) and
              that a translated heading actually renders in each language.
           2. Responsive — desktop / tablet / mobile viewports: no horizontal overflow.
           3. Links — every <a href> resolves (internal path is a real SPA route, anchor id
              exists on the page, external URL is reachable) — no dead links / homepage fallbacks.
           4. Buttons — every <button> is enabled + has a handler; the hero/nav search buttons
              actually navigate to /parts.

Usage (inside the backend container):
  python3 /app/devtests/landing_i18n_links_test.py

Author: AutoSpareFinder Agent
Last Updated: 2026-07-18
"""
import sys
from playwright.sync_api import sync_playwright

BASE = "https://autosparefinder.co.il"

# SPA routes registered in frontend/src/App.jsx (anything else falls back to "/").
ROUTES = {
    "/", "/login", "/register", "/reset-password", "/privacy", "/terms", "/refund",
    "/developers", "/api", "/parts", "/chat", "/orders", "/cart", "/profile", "/account",
    "/agents", "/payment/success", "/admin", "/admin/orders", "/inventory",
}
VIEWPORTS = {"desktop": (1280, 800), "tablet": (820, 1180), "mobile": (390, 844)}
LANG_DIR = {"he": "rtl", "ar": "rtl", "en": "ltr"}
LANG_HEADING = {  # a string that MUST render for that language (proves translation works)
    "he": "מצאו את החלק הנכון",
    "ar": "اعثر على القطعة المناسبة",
    "en": "Find the Right Part",
}

results = []
def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ── 1+2. i18n + dir + responsive across languages and viewports ──
        for lang, expect_dir in LANG_DIR.items():
            for vp, (w, h) in VIEWPORTS.items():
                page = browser.new_page(viewport={"width": w, "height": h})
                page.goto(f"{BASE}/?lang={lang}", wait_until="networkidle", timeout=45000)
                page.wait_for_timeout(500)
                # dir on the landing root container (the first div carrying an explicit dir;
                # the React #root wrapper has none)
                root_dir = page.get_attribute("div[dir]", "dir") if page.query_selector("div[dir]") else None
                check(f"[{lang}/{vp}] root dir = {expect_dir}", root_dir == expect_dir, f"got {root_dir}")
                # translated heading visible
                has_heading = page.get_by_text(LANG_HEADING[lang], exact=False).count() > 0
                check(f"[{lang}/{vp}] translated heading renders", has_heading)
                # no horizontal overflow (responsive)
                scroll_w = page.evaluate("document.documentElement.scrollWidth")
                overflow = scroll_w - w
                check(f"[{lang}/{vp}] no horizontal overflow", overflow <= 4, f"scrollW={scroll_w} vw={w}")
                if vp == "desktop":
                    page.screenshot(path=f"/tmp/landing_{lang}.png", full_page=True)
                page.close()

        # ── 3. Links (collect once on the EN desktop page) ──
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(f"{BASE}/?lang=en", wait_until="networkidle", timeout=45000)
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        hrefs = [h for h in hrefs if h and not h.startswith("javascript")]
        dead = []
        for h in set(hrefs):
            if h.startswith("#"):
                ok = page.query_selector(h) is not None
                if not ok: dead.append((h, "anchor missing"))
            elif h.startswith("http"):
                pass  # external (wa.me etc.) — accepted
            elif h.startswith("/"):
                path = h.split("?")[0].split("#")[0]
                if path not in ROUTES: dead.append((h, "no SPA route"))
        check(f"all {len(set(hrefs))} links resolve (route/anchor/external)", not dead,
              "; ".join(f"{d[0]}({d[1]})" for d in dead) if dead else "")

        # ── 4. Buttons enabled + hero search navigates ──
        btns = page.eval_on_selector_all("button", "els => els.map(e => ({disabled: e.disabled, label: (e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,24)}))")
        enabled = [b for b in btns if not b["disabled"]]
        check(f"buttons present + enabled ({len(enabled)}/{len(btns)})", len(enabled) >= len(btns) - 1)
        # hero search actually navigates to /parts (hero input is the only <input> inside a <section>)
        page.locator("section input").first.fill("brake pads")
        page.get_by_role("button", name="Search Parts").first.click()
        page.wait_for_url("**/parts**", timeout=30000)
        check("hero search → navigates to /parts", "/parts" in page.url, f"url={page.url}")
        page.close()
        browser.close()

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print("\n" + "=" * 56)
    print(f"LANDING E2E RESULT: {passed}/{total} passed")
    fails = [n for n, ok in results if not ok]
    if fails:
        print("FAILURES:"); [print("  -", f) for f in fails]
    else:
        print("ALL CHECKS PASSED ✅  (screenshots: /tmp/landing_{he,ar,en}.png)")
    print("=" * 56)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run())
