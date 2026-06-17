#!/bin/bash
# Run AFTER manual docker restart / rebuild
# Reads /tmp/restart_state.json (saved by pre_restart.sh) and resumes all workers
# Note: container_start.sh handles automatic resume on OOM/auto-restart

STATE_FILE="/tmp/restart_state.json"
if [ ! -f "$STATE_FILE" ]; then
    echo "No restart state file found at $STATE_FILE — nothing to resume"
    exit 0
fi

echo "=== POST-RESTART RESUME ===" >&2

python3 - << 'PYEOF'
import json, subprocess, time, sys, os, re, shlex
from pathlib import Path

state = json.load(open('/tmp/restart_state.json'))
workers = state.get('workers', [])
jobs = state.get('jobs') or []

if not workers:
    print("No subprocesses to resume.")
else:
    print(f"Found {len(workers)} worker(s) to resume:")
    for w in workers:
        name = w.get('name', 'unknown')
        cmd = w.get('cmd', '')
        full_cmd = w.get('full_cmd', '')
        if not cmd:
            continue

        print(f"  -> {name}: {cmd}")
        log = f"/app/state/logs/resumed_{name}_{int(time.time())}.log"

        # Special handling: add checkpoint resume flags where applicable
        resume_args = cmd
        if 'oempartsonline_importer' in cmd or 'car_parts_ie_import' in cmd:
            args = shlex.split(full_cmd)
            brand_match = None
            if '--brand' in args:
                idx = args.index('--brand') + 1
                brand_match = args[idx] if idx < len(args) else None
            elif '--file' in args:
                idx = args.index('--file') + 1
                if idx < len(args):
                    m = re.search(r'/tmp/([a-z]+)_(?:oem|cpie)\.json', args[idx])
                    if m:
                        brand_match = m.group(1)
            if brand_match:
                oem_cp = Path(f'/tmp/{brand_match}_oem_checkpoint.json')
                if oem_cp.exists():
                    try:
                        cp = json.loads(oem_cp.read_text())
                        nb = cp.get('next_batch', 0)
                        if nb > 0:
                            resume_args = f"{cmd} --resume-from {nb}"
                            print(f"     (OEM checkpoint: batch {nb})")
                    except:
                        pass

        full_docker_cmd = ['docker', 'exec', '-d', 'autospare_backend',
                           'bash', '-c', f'PYTHONUNBUFFERED=1 python3 {resume_args} >> {log} 2>&1']
        try:
            subprocess.Popen(full_docker_cmd)
            print(f"     Started in background (log: {log})")
            time.sleep(2)  # stagger starts
        except Exception as e:
            print(f"     ERROR: {e}")

if jobs:
    print(f"\nJob registry had {len(jobs)} running job(s) before restart:")
    for j in jobs:
        print(f"  {j['job_id']} ({j.get('age_mins', 0):.0f}min old) -> auto-resumed by container_start.sh")

print("\n✅ Post-restart resume complete.")
PYEOF

echo "=== POST-RESTART RESUME DONE ===" >&2
