"""Mission-4 UI audit: populated cart, checkout (address + payment steps), order list + expanded order detail, admin tables,
plus an axe-core WCAG 2.2 AA scan of every route. Headless Chromium at 1440x900 and 390x844 (touch) against a `vite preview`
of the build under test. EVERY /api/ call is answered by fixtures.py (MOCKED, contract-faithful, synthetic): nothing reaches
production, nothing is written, no order/payment/message is created. Usage:
  AXE_JS=/path/to/axe.min.js python3 audit.py OUTDIR [screen,screen,...]"""
import json, os, sys, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fixtures as fx

BASE = "http://127.0.0.1:4173"
OUT = sys.argv[1]
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else None
AXE = open(os.environ["AXE_JS"]).read()
VIEWS = {"desktop": dict(viewport={"width": 1440, "height": 900}),
         "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148")}
TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]

METRICS = r"""() => {
  const vis = e => { const r = e.getBoundingClientRect(); const cs = getComputedStyle(e); return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const out = {};
  out.htmlDir = document.documentElement.dir; out.lang = document.documentElement.lang;
  out.overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
  out.h1 = document.querySelectorAll('h1').length;
  const ctl = [...document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, [role=button]')].filter(vis);
  out.controls = ctl.length;
  out.targetsBelow24 = ctl.filter(b => { const r = b.getBoundingClientRect(); return r.width < 24 || r.height < 24; }).length;   // WCAG 2.2 SC 2.5.8 (AA)
  out.targetsBelow44 = ctl.filter(b => { const r = b.getBoundingClientRect(); return r.width < 44 || r.height < 44; }).length;   // AAA / platform guideline, informational
  // long identifiers: any element whose text is a long unbroken token must not overflow its container
  out.overflowingText = [...document.querySelectorAll('body *')].filter(e => vis(e) && e.children.length === 0 && e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow === 'visible' && e.getBoundingClientRect().right > window.innerWidth + 1).map(e => e.textContent.trim().slice(0, 40)).slice(0, 5);
  // form-control text contrast (axe skips the value text of <select>): computed colour vs first opaque ancestor background
  const lum = c => { const f = c.map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]; };
  const rgb = s => (s.match(/[\d.]+/g) || []).map(Number);
  const bgOf = e => { while (e) { const b = rgb(getComputedStyle(e).backgroundColor); if (b.length >= 3 && (b.length < 4 || b[3] >= 0.99)) return b; e = e.parentElement; } return [15, 18, 24]; };
  const lowc = [...document.querySelectorAll('select, input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=range]), textarea')].filter(vis).filter(e => { const f = rgb(getComputedStyle(e).color), b = bgOf(e); const r = (Math.max(lum(f), lum(b)) + 0.05) / (Math.min(lum(f), lum(b)) + 0.05); return r < 4.5; }).map(e => e.outerHTML.slice(0, 120));
  out.controlContrastBelow45 = lowc.length; out.controlContrastSamples = lowc.slice(0, 3);
  out.textLen = document.body.innerText.length;
  out.preview = document.body.innerText.slice(0, 140).replace(/\s+/g, ' ');
  return out;
}"""


def route_handler(requests_log):
    def h(route):
        req = route.request
        url = req.url
        if "/api/" not in url:
            return route.continue_()
        path = url.split("/api/", 1)[1]
        requests_log.append((req.method, path.split("?")[0].replace("v1/", "", 1), req.post_data or ""))
        status, body = fx.api_mock(path, req.method, req.post_data or "")
        route.fulfill(status=status, content_type="application/json", body=json.dumps(body))
    return h


KB = r"""() => { const e = document.activeElement; if (!e || e === document.body) return null; const cs = getComputedStyle(e);
  const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) > 0) || (cs.boxShadow && cs.boxShadow !== 'none');
  const r = e.getBoundingClientRect(); return {tag: e.tagName, ring: !!ring, name: (e.getAttribute('aria-label') || e.innerText || e.placeholder || '').trim().slice(0, 30), vis: r.width > 0}; }"""


