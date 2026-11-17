"""
Script:  relay_run.py
Purpose: Run ONE importer for ONE relay run: stdout/stderr to a per-run log (never overwritten), then write the exit code
         to the run's result file so the harvester (polling /api/v1/system/collect/import-status) sees success or failure.
Usage:   relay_run.py <result.json> <log> -- <command...>
Data Imported/Modified: result file + log only.
Last Updated: 2026-11-17
"""
import json
import subprocess
import sys
import time


def main() -> int:
    res, log = sys.argv[1], sys.argv[2]
    cmd = sys.argv[sys.argv.index("--") + 1:]
    started = time.time()
    with open(log, "w") as lf:
        rc = subprocess.call(cmd, stdout=lf, stderr=subprocess.STDOUT)
    tail = ""
    try:
        tail = open(log).read()[-1500:]
    except Exception:
        pass
    with open(res + ".tmp", "w") as f:
        json.dump({"state": "ok" if rc == 0 else "failed", "exit_code": rc, "started_at": started,
                   "finished_at": time.time(), "log": log, "tail": tail}, f)
    import os
    os.replace(res + ".tmp", res)
    return rc


if __name__ == "__main__":
    sys.exit(main())
