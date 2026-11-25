"""Mission-5 REAL-DATA public-screen audit. The build under test (current tree) is served locally; every GET /api/ call is
forwarded READ-ONLY to production (https://autosparefinder.co.il) so the screens render REAL catalog data. Every non-GET API call
is refused here (nothing is written, no order/payment/message). Authenticated screens are NOT covered (they need an owner
session): see the report. Usage: AXE_JS=... python3 audit_real.py STATIC_DIR OUTDIR"""
import json, os, sys, threading, http.server, socketserver, functools, urllib.request
from playwright.sync_api import sync_playwright
STATIC, OUT = sys.argv[1], sys.argv[2]
AXE = open(os.environ["AXE_JS"]).read()
PROD = "https://autosparefinder.co.il"
os.makedirs(OUT, exist_ok=True)

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(s,*a,**k): super().__init__(*a, directory=STATIC, **k)
    def do_GET(s):
        p = s.translate_path(s.path.split("?")[0])
        if not os.path.exists(p) or os.path.isdir(p): s.path = "/index.html"
        return super().do_GET()
    def log_message(s,*a): pass
srv = socketserver.TCPServer(("127.0.0.1", 4180), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:4180"
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

calls = []
def handler(route):
    req = route.request
    if "/api/" not in req.url: return route.continue_()
    path = req.url.split("/api/", 1)[1]
    if req.method != "GET":
        calls.append(("BLOCKED " + req.method, path.split("?")[0])); return route.fulfill(status=403, content_type="application/json", body='{"detail":"blocked by read-only audit"}')
    try:
        r = urllib.request.urlopen(urllib.request.Request(PROD + "/api/" + path, headers={"User-Agent": "Mozilla/5.0 (read-only UI audit)"}), timeout=30)
        body, st = r.read(), r.status; ct = r.headers.get("content-type", "application/json")
    except urllib.error.HTTPError as e:
        body, st, ct = e.read(), e.code, "application/json"
    except Exception as e:
        body, st, ct = b'{"detail":"audit fetch failed"}', 502, "application/json"
    calls.append(("GET", path.split("?")[0], st)); route.fulfill(status=st, content_type=ct, body=body)

SCREENS = [("home", "/", None), ("parts_empty", "/parts", None), ("parts_search", "/parts", "brake pads"),
           ("login", "/login", None), ("register", "/register", None), ("reset_password", "/reset-password", None),
           ("privacy", "/privacy", None), ("terms", "/terms", None), ("developers", "/developers", None),
           ("guard_cart", "/cart", None), ("guard_orders", "/orders", None), ("guard_admin", "/admin", None), ("guard_agents", "/agents", None)]
res = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for vname, vopt in VIEWS.items():
        for name, path, q in SCREENS:
            ctx = b.new_context(**vopt, locale="he-IL"); pg = ctx.new_page(); pg.route("**/*", handler)
            pg.goto(BASE + path, wait_until="networkidle", timeout=60000)
            extra = {}
            if q:
                box = pg.locator("input[type=search], input[type=text], input:not([type])").first
                box.fill(q); box.press("Enter"); pg.wait_for_timeout(4000); pg.wait_for_load_state("networkidle")
                cards = pg.locator("[class*=card], article").count(); extra["resultish_nodes"] = cards
            met = pg.evaluate(METRICS); met["final_path"] = pg.url.replace(BASE, "")
            pg.add_script_tag(content=AXE)
            ax = pg.evaluate("(t) => axe.run(document, {runOnly: {type: 'tag', values: t}}).then(r => r.violations.map(v => ({id: v.id, impact: v.impact, nodes: v.nodes.length})))", TAGS)
            kb = []
            for _ in range(6):
                pg.keyboard.press("Tab"); kb.append(pg.evaluate(KB) if False else None)
            pg.screenshot(path=f"{OUT}/{name}_{vname}.png", full_page=False)
            res.append({"screen": name, "view": vname, "path": path, **extra, "metrics": met, "axe": ax}); ctx.close()
    b.close()
json.dump({"results": res, "api_calls": calls}, open(f"{OUT}/metrics.json", "w"), ensure_ascii=False, indent=1)
for r in res: print(r["screen"], r["view"], "->", r["metrics"]["final_path"], "overflowX" if r["metrics"]["overflowX"] else "ok", "axe:", [(v["id"], v["nodes"]) for v in r["axe"]] or 0, "dir=", r["metrics"]["htmlDir"], "textLen", r["metrics"]["textLen"])
