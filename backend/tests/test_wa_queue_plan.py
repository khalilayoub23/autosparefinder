"""Night-queue consolidation + channel-outage classification (audit 2026-11-24, #152). Pure logic, no I/O."""
import wa_queue_plan as q


def item(group, state="", sev="info", title="t", text=None, **kw):
    d = {"to": "x", "text": text or f"ℹ️ [{group}] {title}", "alert_key": kw.get("alert_key", group),
         "meta": {"group": group, "state": state, "sev": sev, "title": title}}
    return d


def dec(items):
    return [d["decision"] for d in q.plan_flush(items)]


def test_opened_and_resolved_overnight_is_merged_not_delivered():
    items = [item("catalog_stagnation", "new", "warning"), item("catalog_stagnation", "resolved", "success")]
    assert dec(items) == ["merged", "merged"]


def test_critical_alert_is_never_merged_away():
    items = [item("svc", "new", "critical"), item("svc", "resolved", "success")]
    assert dec(items) == ["deliver", "deliver"]


def test_older_state_superseded_by_newer():
    items = [item("disk:/", "", "warning", "80%"), item("disk:/", "", "warning", "82%")]
    assert dec(items) == ["superseded", "deliver"]


def test_routine_updates_collapse_into_one_digest_but_decisions_and_critical_stay():
    routine = [item(f"g{i}", "new", "info", f"u{i}") for i in range(5)]
    action = item("posts", "action", "info", "approve", text="👉 *אשר 1*")
    crit = item("oom", "new", "critical", "oom")
    d = dec(routine + [action, crit])
    assert d[:5] == ["digest"] * 5 and d[5:] == ["deliver", "deliver"]


def test_few_routine_updates_are_delivered_as_is():
    assert dec([item(f"g{i}", "new", "info") for i in range(3)]) == ["deliver"] * 3


def test_legacy_items_without_meta_are_never_touched():
    legacy = [{"to": "x", "text": "a", "alert_key": "k"}, {"to": "x", "text": "b", "alert_key": "k"}]
    assert dec(legacy) == ["deliver", "deliver"]


def test_digest_text_is_short_and_has_no_detail():
    items = [item(f"g{i}", "new", "info", f"עדכון {i}") for i in range(14)]
    out = q.build_digest(items, "מ-23/11 21:05")
    assert out.count("\n") <= q.DIGEST_MAX_LINES + 3 and "…ועוד 4" in out and "(14)" in out


def test_channel_error_kinds_from_the_real_outage():
    assert q.channel_error_kind("WhatsApp not authenticated — awaiting QR scan") == "down"
    assert q.channel_error_kind("WhatsApp session invalid (logged out by WhatsApp) — re-pairing required") == "down"
    assert q.channel_error_kind("bridge unreachable: x") == "down"
    assert q.channel_error_kind("INVALID_PAYLOAD: empty body") == "permanent"
    assert q.channel_error_kind("timeout") == "transient"
