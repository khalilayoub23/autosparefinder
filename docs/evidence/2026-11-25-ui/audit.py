"""UI audit harness (mission 3). Headless Chromium vs a vite-preview build of this branch; /api/* is forwarded to the
production API read-only (every non-GET request is answered locally, nothing is mutated). Admin screens use a MOCKED
API (clearly labelled) because no admin session is used. Credentials come from the environment, never written out."""
import json, os, sys, time, urllib.request
from playwright.sync_api import sync_playwright

BASE, PROD = "http://127.0.0.1:4173", "https://autosparefinder.co.il"
OUT = sys.argv[1]
VIEWS = {"desktop": dict(viewport={"width": 1440, "height": 900}),
         "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
                        user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148")}
EMAIL, PW = os.environ.get("UI_TEST_EMAIL", ""), os.environ.get("UI_TEST_PW", "")

JS_METRICS = r"""
() => {
  const lum = c => { const [r,g,b] = c.map(v => { v/=255; return v<=0.03928? v/12.92 : Math.pow((v+0.055)/1.055,2.4)}); return 0.2126*r+0.7152*g+0.0722*b };
  const parse = s => { const m = s.match(/rgba?\(([^)]+)\)/); if(!m) return null; const p = m[1].split(',').map(x=>parseFloat(x)); return {c:p.slice(0,3), a:p.length>3?p[3]:1} };
  const bgOf = el => { let e = el; let acc=[]; while(e){ const p=parse(getComputedStyle(e).backgroundColor); if(p&&p.a>0){acc.push(p); if(p.a>=0.99) break;} e=e.parentElement;} 
     let base=[255,255,255]; if(!acc.length||acc[acc.length-1].a<0.99) base=[15,18,24]; for(let i=acc.length-1;i>=0;i--){ const f=acc[i]; base=base.map((v,k)=>f.c[k]*f.a+v*(1-f.a)); } return base };
  const res = {contrastMin: 99, lowContrast: [], n: 0};
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let node; const seen = new Set();
  while ((node = walker.nextNode())) {
    const t = node.textContent.trim(); if (!t) continue; const el = node.parentElement; if (!el || seen.has(el)) continue; seen.add(el);
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    if (cs.visibility==='hidden'||cs.display==='none'||r.width<2||r.height<2||parseFloat(cs.opacity)<0.05) continue;
    const fg = parse(cs.color); if(!fg) continue; let bg = bgOf(el);
    let g = null; { let e2 = el; while (e2) { const bi = getComputedStyle(e2).backgroundImage; if (bi && bi.includes('gradient')) { g = (bi.match(/rgba?\([^)]+\)/g)||[]).map(parse).filter(x=>x&&x.a>=0.5); break; } e2 = e2.parentElement; } }
    if (g && g.length) { let worst = 99; for (const st of g) { const f2 = fg.c.map((v,k)=> v*fg.a + st.c[k]*(1-fg.a)); const r2 = (Math.max(lum(f2),lum(st.c))+0.05)/(Math.min(lum(f2),lum(st.c))+0.05); worst = Math.min(worst, r2); } res.n++; if (worst < res.contrastMin) res.contrastMin = +worst.toFixed(2); if (worst < 4.5 && res.lowContrast.length < 6) res.lowContrast.push({t: t.slice(0,40), ratio: +worst.toFixed(2)}); continue; }
    const f = fg.c.map((v,k)=> v*fg.a + bg[k]*(1-fg.a));
    const L1 = lum(f), L2 = lum(bg); const ratio = (Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05);
    res.n++; if (ratio < res.contrastMin) res.contrastMin = +ratio.toFixed(2);
    if (ratio < 4.5 && res.lowContrast.length < 6) res.lowContrast.push({t: t.slice(0,40), ratio: +ratio.toFixed(2)});
  }
  const bodyBg = parse(getComputedStyle(document.body).backgroundColor);
  res.bodyBg = getComputedStyle(document.body).backgroundColor;
  res.htmlDir = document.documentElement.dir; res.lang = document.documentElement.lang;
  res.overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
  res.h1 = document.querySelectorAll('h1').length;
  const btns = [...document.querySelectorAll('button, a[role=button], [role=button]')].filter(b=>{const r=b.getBoundingClientRect();return r.width>0&&r.height>0});
  res.buttons = btns.length;
  res.pillButtons = btns.filter(b=>parseFloat(getComputedStyle(b).borderTopLeftRadius)>=999 && b.getBoundingClientRect().width>b.getBoundingClientRect().height*1.5).length;
  res.smallTargets = [...document.querySelectorAll('a, button, input, select, textarea')].filter(b=>{const r=b.getBoundingClientRect(); return r.width>0&&r.height>0&&(r.width<44||r.height<44)}).length;
  res.noLabelButtons = btns.filter(b=>!(b.innerText||'').trim() && !b.getAttribute('aria-label') && !b.getAttribute('title')).length;
  res.imgNoAlt = [...document.images].filter(i=>!i.hasAttribute('alt')).length;
  res.inputsNoLabel = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(i=>!(i.getAttribute('aria-label')||i.getAttribute('placeholder')||i.id&&document.querySelector('label[for="'+i.id+'"]')||i.closest('label'))).length;
  res.clipped = [...document.querySelectorAll('h1,h2,h3,button,a')].filter(e=>e.scrollWidth>e.clientWidth+2 && getComputedStyle(e).overflow!=='visible' && getComputedStyle(e).textOverflow!=='ellipsis').length;
  res.rawSlugs = (document.body.innerText.match(/\b(brakes|engine|filters|body-exterior|suspension|electrical)\b/g)||[]).length;
  res.textLen = document.body.innerText.length;
  res.preview = document.body.innerText.slice(0,160).replace(/\s+/g,' ');
  return res;
}
"""

def forward(route, mock=None):
    req = route.request
    url = req.url
    path = url.split("/api/", 1)[1] if "/api/" in url else ""
    if mock is not None:
        body = mock(path, req.method)
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
    if req.method not in ("GET", "HEAD"):
        return route.fulfill(status=200, content_type="application/json", body="{}")
    try:
        r = route.fetch(url=PROD + "/api/" + path, headers={k: v for k, v in req.headers.items() if k.lower() not in ("host", "origin", "referer")})
        route.fulfill(response=r)
    except Exception as e:  # noqa
        route.fulfill(status=502, body="{}")

CUSTOMER_USER = {"id": "00000000-0000-0000-0000-0000000000c1", "email": "customer@example.test", "full_name": "לקוח בדיקה", "phone": "0500000000",
                 "is_admin": False, "role": "customer", "is_verified": True, "terms_accepted_at": "2026-01-01T00:00:00Z"}
ORDER_FIXTURE = {"id": "00000000-0000-0000-0000-0000000000o1", "order_number": "ASF-TEST-1", "status": "shipped", "total_amount": 294.2, "currency": "ILS",
                 "created_at": "2026-11-20T10:00:00Z", "items": [{"name": "רפידות בלם (פריט בדיקה)", "quantity": 1, "unit_price": 224.75}]}
def customer_mock(path, method):
    if path.startswith(("v1/auth/me", "v1/customers/me", "v1/users/me")): return CUSTOMER_USER
    if path.startswith("v1/orders") and "/" not in path[len("v1/orders"):].strip("/"): return {"orders": [ORDER_FIXTURE, dict(ORDER_FIXTURE, id="00000000-0000-0000-0000-0000000000o2", order_number="ASF-TEST-2", status="pending_payment")]}
    if "cart" in path: return {"items": [], "total": 0}
    return {"items": [], "results": [], "total": 0}

ADMIN_USER = {"terms_accepted_at": "2026-01-01T00:00:00Z", "id": "00000000-0000-0000-0000-000000000001", "email": "admin@example.test", "full_name": "מנהל בדיקות", "is_admin": True,
              "role": "admin", "is_verified": True}
def admin_mock(path, method):
    if path.startswith("v1/auth/me") or path.startswith("v1/customers/me") or path.startswith("v1/users/me"):
        return ADMIN_USER
    if "orders" in path: return {"orders": [], "items": [], "total": 0}
    if "suppliers" in path: return {"suppliers": [], "items": [], "total": 0}
    if "stats" in path or "dashboard" in path or "overview" in path: return {}
    return {"items": [], "results": [], "total": 0}

SCREENS = [  # (name, path, auth)  auth: guest | customer | admin(mock)
    ("home", "/", "guest"), ("search_results", "/parts?search=%D7%A8%D7%A4%D7%99%D7%93%D7%95%D7%AA+%D7%91%D7%9C%D7%9D", "guest"),
    ("parts_landing", "/parts", "guest"), ("cart_guest", "/cart", "guest"), ("login", "/login", "guest"), ("register", "/register", "guest"),
    ("reset_password", "/reset-password", "guest"), ("privacy", "/privacy", "guest"), ("terms", "/terms", "guest"),
    ("refund", "/refund", "guest"), ("developers", "/developers", "guest"), ("not_found", "/does-not-exist", "guest"),
    ("orders", "/orders", "customer"), ("cart", "/cart", "customer"), ("profile", "/profile", "customer"), ("account", "/account", "customer"),
    ("chat", "/chat", "customer"), ("agents_customer", "/agents", "customer"), ("admin_as_customer", "/admin", "customer"),
    ("admin", "/admin", "admin"), ("admin_orders", "/admin/orders", "admin"), ("inventory", "/inventory", "admin"), ("agents_admin", "/agents", "admin"),
]
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else None

def login_tokens():
    if not (EMAIL and PW): return None
    req = urllib.request.Request(PROD + "/api/v1/auth/login", data=json.dumps({"email": EMAIL, "password": PW}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return d if d.get("access_token") else {"error": "2fa/other"}
    except Exception as e:
        return {"error": str(e)[:80]}

def main():
    tokens = None  # production customer login needs 2FA by WhatsApp (not available): customer + admin screens use the mocked API
    print("customer login:", "ok" if tokens and tokens.get("access_token") else {k: v for k, v in (tokens or {}).items() if k in ("error", "requires_2fa", "detail")}, file=sys.stderr)
    results = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        for vname, vopts in VIEWS.items():
            for name, path, auth in SCREENS:
                if ONLY and name not in ONLY: continue
                ctx = b.new_context(locale="he-IL", **vopts)
                if auth in ("admin", "customer"):
                    ctx.add_init_script("localStorage.setItem('access_token','mock'); localStorage.setItem('refresh_token','mock');")
                page = ctx.new_page()
                errs, failed = [], []
                page.on("console", lambda m, e=errs: e.append(m.text[:140]) if m.type == "error" else None)
                page.on("requestfailed", lambda r, f=failed: f.append(r.url[-60:]))
                page.route("**/api/**", (lambda r, q=None: forward(r, admin_mock)) if auth == "admin" else ((lambda r, q=None: forward(r, customer_mock)) if auth == "customer" else (lambda r, q=None: forward(r))))
                t0 = time.time(); status = "PASS"; note = ""
                try:
                    page.goto(BASE + path, wait_until="domcontentloaded", timeout=30000)
                    try: page.wait_for_load_state("networkidle", timeout=12000)
                    except Exception: note = "networkidle not reached in 12s (background polling); DOM content measured"
                    page.wait_for_timeout(1200)
                    m = page.evaluate(JS_METRICS)
                    m["finalPath"] = page.url.replace(BASE, "")
                    shot = f"{OUT}/{name}_{vname}.png"
                    page.screenshot(path=shot, full_page=False)
                except Exception as e:
                    m, status, note = {}, "FAIL", str(e)[:140]
                results.append(dict(screen=name, view=vname, auth=auth + ("(MOCKED API)" if auth in ("admin", "customer") else ""), path=path, secs=round(time.time()-t0, 1),
                                    status=status, note=note, console_errors=errs[:3], failed_requests=len(failed), **m))
                ctx.close()
        b.close()
    json.dump(results, open(f"{OUT}/metrics.json", "w"), ensure_ascii=False, indent=1)
    for r in results:
        print(f"{r['screen']:16}{r['view']:8}{r.get('status', r.get('result')):5} fin={r.get('finalPath','')[:22]:22} bg={r.get('bodyBg','')[:18]:18} cMin={r.get('contrastMin')} low={len(r.get('lowContrast',[]))} ovf={r.get('overflowX')} h1={r.get('h1')} pill={r.get('pillButtons')} small={r.get('smallTargets')} nolbl={r.get('noLabelButtons')} noalt={r.get('imgNoAlt')} inLbl={r.get('inputsNoLabel')} err={len(r.get('console_errors',[]))} txt={r.get('textLen')}")
main()