def keyboard_probe(page, n=14):
    seen, noring = [], []
    for _ in range(n):
        page.keyboard.press("Tab")
        f = page.evaluate(KB)
        if f and f["vis"]:
            seen.append(f)
            if not f["ring"]:
                noring.append(f"{f['tag']}:{f['name']}")
    return {"tab_stops": len(seen), "tab_stops_without_visible_focus": len(noring), "no_focus_samples": noring[:4]}


def axe_scan(page):
    page.add_script_tag(content=AXE)
    res = page.evaluate("async (tags) => { const r = await axe.run(document, {runOnly: {type: 'tag', values: tags}, resultTypes: ['violations']}); "
                        "return r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, "
                        "samples: v.nodes.slice(0, 3).map(n => n.target.join(' ') + ' :: ' + (n.html || '').slice(0, 110))})); }", TAGS)
    return res


# (name, path, auth, steps)  steps: list of callables(page, log) -> dict of interaction results
def click_text(page, text, nth=0):
    page.get_by_text(text, exact=False).nth(nth).click()


def cart_to_address(p, log):
    p.get_by_role("button", name="המשך").first.click()
    p.wait_for_timeout(300)
    return {}


def cart_address_validation(p, log):
    cart_to_address(p, log)
    p.get_by_role("button", name="שנה כתובת").first.click()
    p.wait_for_timeout(200)
    street = p.locator("input.input-field").first
    res = {"continue_disabled_when_street_empty": p.get_by_role("button", name="המשך לתשלום").is_disabled()}
    street.fill("הרצל 55")
    p.locator("input.input-field").nth(1).fill("עכו")
    p.wait_for_timeout(150)
    res["continue_enabled_when_filled"] = not p.get_by_role("button", name="המשך לתשלום").is_disabled()
    return res


def cart_payment(p, log):
    cart_to_address(p, log)
    p.get_by_role("button", name="המשך לתשלום").click()
    p.wait_for_timeout(300)
    return {"pay_button": p.get_by_role("button", name="שלם").first.inner_text()}


def cart_pay_click(p, log):
    cart_payment(p, log)
    p.route("**/__mock_checkout__", lambda r: r.fulfill(status=200, content_type="text/html", body="<!doctype html><html lang=he><head><title>mock checkout</title></head><body><main><h1>mock checkout (no payment)</h1></main></body></html>"))
    p.get_by_role("button", name="שלם").first.click()
    p.wait_for_timeout(900)
    calls = [(m, u) for m, u, _ in log if u in ("customers/checkout", "payments/create-checkout")]
    body = next((b for m, u, b in log if u == "customers/checkout"), "")
    return {"api_calls": calls, "checkout_payload_keys": sorted(json.loads(body).keys()) if body else None}


def cart_qty_remove(p, log):
    before = p.locator("text=₪").count()
    p.get_by_role("button", name="הסר").first.click() if p.get_by_role("button", name="הסר").count() else None
    p.wait_for_timeout(300)
    return {"remove_buttons_have_accessible_name": p.get_by_role("button", name="הסר").count() > 0, "price_nodes_before": before}


def order_expand(p, log):
    p.locator("button[aria-expanded]:has(bdi)").first.click()
    p.wait_for_timeout(500)
    return {"detail_requested": any(u.startswith("orders/5") for _, u, _ in log), "items_visible": "מסנן שמן" in p.inner_text("body")}


def admin_tab(label):
    def f(p, log):
        p.get_by_role("button", name=label).first.click()
        p.wait_for_timeout(700)
        return {"tab": label}
    return f


SCREENS = [
    ("cart_populated", "/cart", "customer", []),
    ("checkout_address", "/cart", "customer", [cart_to_address]),
    ("checkout_address_validation", "/cart", "customer", [cart_address_validation]),
    ("checkout_payment", "/cart", "customer", [cart_payment]),
    ("checkout_pay_click", "/cart", "customer", [cart_pay_click]),
    ("cart_remove", "/cart", "customer", [cart_qty_remove]),
    ("orders_list", "/orders", "customer", []),
    ("order_detail", "/orders", "customer", [order_expand]),
    ("admin_orders", "/admin", "admin", [admin_tab("הזמנות")]),
    ("admin_users", "/admin", "admin", [admin_tab("משתמשים")]),
    ("admin_suppliers", "/admin", "admin", [admin_tab("ספקים")]),
    ("admin_dashboard", "/admin", "admin", []),
] + [(f"admin_tab_{i}", "/admin", "admin", [admin_tab(l)]) for i, l in enumerate(
    ["צ׳אטים", "נתוני DB", "ייבוא חלקים", "רשתות חברתיות", "החזרות", "ייבוא PDF", "משימות"])]
