"""Test the harvest-status notification POLICY.

Owner complaint (2026-07-29): "fix the messages i get in whatsapp about tasks
that is done — it should not be sent. i asked to be notified about the status of
the import if it's idle, so i don't have to get reminders."

The old loop sent an hourly progress report unconditionally, so a healthy day
produced ~13 WhatsApps inside the notify window and a genuine stall looked
exactly like the other twelve. The policy is now: silent while healthy, speak on
stall/idle, speak once on recovery, chase a persistent stall only every 6h.

These assert the POLICY, not the wording — the function under test is the same
one the live loop calls.

Run: docker exec autospare_backend python3 /app/devtests/harvest_notify_policy_test.py
"""
import sys

sys.path.insert(0, "/app")
from BACKEND_API_ROUTES import _harvest_status_decision as decide  # noqa: E402

REALERT = 21600  # 6h, the production default
fails = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got={got} want={want}")
    if not ok:
        fails.append(label)


print("1. a healthy, progressing harvester is SILENT")
state, send = decide(first_sample=False, d_models=3, d_parts=1200, in_progress=2,
                     pending=500, prev_state="ok", secs_since_alert=None,
                     realert_s=REALERT)
check("progressing", (state, send), ("ok", False))

print("\n2. the FIRST sample never alerts (no measured window yet)")
state, send = decide(first_sample=True, d_models=0, d_parts=0, in_progress=0,
                     pending=0, prev_state=None, secs_since_alert=None,
                     realert_s=REALERT)
check("first sample", (state, send), ("ok", False))

print("\n3. work queued but not moving => STALLED, alerts once")
state, send = decide(first_sample=False, d_models=0, d_parts=0, in_progress=2,
                     pending=800, prev_state="ok", secs_since_alert=None,
                     realert_s=REALERT)
check("stall detected", (state, send), ("stalled", True))

print("\n4. the SAME stall an hour later does NOT re-alert")
state, send = decide(first_sample=False, d_models=0, d_parts=0, in_progress=2,
                     pending=800, prev_state="stalled", secs_since_alert=3600,
                     realert_s=REALERT)
check("stall repeat @1h", (state, send), ("stalled", False))

print("\n5. a stall still unresolved after 6h DOES chase up")
state, send = decide(first_sample=False, d_models=0, d_parts=0, in_progress=2,
                     pending=800, prev_state="stalled", secs_since_alert=REALERT,
                     realert_s=REALERT)
check("stall re-alert @6h", (state, send), ("stalled", True))

print("\n6. nothing in progress AND nothing pending => IDLE (this is what the owner asked for)")
state, send = decide(first_sample=False, d_models=0, d_parts=0, in_progress=0,
                     pending=0, prev_state="ok", secs_since_alert=None,
                     realert_s=REALERT)
check("idle detected", (state, send), ("idle", True))

print("\n7. recovery after a stall is announced exactly once")
state, send = decide(first_sample=False, d_models=5, d_parts=900, in_progress=3,
                     pending=700, prev_state="stalled", secs_since_alert=1000,
                     realert_s=REALERT)
check("recovery", (state, send), ("ok", True))
state, send = decide(first_sample=False, d_models=5, d_parts=900, in_progress=3,
                     pending=700, prev_state="ok", secs_since_alert=None,
                     realert_s=REALERT)
check("silent after recovery", (state, send), ("ok", False))

print("\n8. stall -> idle is a DIFFERENT state, so it is reported")
state, send = decide(first_sample=False, d_models=0, d_parts=0, in_progress=0,
                     pending=0, prev_state="stalled", secs_since_alert=60,
                     realert_s=REALERT)
check("stalled->idle", (state, send), ("idle", True))

print("\n9. HARVEST_REPORT_MODE=hourly restores the old behaviour (escape hatch)")
state, send = decide(first_sample=False, d_models=3, d_parts=1200, in_progress=2,
                     pending=500, prev_state="ok", secs_since_alert=None,
                     realert_s=REALERT, mode="hourly")
check("hourly mode", (state, send), ("ok", True))

# The headline number: simulate a full healthy day, hour by hour.
print("\n10. VOLUME — 24h of a healthy harvester")
sent = 0
prev = None
for _ in range(24):
    st, sd = decide(first_sample=False, d_models=2, d_parts=800, in_progress=2,
                    pending=400, prev_state=prev, secs_since_alert=None,
                    realert_s=REALERT)
    sent += 1 if sd else 0
    prev = st
check("messages in a healthy day", sent, 0)

# And a day where it breaks once and stays broken. NOTE: the elapsed time is
# derived from an absolute clock, exactly as the loop does
# (`_now - _harvest_alert_sent_utc`) — do NOT model it as a counter bumped after
# the decision, which silently adds an hour of lag per cycle and understates the
# alert count.
print("\n11. VOLUME — 24h where it stalls at hour 3 and never recovers")
sent, prev, last_sent_h = 0, "ok", None
for h in range(24):
    st, sd = decide(first_sample=False,
                    d_models=(2 if h < 3 else 0), d_parts=(800 if h < 3 else 0),
                    in_progress=2, pending=400, prev_state=prev,
                    secs_since_alert=(None if last_sent_h is None
                                      else (h - last_sent_h) * 3600),
                    realert_s=REALERT)
    if sd:
        sent += 1
        last_sent_h = h
    prev = st
# detected at h3, then chased at h9, h15, h21 — every 6h while unresolved.
check("alerts for a day-long stall", sent, 4)

print()
if fails:
    print(f"FAILED: {len(fails)} -> {fails}")
    sys.exit(1)
print("ALL PASS — healthy harvester is silent; stall and idle both reported.")
