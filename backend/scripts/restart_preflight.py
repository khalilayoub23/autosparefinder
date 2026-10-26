"""
Script: scripts/restart_preflight.py
Purpose: Answer ONE question before a backend restart / recreation: is any work running that
         the restart would cut? Run inside the backend container (pre_restart.sh calls it first
         and aborts before its own side effects when the answer is "yes").
Why: pre_restart.sh looked only at job_registry rows and importer subprocesses. On 2026-10-10 a
     recreation at 07:06 UTC cut the daily Facebook group scan, which runs INSIDE uvicorn
     (started 07:00 UTC, ~3 h, no catch-up) - that day's owner-approval drafts were lost.
Process:
  gather()    facts, each source independent and fail-soft (an unreadable source is reported):
              in-process markers (inproc_job_marker: group_scan), any browser main process
              other than the always-on Amayama browser, the uvicorn start time, managed subprocesses (/proc argv), job_registry rows 'running', Redis
              job locks + the night-pipeline running marker, Email Agent writes in flight.
  evaluate()  pure decision -> {"safe": bool, "blockers": [...], "notes": [...]}.
              Blockers: group scan running (marker), any other browser job, inside the scan
              window with no finish marker for today UNLESS this process started after today's
              slot (then it never began today's scan), night-pipeline job running, job_registry running rows, job
              locks, heavy subprocesses, a Gmail write in flight, or a source that could not
              be read (fail closed). Notes: inside the night window, harvester state.
  Exit code:  0 safe, 4 blocked. `--json` prints the full result.
  Override:   PRE_RESTART_FORCE=1 on pre_restart.sh (an explicit owner decision).
Data Imported/Modified: none (read-only).
Data Sources: /proc, /app/state, Redis, catalog DB (SELECT).
Missing Data Delegation: n/a.
Last Updated: 2026-10-10
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HEAVY_SUBPROCESS_MARKERS = ("meili_sync.py", "meili_purge_inactive.py", "build_part_thumbnails.py",
                            "/app/importers/", "/app/maintenance/", "/app/scrapers/")
# The Amayama server browser is the ONLY Chrome that is always on. Any other browser main
# process is a job in progress (group scan batch, OEM scraper, ...) - identified by exclusion,
# because a job's profile path is configuration and has changed before.
ALWAYS_ON_BROWSER_MARKER = "amayama_chrome_profile"
GROUP_SCAN_MAX_H = 4.0          # measured ~3.1 h (FIXES_TRACKER #52-#54)


def _hm(env: str, default: str) -> time:
    h, m = (os.getenv(env, default) or default).split(":")
    return time(int(h), int(m))


def _local(now_utc: datetime):
    from local_schedule import LOCAL_TZ
    return now_utc.astimezone(LOCAL_TZ)


def group_scan_window(now_utc: datetime) -> Dict[str, Any]:
    """Is `now` inside today's scan window [start, start + GROUP_SCAN_MAX_H) in Israel time?"""
    loc = _local(now_utc)
    start = loc.replace(hour=int(os.getenv("SOCIAL_GROUP_SCAN_HOUR_IL", "10")),
                        minute=int(os.getenv("SOCIAL_GROUP_SCAN_MINUTE_IL", "0")), second=0, microsecond=0)
    end = start + timedelta(hours=GROUP_SCAN_MAX_H)
    return {"inside": start <= loc < end, "start_utc": start.astimezone(timezone.utc),
            "end_local": end.strftime("%H:%M")}


def in_night_window(now_utc: datetime) -> bool:
    t = _local(now_utc).time()
    return _hm("NIGHT_PIPELINE_OPEN_IL", "01:00") <= t < _hm("NIGHT_PIPELINE_CLOSE_IL", "07:00")


def evaluate(facts: Dict[str, Any], now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    now_utc = now_utc or datetime.now(timezone.utc)
    blockers: List[str] = []
    notes: List[str] = []

    for src, err in sorted((facts.get("errors") or {}).items()):
        blockers.append(f"could not read {src} ({err}) - state unknown")

    enabled = os.getenv("SOCIAL_GROUP_SCAN_ENABLED", "1").strip() == "1"
    marker = facts.get("group_scan_marker") or {}
    win = group_scan_window(now_utc)
    for b in facts.get("other_browsers") or []:
        blockers.append(describe_browser(b))
    started = facts.get("process_started_at")
    if marker.get("running"):
        blockers.append(f"Facebook group scan in progress (started {int(marker.get('age_s') or 0) // 60} min ago)")
    elif enabled and win["inside"]:
        finished = marker.get("finished_at")
        done_today = bool(finished) and datetime.fromtimestamp(float(finished), timezone.utc) >= win["start_utc"]
        if done_today:
            pass
        elif isinstance(started, (int, float)) and started > win["start_utc"].timestamp():
            # The loop sleeps until the NEXT 10:00 strictly after it starts, so a process that
            # started after today's slot never began today's scan: there is nothing to cut.
            notes.append("backend process started after today's group-scan slot - this process has no scan "
                         "to interrupt today (next scan: next scheduled slot)")
        else:
            blockers.append(f"inside the Facebook group scan window (until {win['end_local']} IL) and no "
                            "finish marker for today - a restart now loses today's scan (no catch-up)")

    if facts.get("night_job"):
        blockers.append(f"night pipeline is running {facts['night_job']}")
    elif in_night_window(now_utc):
        notes.append("inside the night-pipeline window: the next queued job starts within a minute")

    for job in facts.get("running_jobs") or []:
        blockers.append(f"job_registry: {job} is running")
    for lock in facts.get("locks") or []:
        blockers.append(f"Redis job lock held: {lock}")
    for proc in facts.get("subprocesses") or []:
        blockers.append(f"subprocess running: {proc}")
    if facts.get("email_writes_in_flight"):
        blockers.append(f"Email Agent: {len(facts['email_writes_in_flight'])} Gmail write(s) in flight")

    if facts.get("harvester"):
        notes.append("car-parts.ie harvester is running (continuous by design; relaunched after the restart)")
    return {"safe": not blockers, "blockers": blockers, "notes": notes,
            "checked_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")}