# route sweep (axe only) over the whole application
SWEEP = [("home", "/", "guest"), ("parts", "/parts", "guest"), ("login", "/login", "guest"), ("register", "/register", "guest"),
         ("reset_password", "/reset-password", "guest"), ("privacy", "/privacy", "guest"), ("terms", "/terms", "guest"), ("refund", "/refund", "guest"),
         ("developers", "/developers", "guest"), ("not_found", "/nope", "guest"), ("chat", "/chat", "customer"), ("profile", "/profile", "customer"),
         ("account", "/account", "customer"), ("payment_success", "/payment/success", "customer"), ("inventory", "/inventory", "admin"),
         ("agents_admin", "/agents", "admin"),
         ("guard_guest_cart", "/cart", "guest"), ("guard_guest_orders", "/orders", "guest"), ("guard_customer_admin", "/admin", "customer"),
         ("guard_customer_agents", "/agents", "customer")]


def run_one(b, view, vopts, name, path, auth, steps):
    ctx = b.new_context(locale="he-IL", **vopts)
    if auth != "guest":
        ctx.add_init_script("localStorage.setItem('access_token','mock');localStorage.setItem('refresh_token','mock');")
    fx.MOCK_STATE["user"] = fx.ADMIN_USER if auth == "admin" else fx.CUSTOMER_USER
    page = ctx.new_page()
    log, errs = [], []
    page.on("console", lambda m: errs.append(m.text[:140]) if m.type == "error" else None)
    page.route("**/*", route_handler(log))
    rec = dict(screen=name, view=view, path=path, auth=auth + " (MOCKED API)" if auth != "guest" else "guest (real UI, mocked API)", status="PASS", note="", interactions={})
    try:
        page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(900)
        for s in steps:
            rec["interactions"].update(s(page, log) or {})
        rec["finalPath"] = page.url.replace(BASE, "")
        rec.update(page.evaluate(METRICS))
        rec["keyboard"] = keyboard_probe(page)
        rec["axe"] = axe_scan(page)
        page.screenshot(path=f"{OUT}/{name}_{view}.png", full_page=False)
        if name in ("cart_populated", "orders_list", "admin_orders", "order_detail", "checkout_address", "checkout_payment"):
            page.screenshot(path=f"{OUT}/{name}_{view}_full.png", full_page=True)
    except Exception as e:
        rec["status"], rec["note"] = "FAIL", str(e)[:200]
    rec["console_errors"] = errs[:3]
    ctx.close()
    return rec


def main():
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        for view, vopts in VIEWS.items():
            for name, path, auth, steps in SCREENS:
                if ONLY and name not in ONLY:
                    continue
                results.append(run_one(b, view, vopts, name, path, auth, steps))
            for name, path, auth in SWEEP:
                if ONLY and name not in ONLY:
                    continue
                results.append(run_one(b, view, vopts, name, path, auth, []))
        b.close()
    json.dump(results, open(f"{OUT}/metrics.json", "w"), ensure_ascii=False, indent=1)
    tot, kbs = {}, 0
    for r in results:
        kbs += r.get("keyboard", {}).get("tab_stops_without_visible_focus", 0)
        for v in r.get("axe", []):
            tot[v["id"]] = tot.get(v["id"], 0) + v["nodes"]
        print(f"{r['screen']:28}{r['view']:8}{r['status']:5} ovf={r.get('overflowX')} <24={r.get('targetsBelow24')} <44={r.get('targetsBelow44')} "
              f"ctl={r.get('controlContrastBelow45')} axe={sum(v['nodes'] for v in r.get('axe', []))} fin={r.get('finalPath', '')[:18]} {r.get('note', '')}")
    print("TAB STOPS WITHOUT VISIBLE FOCUS (all runs):", kbs)
    print("AXE TOTAL BY RULE:", json.dumps(tot, ensure_ascii=False))


main()
