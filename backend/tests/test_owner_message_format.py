"""Owner message formatter - regression on real 2026-11 messages (audit 2026-11-24, #152)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
import owner_message_format as f
from owner_messages_2026_11 import DB_AGENT_BODY, DISK_SAMPLES, FLARE_TEXTS


def test_hebrew_plural_fixes_1_mishimot():
    assert f.he_count(1, "task") == "משימה אחת"
    assert f.he_count(2, "task") == "שתי משימות"
    assert f.he_count(3, "task") == "3 משימות"
    assert f.he_count(0, "task") == "0 משימות"
    assert f.he_count(1, "item") == "פריט אחד"
    assert f.he_count(1, "unknown-noun") == "1 unknown-noun"


def test_db_traceback_is_replaced_by_short_hebrew_and_reference():
    clean, raw = f.sanitize_body(DB_AGENT_BODY)
    for banned in ("sqlalche", "Background on this error", "RETURNING", "is_active", "fff4e30a", "http"):
        assert banned not in clean, banned
    assert "dedup_catalog_parts" in clean and "fix_manufacturer_overflow" in clean and "flag_fake_skus" in clean
    assert "שגיאת בסיס נתונים" in clean and "אסמכתה" in clean
    assert "חרגה ממגבלת הזמן (30 דקות)" in clean
    assert "15 הצליחו" in clean                       # the summary line survives
    assert raw, "raw diagnostics must be returned for the log"


def test_reference_is_stable_for_same_defect():
    a = f.ref_for_error("UndefinedColumn x at row 17 id 8ea8faa5-0000-0000-0000-000000000000")
    b = f.ref_for_error("UndefinedColumn x at row 99 id 11111111-0000-0000-0000-000000000000")
    assert a == b and len(a) == 6


def test_shell_command_and_fence_removed():
    body = "הקטלוג לא מתקדם.\nפעולה — הוסף swap לשרת:\nfallocate -l 4G /swapfile && swapon /swapfile\n```docker exec x```"
    clean, raw = f.sanitize_body(body)
    assert "fallocate" not in clean and "docker exec" not in clean and "```" not in clean
    assert "הקטלוג לא מתקדם." in clean and len(raw) == 2


def test_plain_hebrew_is_untouched():
    body = "שירות מנוע החיפוש נפל.\nבדיקה אוטומטית כל 5 דקות."
    assert f.sanitize_body(body) == (body, [])


def test_disk_two_mounts_one_storage():
    assert f.disk_same_storage(*DISK_SAMPLES)
    assert len(f.distinct_samples(DISK_SAMPLES)) == 1
    other = dict(DISK_SAMPLES[0], total=10**11)
    assert len(f.distinct_samples([DISK_SAMPLES[0], other])) == 2


def test_cap_cuts_on_line_boundary():
    t = "\n".join(["שורה מספר %d" % i for i in range(200)])
    out = f.cap_message(t, 200)
    assert len(out) < 230 and out.endswith("…") and "שורה מספר 0" in out
    assert f.cap_message("קצר", 200) == "קצר"


def test_flare_old_texts_contain_the_defects_we_remove():
    clean, _ = f.sanitize_body(FLARE_TEXTS[0].split("\n\n", 1)[1])
    assert "fallocate" not in clean and "swapon" not in clean


def test_reversed_hebrew_keyword_candidates_are_not_offered_to_the_owner():
    import category_learning as cl
    for bad in ("ינשמ", "םינטיינ", "ןפ"):     # visual-order tokens; `תונולח` (starts/ends with legal letters) is a known miss
        assert cl.looks_reversed_hebrew(bad), bad
    for ok in ("בלמים", "מסנן", "רפידות", "filter", "אור"):
        assert not cl.looks_reversed_hebrew(ok), ok