def describe_browser(profile: str) -> str:
    """Name what a non-Amayama browser is. Every kind is a blocker; the text tells the operator
    whether to wait hours (the group scan) or about a minute (a per-part scraper fetch)."""
    fb = (os.getenv("FB_PROFILE_DIR", "") or "fb_native_test/profile").rstrip("/")
    if profile and (profile in fb or fb.endswith(profile) or "fb_" in profile):
        return f"Facebook group scan: its browser is running (profile {profile}) - do not restart until it finishes"
    if "playwright_chromiumdev_profile" in profile or profile == "no profile":
        return (f"a short-lived scraper browser is running ({profile}) - a per-part fetch of a few seconds; "
                "re-run the preflight in about a minute")
    return f"a browser job is running (not the always-on Amayama browser): {profile}"


def other_browsers(cmdlines: List[str]) -> List[str]:
    """Browser MAIN processes (no --type= helper, no crashpad) that are not the Amayama browser."""
    out = []
    for c in cmdlines:
        first = c.split(" ", 1)[0]
        if not first.rsplit("/", 1)[-1].startswith(("chrome", "chromium", "headless_shell")):
            continue
        if "--type=" in c or "crashpad" in first or ALWAYS_ON_BROWSER_MARKER in c:
            continue
        prof = next((t.split("=", 1)[1] for t in c.split() if t.startswith("--user-data-dir=")), "no profile")
        out.append(prof.rsplit("/", 2)[-2] + "/" + prof.rsplit("/", 1)[-1] if "/" in prof else prof)
    return sorted(out)


def process_started_at(pid: int = 1, proc_root: str = "/proc") -> Optional[float]:
    """Epoch start time of a process (uvicorn is PID 1 in the backend container). None if unknown."""
    try:
        stat = open(os.path.join(proc_root, str(pid), "stat")).read()
        ticks = float(stat.rsplit(")", 1)[1].split()[19])          # field 22: starttime, in clock ticks
        btime = next(float(l.split()[1]) for l in open(os.path.join(proc_root, "stat")) if l.startswith("btime"))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return None


def heavy_subprocesses(cmdlines: List[str]) -> List[str]:
    """Script name of every heavy managed subprocess, one entry per script."""
    found = set()
    for c in cmdlines:
        if "restart_preflight" in c:
            continue
        for token in c.split():
            if token.endswith(".py") and any(m in token for m in HEAVY_SUBPROCESS_MARKERS):
                found.add(token.rsplit("/", 1)[-1])
    return sorted(found)


def _proc_cmdlines(proc_root: str = "/proc") -> List[str]:
    out = []
    me = str(os.getpid())
    for pid in os.listdir(proc_root):
        if not pid.isdigit() or pid == me:
            continue
        try:
            raw = open(os.path.join(proc_root, pid, "cmdline"), "rb").read()
        except OSError:
            continue
        if raw:
            out.append(raw.replace(b"\0", b" ").decode("utf-8", "replace").strip())
    return out


def gather() -> Dict[str, Any]:
    facts: Dict[str, Any] = {"errors": {}}
    try:
        cmds = _proc_cmdlines()
        facts["other_browsers"] = other_browsers(cmds)
        facts["process_started_at"] = process_started_at(1)
        facts["subprocesses"] = heavy_subprocesses(cmds)
        facts["harvester"] = any("car_parts_ie_flaresolverr_harvester" in c for c in cmds)
    except Exception as e:
        facts["errors"]["/proc"] = type(e).__name__
    try:
        import inproc_job_marker
        facts["group_scan_marker"] = inproc_job_marker.status("group_scan")
    except Exception as e:
        facts["errors"]["group scan marker"] = type(e).__name__
    try:
        import redis
        r = redis.from_url(os.environ["REDIS_URL"], decode_responses=True, socket_timeout=5)
        facts["locks"] = sorted(r.scan_iter("autospare:lock:*"))
        facts["night_job"] = r.get("autospare:night_pipeline:running")
    except Exception as e:
        facts["errors"]["Redis"] = type(e).__name__
    try:
        import asyncio
        import asyncpg

        async def _jobs():
            c = await asyncpg.connect(os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://"),
                                      timeout=10)
            try:
                return [r["job_id"] for r in await c.fetch(
                    "SELECT job_id FROM job_registry WHERE status = 'running' ORDER BY started_at")]
            finally:
                await c.close()
        facts["running_jobs"] = asyncio.run(_jobs())
    except Exception as e:
        facts["errors"]["job_registry"] = type(e).__name__
    try:
        from email_agent import control
        facts["email_writes_in_flight"] = [w for w in control.writes_in_flight() if not w.get("stale")]
    except Exception as e:
        facts["errors"]["Email Agent control"] = type(e).__name__
    return facts


def main(argv: List[str]) -> int:
    result = evaluate(gather())
    if "--json" in argv:
        print(json.dumps(result, indent=2))
    else:
        print("RESTART PREFLIGHT:", "SAFE" if result["safe"] else "BLOCKED")
        for b in result["blockers"]:
            print("  BLOCKER:", b)
        for n in result["notes"]:
            print("  note:", n)
    return 0 if result["safe"] else 4


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
