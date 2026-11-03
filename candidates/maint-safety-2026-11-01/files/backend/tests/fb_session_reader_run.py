"""Helper (NOT a test): a SECOND container reading the persisted Facebook session through the real consumers. Prints names/count only."""
import importlib.util, json, os, sys
APP = sys.argv[1]; sys.path.insert(0, APP)
def mod(rel, name):
    s = importlib.util.spec_from_file_location(name, os.path.join(APP, rel)); m = importlib.util.module_from_spec(s); sys.modules[name] = m; s.loader.exec_module(m); return m
se = mod("social/facebook_browser/session.py", "se"); bl = mod("social/facebook_browser/fb_browser_login.py", "bl"); ci = mod("maintenance/fb_cookie_import.py", "ci")
a = se._load_cookies()
b = json.loads((ci._STATE_DIR / "cookies.json").read_text(encoding="utf-8"))   # fb_cookie_import --check reads this same file
print(json.dumps({"uid": os.getuid(), "session_file": str(se._COOKIES_FILE), "login_file": str(bl._COOKIES_FILE), "import_dir": str(ci._STATE_DIR),
                  "same_dir": str(se._STATE_DIR) == str(bl._STATE_DIR) == str(ci._STATE_DIR),
                  "session_names": sorted(c["name"] for c in a), "import_tool_file_names": sorted(c["name"] for c in b),
                  "identical": a == b, "authenticated_pair": {"c_user", "xs"} <= {c["name"] for c in a}}))
