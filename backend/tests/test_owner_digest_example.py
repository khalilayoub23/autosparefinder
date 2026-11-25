"""Mission 4: the representative morning digest built from the redacted real-event fixtures, with the numbers the owner asked
for. The rendered message is written to $DIGEST_EXAMPLE_OUT when set (docs evidence); nothing here reaches WhatsApp."""
import json
import os
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
from test_owner_digest import app, run, od, TZ  # noqa: E402,F401  (the `app` fixture: real notify_owner + fake Redis/bridge)
from owner_messages_2026_11 import ASAP_NEWSLETTER, DB_AGENT_BODY, DISK_SAMPLES  # noqa: E402


def test_representative_morning_digest_with_counts(app):
    from email_agent import owner_flow
    n = app.a.notify_owner
    calls = 0

    def go(*a, **k):
        nonlocal calls
        calls += 1
        return run(n(*a, **k))
    for row in ASAP_NEWSLETTER:                                   # one supplier newsletter: 10 mails, each also seen by BOAZ
        go("email", "מייל חדש — ספק", "מאת: ASAP Network", alert_key=f"email_{row['id']}", event_keys=owner_flow.event_keys(row))
        go("supplier", "BOAZ צריך החלטה", "x", alert_key=f"boaz_{row['id']}", event_keys=[f"mail:{owner_flow.ref(row)}"])
    for d in DISK_SAMPLES:                                         # same condition, two mount points
        go("health", f"שימוש בדיסק {d['pct']}%", "הדיסק מתמלא", severity="warning", alert_key="disk_warning")
    for _ in range(3):                                             # a persistent warning that keeps reporting the same thing
        go("harvest", "ייבוא ספק ממתין לאישור", "יש קובץ מחירים חדש", severity="warning", alert_key="import_wait", fingerprint="wait")
    go("harvest", "ייבוא ספק ממתין לאישור", "הקובץ עודכן — 2 קבצים", severity="warning", alert_key="import_wait", fingerprint="wait-2")  # real change
    go("health", "עבודת הלילה: 3 משימות נכשלו", DB_AGENT_BODY, severity="error", alert_key="dbfail")
    go("social", "NOA בפייסבוק אתמול", "20 טיוטות ממתינות", alert_key="noa_eod")
    go("health", "סנכרון מחירים הושלם", "ok", severity="success", alert_key="job_ok")
    go("health", "תקלת סנכרון", "x", severity="warning", alert_key="inc", fingerprint="open", state="new")
    run(app.a.resolve_owner_alert("health", "תקלת הסנכרון נפתרה", "ok", alert_key="inc"))
    go("supplier", "ספק חדש ממתין לאישורך", "נמצא ספק חדש", alert_key="sup_new", ref="S12", choices=[("approve", "אשרספק S12"), ("reject", "דחהספק S12")])
    go("orders", "הזמנה ממתינה לתשלום", "הזמנה פתוחה", alert_key="order_wait", ref="O77", choices=[("open", "הזמנות")])
    go("system", "שרת בסיס הנתונים לא זמין", "אין חיבור", severity="critical", alert_key="db_down")   # CRITICAL: immediate, not here

    immediate = [d for d in app.direct if d["critical"]]
    assert len(immediate) == 1 and "בסיס הנתונים" in immediate[0]["text"] and len(app.direct) == 1
    assert app.bridge == []                                       # nothing routine left before the digest
    st_before = run(od.Store(app.r).status())
    pending = run(od.Store(app.r).pending())
    res = run(app.a._owner_digest_pass(force=True))
    assert res["ok"] and len(app.bridge) == 1
    text = app.bridge[0]["text"]
    stats = st_before["stats"]
    sections = od.group_events(pending)
    non_empty = {k: len(v) for k, v in sections.items() if v}
    n_action = sum(1 for e in pending if od.section_for(e) == "action")
    repeats = sum(int(e.get("repeats", 1)) - 1 for e in pending)
    summary = {
        "producer_calls": calls + 1, "critical_sent_immediately": len(immediate),
        "routine_events_collected": calls - len(immediate) + 1,   # + the resolve call
        "merged_as_duplicates": stats.get("merged_duplicate", 0) + stats.get("merged_duplicate_after_delivery", 0),
        "repeats_suppressed_by_cooldown_or_unchanged": stats.get("repeat_suppressed", 0) + stats.get("repeat_suppressed_after_delivery", 0),
        "suppressed_unchanged": stats.get("suppressed_unchanged", 0),
        "replaced_by_newer_version": stats.get("replaced_version", 0), "opened_and_resolved_in_one_digest": stats.get("resolved_pending", 0),
        "occurrences_counted_as_repeats": repeats, "items_in_digest": len(pending), "sections": non_empty, "action_items": n_action,
        "digest_id": res["digest"], "message_chars": len(text), "event_ids": [e["id"] for e in pending],
    }
    out = os.getenv("DIGEST_EXAMPLE_OUT")
    if out:
        Path(out).write_text(text + "\n\n--- SUMMARY ---\n" + json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    # the claims the owner asked to be proven
    assert summary["merged_as_duplicates"] >= 19 and stats.get("merged_duplicate_after_delivery", 0) == 0 and summary["repeats_suppressed_by_cooldown_or_unchanged"] >= 2 and summary["occurrences_counted_as_repeats"] >= 23
    assert summary["items_in_digest"] < summary["routine_events_collected"] / 2          # real consolidation
    assert text.count("מייל חדש") <= 1 and text.count("BOAZ צריך") <= 1                  # newsletter x10 (+BOAZ) = one line
    assert "חזר" in text
    assert text.count("שימוש בדיסק") == 1                                                # disk x2 = one line
    assert text.count("ייבוא ספק ממתין לאישור") == 1 and "🔄" in text                    # repeats merged, the real change retained
    assert "נפתח ונפתר" in text and "אשרספק S12" in text and "#S12" in text and "#O77" in text
    assert "בסיס הנתונים לא זמין" not in text                                            # critical stays out of the routine digest
    assert len(text) <= od.MSG_LIMIT
    # empty store -> nothing is sent
    before = len(app.bridge)
    assert run(app.a._owner_digest_pass(force=True)).get("skipped") == "empty" and len(app.bridge) == before
