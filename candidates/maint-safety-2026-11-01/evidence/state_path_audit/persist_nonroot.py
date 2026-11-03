import importlib.util, json, sys
sys.path.insert(0, "/app")
def mod(rel, name):
    s = importlib.util.spec_from_file_location(name, "/app/" + rel); m = importlib.util.module_from_spec(s); sys.modules[name] = m; s.loader.exec_module(m); return m
im = mod("inproc_job_marker.py", "im")
print("read  :", im.status("group_scan", now=2100.0)["finished_at"])
try:
    im.mark_started("other"); print("write : ok")
except OSError as e:
    print("write : REFUSED ->", type(e).__name__, e)
