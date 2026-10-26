"""
Script: harvester_health.py
Purpose: Decide whether the car-parts.ie harvester is healthy, idle, or genuinely stalled (FIXES_TRACKER #78).
Why: the healthcheck judged ONLY the log file's age (> 900 s = stalled). A drained queue makes the harvester rest
     up to HARVESTER_IDLE_MAX_REST_S (3600 s) without logging — correct behaviour — so every long idle rest was
     classified STALLED and killed (production log: `log_age_s=1823 status=STALLED` → `exited rc=-15 after 3575s`),
     about hourly, each relaunch minting a fresh FlareSolverr cookie. Quietness is not a stall.
Process:
  The harvester writes /app/state/harvester_status.json (harvesters/car_parts_ie_flaresolverr_harvester.py):
    pid, state (idle | waiting_clearance | working), updated_at (main-loop heartbeat),
    rest_until (idle / waiting_clearance only), last_progress_at (any worker finished a page request — success or
    empty — or a model), workers {name: {vehicle, started_at, last_progress_at}}.
  evaluate() — pure, no I/O — returns {"verdict": ok | idle | stalled | missing, "reason", ...ages}:
    * no process                                  → missing (the supervisor relaunches it)
    * no status / status of ANOTHER pid / unknown → legacy rule: stalled iff log age > STALE_LOG_S
      (a stale file from a dead process can never vouch for a new one)
    * idle / waiting_clearance: healthy while now <= rest_until + REST_GRACE_S AND the main-loop heartbeat is
      younger than HEARTBEAT_MAX_AGE_S; past rest_until + grace → stalled "rest_overrun" (it never woke up);
      heartbeat stale inside the rest → stalled "idle_heartbeat_stale"
    * working: stalled iff no page/model has completed for PROGRESS_STALL_S (a single page request is bounded by
      the 40 s HTTP timeout, so 15 min with no completion across ALL workers is a hang, not slowness). The
      heartbeat alone NEVER keeps a working harvester "healthy" — only progress does.
  One hung worker while others still complete pages is NOT stalled (useful work continues); once the others finish
  their slice the cycle cannot complete, progress stops and the rule fires (bound: slice time + PROGRESS_STALL_S).
Data Imported/Modified: none (pure function).
Last Updated: 2026-10-21
"""
from __future__ import annotations

from typing import Any, Dict, Optional

STALE_LOG_S = 900            # legacy rule (no usable status file)
PROGRESS_STALL_S = 900       # working with no completed request for 15 min = hung
HEARTBEAT_MAX_AGE_S = 300    # the main loop beats every <= 60 s while resting
REST_GRACE_S = 300           # a rest may overrun its planned end by this much (clock/scheduling slack)
IDLE_STATES = ("idle", "waiting_clearance")
CLOCK_SKEW_S = 300           # a timestamp further in the FUTURE than this is not trusted (clock stepped back)


def _age(now: float, ts: Any) -> Optional[float]:
    """Seconds since ts; None when ts is missing, not a number, or more than CLOCK_SKEW_S in the future — so a
    backward clock step can never make an old status look fresh for longer than CLOCK_SKEW_S (None → the rules
    treat it as stale: at worst one recoverable relaunch)."""
    try:
        age = now - float(ts)
    except (TypeError, ValueError):
        return None
    if age < -CLOCK_SKEW_S:
        return None
    return max(0.0, age)


