"""Container B (a NEW container, same volume): read it all back through the real read functions."""
import asyncio, importlib.util, json, os, sys
sys.path.insert(0, "/app")
def mod(rel, name):
    s = importlib.util.spec_from_file_location(name, "/app/" + rel); m = importlib.util.module_from_spec(s); sys.modules[name] = m; s.loader.exec_module(m); return m
R = {"uid": os.getuid()}
im = mod("inproc_job_marker.py", "im"); R["SP03"] = im.status("group_scan", now=2100.0)["finished_at"] == 2000.0
th = mod("maintenance/build_part_thumbnails.py", "th"); R["SP04"] = th._load_failed() == {"persist.example": 123.0}
ti = mod("thumbnail_idle_state.py", "ti"); R["SP17"] = ti.resume(floor_s=120, now=5100.0) == (800.0, 1800.0)
ri = mod("runtime_identity.py", "ri"); R["SP09"] = json.load(open(os.path.join(ri.state_dir(), "persist_probe.99.1.json")))["sha256"] == "ab"
ms = mod("meili_sync.py", "ms"); rd = mod("meili_checkpoint_state.py", "rd"); R["SP08"] = ms._load_checkpoint()["offset"] == 10 and rd.last_completed_sync() is not None
rw = mod("scripts/restart_workers.py", "rw"); R["SP10"] = json.load(open(rw.STATE_FILE)).get("marker") == "persist"
se = mod("social/facebook_browser/session.py", "se"); R["SP13"] = se._load_cookies()[0]["value"] == "9"
R["SP14"] = any(n.startswith("persist_") for n in os.listdir(se._SCREENSHOT_DIR))
bl = mod("social/facebook_browser/fb_browser_login.py", "bl"); R["SP12"] = os.path.exists(os.path.join(str(bl._SCREENSHOT_DIR), "persist_login.png"))
ce = mod("category_embed.py", "ce"); R["SP01"] = os.path.exists(ce.ONNX_PATH) and os.path.exists(ce.TOKENIZER_PATH)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(str(se._PROFILE_DIR), headless=True, args=["--no-sandbox"]); R["SP15"] = [c["name"] for c in ctx.cookies("http://persist.example/")] == ["k"]; ctx.close()
na = mod("social/noa_ops.py", "na"); R["SP16"] = na.fb_profile_dir() == str(se._PROFILE_DIR) and os.path.exists(os.path.join(na.fb_profile_dir(), "Default", "Cookies"))
cp = mod("maintenance/categorize_parts_batch.py", "cp"); R["SP05"] = bool(json.load(open(cp.CURSOR_PATH)).get("last_id")) if os.path.exists(cp.CURSOR_PATH) else None
R["SP06"] = str(se._STATE_DIR) == str(mod("maintenance/fb_cookie_import.py", "fc")._STATE_DIR) and os.path.exists(os.path.join(str(se._STATE_DIR), "cookies.json"))
R["SP07"] = mod("maintenance/fetch_embed_model.py", "fe").DEST == ce.MODEL_DIR
R["SP11"] = rw.LOG_DIR == os.path.join(str(se._STATE_DIR.parent), "logs") and os.path.isdir(rw.LOG_DIR)
R["SP02_hf_cache_survived_recreation"] = os.path.exists("/root/.cache/huggingface/hub/ephemeral_marker")
print(json.dumps(R))
