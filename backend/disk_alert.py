"""
Script:  disk_alert.py
Purpose: Disk-usage threshold evaluation with deduplication, escalation and recovery.
Process:
  1. `evaluate(pct, prev_level, last_sent, now)` maps usage to a level
     (ok / warning 80 / high 85 / critical 90 / emergency 95) and decides whether to notify.
  2. Escalation always notifies; the same level repeats only after its cooldown; dropping
     below 80% sends ONE "recovered"; a lower level after a higher one is a downgrade notice.
  3. `sample(path)` reads total/used/free via shutil.disk_usage (cheap, no scan).
  4. `top_consumers()` is run ONLY when an alert fires: bounded `du` of a fixed short list.
  5. Monitoring errors are reported as level "error", never as "ok".
Data Imported/Modified: none (state is kept by the caller, in Redis).
Data Sources: statvfs of the monitored paths; `du -sx` of known directories on alert only.
Last Updated: 2026-11-03
"""
from __future__ import annotations

import shutil
import subprocess

LEVELS = [(95, "emergency"), (90, "critical"), (85, "high"), (80, "warning")]
RANK = {"ok": 0, "warning": 1, "high": 2, "critical": 3, "emergency": 4}
# Repeat cooldown per level (seconds): worse = more often.
COOLDOWN_S = {"warning": 24 * 3600, "high": 12 * 3600, "critical": 4 * 3600, "emergency": 3600}
SEVERITY = {"warning": "warning", "high": "warning", "critical": "critical", "emergency": "critical"}


def level_for(pct: float) -> str:
    for threshold, name in LEVELS:
        if pct >= threshold:
            return name
    return "ok"


def sample(path: str = "/") -> dict:
    u = shutil.disk_usage(path)
    if u.total <= 0 or u.used < 0 or u.free < 0:  # invalid data is a monitoring error, never "0% used"
        raise OSError(f"invalid disk usage for {path}: total={u.total} used={u.used} free={u.free}")
    return {"path": path, "total": u.total, "used": u.used, "free": u.free,
            "pct": round(u.used * 100 / u.total, 1) if u.total else 0.0}


def evaluate(pct: float, prev_level: str, last_sent_at: float | None, now: float) -> tuple[str, str | None]:
    """Return (current_level, action) where action in None|'alert'|'escalate'|'downgrade'|'recovered'."""
    cur = level_for(pct)
    if cur == "ok":
        return cur, ("recovered" if prev_level != "ok" else None)
    if RANK[cur] > RANK.get(prev_level, 0):
        return cur, "escalate"           # never suppressed by an earlier lower-level alert
    if RANK[cur] < RANK.get(prev_level, 0):
        return cur, "downgrade"
    if last_sent_at is None or now - last_sent_at >= COOLDOWN_S[cur]:
        return cur, "alert"
    return cur, None


def top_consumers(paths=("/var/lib/docker", "/opt/autosparefinder/backups", "/var/log/journal",
                         "/opt/autosparefinder/backend"), timeout: int = 60) -> list[tuple[str, int]]:
    """Bounded, same-filesystem `du` of a fixed short list. Called only when an alert fires."""
    out = []
    for p in paths:
        try:
            r = subprocess.run(["du", "-sxB1", p], capture_output=True, text=True, timeout=timeout)
            if r.returncode == 0 and r.stdout.strip():
                out.append((p, int(r.stdout.split()[0])))
        except (OSError, subprocess.TimeoutExpired, ValueError):
            continue
    return sorted(out, key=lambda x: -x[1])[:5]


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def format_message(s: dict, level: str, action: str, consumers=None) -> tuple[str, str]:
    title = {"recovered": "הדיסק חזר לנורמה"}.get(action, f"שימוש בדיסק {s['pct']}% ({level})")
    body = (f"{s['path']}: {_gb(s['used'])} מתוך {_gb(s['total'])} בשימוש ({s['pct']}%), פנוי {_gb(s['free'])}.")
    if consumers:
        body += "\nהגדולים: " + " · ".join(f"{p} {_gb(n)}" for p, n in consumers)
    return title, body