def evaluate(status: Optional[Dict[str, Any]], *, pid: Optional[int], now: float,
             log_age_s: Optional[float]) -> Dict[str, Any]:
    if pid is None:
        return {"verdict": "missing", "reason": "no_process"}
    # Anything but a JSON object (a list, a number, null — a corrupt or foreign file) is "no usable status".
    st = status if isinstance(status, dict) else {}
    if not st or str(st.get("pid")) != str(pid) or st.get("state") not in IDLE_STATES + ("working",):
        why = ("no_status" if not st else
               ("status_of_other_pid" if str(st.get("pid")) != str(pid) else f"unknown_state:{st.get('state')}"))
        stalled = log_age_s is not None and log_age_s > STALE_LOG_S
        return {"verdict": "stalled" if stalled else "ok", "reason": f"legacy_log_age({why})",
                "log_age_s": log_age_s}
    hb_age = _age(now, st.get("updated_at"))
    state = st["state"]
    if state in IDLE_STATES:
        rest_until = st.get("rest_until")
        try:
            overrun = now - float(rest_until)
        except (TypeError, ValueError):
            return {"verdict": "stalled", "reason": "idle_without_rest_until", "heartbeat_age_s": hb_age}
        if overrun > REST_GRACE_S:
            return {"verdict": "stalled", "reason": "rest_overrun", "overrun_s": overrun, "heartbeat_age_s": hb_age}
        if hb_age is None or hb_age > HEARTBEAT_MAX_AGE_S:
            return {"verdict": "stalled", "reason": "idle_heartbeat_stale", "heartbeat_age_s": hb_age}
        return {"verdict": "idle", "reason": state, "rest_remaining_s": max(0.0, -overrun),
                "heartbeat_age_s": hb_age}
    progress_age = _age(now, st.get("last_progress_at"))
    if progress_age is None or progress_age > PROGRESS_STALL_S:
        return {"verdict": "stalled", "reason": "no_progress", "progress_age_s": progress_age,
                "heartbeat_age_s": hb_age, "workers": _worker_summary(st, now)}
    return {"verdict": "ok", "reason": "working", "progress_age_s": progress_age, "heartbeat_age_s": hb_age,
            "workers": _worker_summary(st, now)}


def _worker_summary(st: Dict[str, Any], now: float) -> Dict[str, Any]:
    out = {}
    workers = st.get("workers")
    for name, w in (workers.items() if isinstance(workers, dict) else ()):
        w = w if isinstance(w, dict) else {}
        out[name] = {"busy_s": _age(now, w.get("started_at")), "progress_age_s": _age(now, w.get("last_progress_at"))}
    return out


def log_line(pid: Optional[int], result: Dict[str, Any], log_age_s: Optional[float]) -> str:
    """One structured, log-safe line (no URLs, no supplier data, no secrets)."""
    def _i(v):
        return None if v is None else int(v)
    return (f"[car_parts_ie_healthcheck] alive={pid is not None} pid={pid} verdict={result['verdict']} "
            f"reason={result['reason']} progress_age_s={_i(result.get('progress_age_s'))} "
            f"heartbeat_age_s={_i(result.get('heartbeat_age_s'))} log_age_s={_i(log_age_s)} "
            f"status={'STALLED' if result['verdict'] == 'stalled' else ('MISSING' if pid is None else 'ok')}")


async def health_pass(pid: Optional[int], *, log_path: str, status_path: str, now: float, kill) -> Dict[str, Any]:
    """ONE healthcheck pass — the exact decision path the supervisor loop runs (#78). kill(pid, label) is awaited
    only on a "stalled" verdict. Returns the verdict (with the printed line under "line")."""
    import json as _json
    import os as _os
    log_age_s = (now - _os.path.getmtime(log_path)) if _os.path.exists(log_path) else None
    try:
        with open(status_path) as fh:
            status = _json.load(fh)
    except (OSError, ValueError):
        status = None
    result = evaluate(status, pid=pid, now=now, log_age_s=log_age_s)
    result["line"] = log_line(pid, result, log_age_s)
    if result["verdict"] == "stalled" and pid is not None:
        await kill(pid, f"stalled harvester ({result['reason']})")
    return result


_OFF = ("0", "false", "no", "off")
_ON = ("1", "true", "yes", "on")
TOGGLE_VARS = ("CPIE_HARVEST_ENABLED", "HARVESTER_ENABLED")   # specific first; HARVESTER_ENABLED = legacy alias


def harvest_enabled(env=None) -> tuple:
    """(enabled, source) for the car-parts.ie harvester (#93). The FIRST of TOGGLE_VARS that is set decides; an
    explicit 0/false/no/off disables. Unset everywhere → enabled (production sets neither: behaviour unchanged).
    An unrecognised value does NOT disable (a typo must not silently stop production) and is reported."""
    import os as _os
    env = _os.environ if env is None else env
    for name in TOGGLE_VARS:
        raw = env.get(name)
        if raw is None or str(raw).strip() == "":
            continue
        v = str(raw).strip().lower()
        if v in _OFF:
            return False, f"{name}={raw}"
        if v in _ON:
            return True, f"{name}={raw}"
        return True, f"{name}={raw!r} unrecognised → enabled"
    return True, "default (no toggle set)"
