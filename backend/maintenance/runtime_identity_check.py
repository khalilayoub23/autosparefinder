"""
Script: maintenance/runtime_identity_check.py
Purpose: READ-ONLY check of which source a running process loaded for each watched module (runtime_identity.py).
Process: for the target pid (default 1 = uvicorn in autospare_backend): read its start ticks + boot id, find the
         record written by THAT process instance, hash the file on disk now, and print one status per module:
           LOADED_MATCH          the process compiled exactly the bytes now on disk (and == --expect if given)
           LOADED_DISK_CHANGED   the process runs different bytes than the file on disk now
           LOADED_EXPECT_MISMATCH  the process runs bytes other than --expect
           NOT_LOADED            no record from this process instance (module not imported yet)
         Exit 0 only when every module is LOADED_MATCH. Writes nothing, imports no app code.
Usage: docker exec autospare_backend python3 /app/maintenance/runtime_identity_check.py [--pid 1] [--expect MOD=SHA256]
Last Updated: 2026-10-28
"""
import argparse, glob, hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import runtime_identity as ri  # noqa: E402  (stdlib-only module; installs nothing when imported)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", default="1")
    ap.add_argument("--expect", action="append", default=[], help="module=sha256")
    a = ap.parse_args()
    expect = dict(e.split("=", 1) for e in a.expect)
    ticks, boot = ri.process_start_ticks(a.pid), ri.boot_id()
    ok = True
    for mod in sorted(ri.watched()):
        recs = []
        for p in glob.glob(os.path.join(ri.state_dir(), f"{mod}.{a.pid}.*.json")):
            try:
                recs.append(json.load(open(p)))
            except Exception:
                pass
        mine = [r for r in recs if str(r.get("pid")) == str(a.pid) and r.get("process_start_ticks") == ticks
                and r.get("boot_id") == boot]
        if not mine:
            print(f"{mod}: NOT_LOADED (pid {a.pid} start {ticks}; {len(recs)} record(s) from other process instances)")
            ok = False
            continue
        r = max(mine, key=lambda x: x.get("load_count", 0))
        disk = hashlib.sha256(open(r["path"], "rb").read()).hexdigest()
        if r["sha256"] != disk:
            status, ok = "LOADED_DISK_CHANGED", False
        elif mod in expect and expect[mod] != r["sha256"]:
            status, ok = "LOADED_EXPECT_MISMATCH", False
        else:
            status = "LOADED_MATCH"
        print(f"{mod}: {status} loaded_sha256={r['sha256']} disk_sha256={disk} path={r['path']} pid={r['pid']} "
              f"start={r['process_start_ticks']} host={r['hostname']} loaded_utc={r['loaded_utc']} loads={r['load_count']}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
