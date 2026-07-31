"""Every command in the job-queue plan must be INVOCABLE before the queue runs.

I wrote `--scope backlog` into the plan from memory. It is not a valid choice
(`buckets` / `all`), so argparse would have aborted that step on its first batch
— after the merge step had already spent hours running. A plan is a set of
command lines; command lines are exactly the kind of thing that is wrong when
written from memory rather than checked.

This asserts, WITHOUT doing any work:
  • the script file referenced by each step exists
  • every flag the step passes is accepted by that script's parser
  • the command is not silently a no-op path

It deliberately does NOT execute the real jobs — argparse validation happens
before any DB work, so `--help`-style probing and a flag audit are enough.

Run: docker exec autospare_backend python3 /app/devtests/job_queue_commands_test.py
"""
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, "/app")
import job_queue as jq  # noqa: E402

fails = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))
    if not ok:
        fails.append(label)


print("1. every step's script exists")
scripts = {}
for s in jq.DEFAULT_PLAN:
    parts = shlex.split(s["cmd"].replace("{batch}", "1"))
    path = next((p for p in parts if p.endswith(".py")), None)
    scripts[s["step_key"]] = (path, parts)
    check(f"{s['step_key']} -> {path}", bool(path) and os.path.isfile(path))

print("\n2. every flag passed is accepted by that script's argparse")
for key, (path, parts) in scripts.items():
    if not path or not os.path.isfile(path):
        continue
    src = open(path, encoding="utf-8").read()
    declared = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', src))
    passed = {p for p in parts if p.startswith("--")}
    unknown = passed - declared
    check(f"{key}: flags {sorted(passed) or '(none)'}", not unknown,
          f"UNKNOWN={sorted(unknown)}" if unknown else "")

    # A `choices=` constraint is the trap that bit --scope; verify each value.
    for flag in passed:
        m = re.search(rf'add_argument\(\s*"{re.escape(flag)}"[^)]*choices\s*=\s*'
                      r'\(([^)]*)\)', src)
        if not m:
            continue
        allowed = {v.strip().strip("\"'") for v in m.group(1).split(",") if v.strip()}
        idx = parts.index(flag)
        val = parts[idx + 1] if idx + 1 < len(parts) else None
        check(f"{key}: {flag}={val} is a valid choice", val in allowed,
              f"allowed={sorted(allowed)}")

print("\n3. {batch} is substituted for every step that declares a batch_limit")
for s in jq.DEFAULT_PLAN:
    if s["batch_limit"]:
        check(f"{s['step_key']} uses {{batch}}", "{batch}" in s["cmd"],
              "a batch_limit with no placeholder means the limit is ignored")
    rendered = s["cmd"].replace("{batch}", str(s["batch_limit"]))
    check(f"{s['step_key']} renders cleanly", "{" not in rendered, rendered)

print("\n4. the scripts respond to --help (parser is reachable, no import-time work)")
for key, (path, _) in scripts.items():
    if not path or not os.path.isfile(path):
        continue
    try:
        p = subprocess.run([sys.executable, path, "--help"],
                           capture_output=True, text=True, timeout=90)
        # A script with no argparse exits non-zero on --help; that is acceptable
        # as long as it did not hang or traceback on an import error.
        bad = "ModuleNotFoundError" in (p.stderr or "") or "ImportError" in (p.stderr or "")
        check(f"{key} --help reachable", not bad,
              (p.stderr or "").strip().splitlines()[-1][:90] if bad else "")
    except subprocess.TimeoutExpired:
        check(f"{key} --help reachable", False,
              "TIMED OUT — the script does work at import time")

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL PASS — every queued command is invocable.")
