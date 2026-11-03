"""Container A: write state through each REAL module at its DEFAULT path (no overrides), cwd=/ . App tree is mounted READ-ONLY."""
import asyncio, importlib.util, json, os, sys, time
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/tests")
def mod(rel, name):
    s = importlib.util.spec_from_file_location(name, "/app/" + rel); m = importlib.util.module_from_spec(s); sys.modules[name] = m; s.loader.exec_module(m); return m
R = {"uid": os.getuid(), "cwd": os.getcwd()}
im = mod("inproc_job_marker.py", "im"); im.mark_started("group_scan", now=1000.0); im.mark_finished("group_scan", now=2000.0)
th = mod("maintenance/build_part_thumbnails.py", "th"); th._save_failed({"persist.example": 123.0})
ti = mod("thumbnail_idle_state.py", "ti"); ti.save(900, now=5000.0)
ri = mod("runtime_identity.py", "ri"); ri._write({"module": "persist_probe", "pid": 99, "process_start_ticks": 1, "sha256": "ab"})
ms = mod("meili_sync.py", "ms"); ms._save_checkpoint(10, 10)
rw = mod("scripts/restart_workers.py", "rw"); rw._write_state({"version": 2, "workers": [], "marker": "persist"})
se = mod("social/facebook_browser/session.py", "se"); se._save_cookies([{"name": "c_user", "value": "9"}])
class P:
    async def screenshot(self, path, full_page=False): open(path, "wb").write(b"png")
asyncio.run(se._save_failure_screenshot(P(), "persist"))
bl = mod("social/facebook_browser/fb_browser_login.py", "bl"); asyncio.run(bl._screenshot(P(), "persist_login"))
ce = mod("category_embed.py", "ce"); os.makedirs(os.path.join(ce.MODEL_DIR, "onnx"), exist_ok=True)
open(ce.ONNX_PATH, "wb").write(b"x"); open(ce.TOKENIZER_PATH, "w").write("{}")
# real Chromium persistent profile (SP15)
from playwright.sync_api import sync_playwright
se._PROFILE_DIR.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(se._PROFILE_DIR), headless=True, args=["--no-sandbox"])
    ctx.add_cookies([{"name": "k", "value": "v", "domain": "persist.example", "path": "/", "expires": 4102444800}]); ctx.new_page().goto("about:blank"); ctx.close()
# categorize cursor through the real script (sandbox DB)
if os.environ.get("RUN_DB_MUTATION_TESTS") == "1":
    import asyncpg
    from importer_e2e_harness import dsn, ensure_brand, seed_part, run_script, cleanup, release_created_brands
    async def cur():
        conn = await asyncpg.connect(dsn()); mid, _ = await ensure_brand(conn, "BMW"); await seed_part(conn, "PERSIST-CUR", "BMW", mid, oem="PERSISTCUR")
        await conn.execute("UPDATE parts_catalog SET category='כללי', updated_at=NOW()-interval '1 hour' WHERE sku='PERSIST-CUR'")
        pid = str(await conn.fetchval("SELECT id FROM parts_catalog WHERE sku='PERSIST-CUR'"))
        p = run_script("maintenance/categorize_parts_batch.py", "--improve-only", timeout=300)
        await cleanup(conn, "PERSIST-CUR"); await release_created_brands(conn); await conn.close(); return pid, p.returncode
    R["cursor_part"], R["cursor_rc"] = asyncio.run(cur())
# SP02: the HF cache probe path lives on the container layer, not a mount
os.makedirs("/root/.cache/huggingface/hub", exist_ok=True); open("/root/.cache/huggingface/hub/ephemeral_marker", "w").write("x")
print(json.dumps(R))
