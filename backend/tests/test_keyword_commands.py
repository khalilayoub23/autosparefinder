"""Owner console keyword decisions act on the EXACT stored token, multi-word included (FIXES_TRACKER #72).

Before: `(\\S+)` captured only the first word — "דחהמילה brake pad" acted on the DIFFERENT token "brake";
50 approved keywords are multi-word and could not be undone from the console; reject() of a mistyped
token still added it to the in-memory rejected set (blocking it until restart).

End-to-end cases go through process_owner_message (the real WhatsApp console entry) against the ISOLATED
sandbox catalogue DB only (tests/isolated_db_guard.py), with unique test tokens removed afterwards.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolated_db_guard  # noqa: E402
from agents import owner_console as oc  # noqa: E402
import category_learning as cl  # noqa: E402


# ── parsing (no DB) ───────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("rest,expected", [
    (" vest", ("vest", False, "vest")),
    ("  brake   pad  ", ("brake pad", False, "brake pad")),
    (" רפידות  בלם ", ("רפידות בלם", False, "רפידות בלם")),
    (" brake pad בכוח", ("brake pad", True, "brake pad בכוח")),
    (" brake pad force", ("brake pad", True, "brake pad force")),
    ("", ("", False, "")),
])
def test_argument_is_the_whole_rest_of_the_line(rest, expected):
    assert oc._keyword_args(rest) == expected


@pytest.mark.parametrize("raw,norm", [("  Brake   PAD ", "brake pad"), ("רפידות\tבלם", "רפידות בלם"), ("", "")])
def test_token_normalisation(raw, norm):
    assert cl.norm_token(raw) == norm


# ── end to end against the isolated sandbox DB ───────────────────────────────────────────────
pytestmark_e2e = pytest.mark.skipif(isolated_db_guard.static_refusal() is not None,
                                    reason=f"isolated DB only: {isolated_db_guard.static_refusal()}")


def _e2e(steps):
    """Insert test tokens, run console commands, return persisted statuses. One event loop."""
    from sqlalchemy import text
    from catalog_scraper import scraper_session_factory
    import random
    import string
    # letters only: the blocklist (correctly) refuses any token containing a digit
    tag = "zzt" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))

    async def main():
        async with scraper_session_factory() as db:
            refusal = await isolated_db_guard.live_refusal(db)
            assert refusal is None, f"REFUSED: {refusal}"
            await cl.ensure_table(db)
            rows = [(f"{tag} brake pad", "brakes", 9, "pending"), (f"{tag} brake pad", "engine", 1, "pending"),
                    (f"{tag} brake", "brakes", 9, "pending"), (f"{tag} רפידות בלם", "brakes", 8, "approved"),
                    (f"{tag}", "engine", 5, "pending")]
            for t, c, o, st in rows:
                await db.execute(text("INSERT INTO category_learned_keywords (token, category, observations, status) "
                                      "VALUES (:t, :c, :o, :s)"), {"t": t, "c": c, "o": o, "s": st})
            await db.commit()
        replies = []
        try:
            for cmd in steps(tag):
                replies.append(await oc.process_owner_message(cmd))
            async with scraper_session_factory() as db:
                got = (await db.execute(text(
                    "SELECT token, category, status FROM category_learned_keywords WHERE token LIKE :p ORDER BY 1, 2"),
                    {"p": f"{tag}%"})).fetchall()
            return tag, replies, {(r[0], r[1]): r[2] for r in got}, set(cl._rejected_tokens)
        finally:
            async with scraper_session_factory() as db:
                await db.execute(text("DELETE FROM category_learned_keywords WHERE token LIKE :p"), {"p": f"{tag}%"})
                await db.commit()
            for t in [x for x in cl._rejected_tokens if x.startswith(tag)]:
                cl._rejected_tokens.discard(t)
            # pooled asyncpg connections belong to THIS event loop; the next test runs a new one
            import catalog_scraper
            import BACKEND_DATABASE_MODELS as _m
            await catalog_scraper._engine.dispose()
            await _m.engine.dispose()
    return asyncio.run(main())


@pytestmark_e2e
def test_reject_multiword_hebrew_with_messy_spaces_changes_only_that_token():
    tag, replies, st, rejected = _e2e(lambda t: [f"דחהמילה   {t}   רפידות    בלם  "])
    assert st[(f"{tag} רפידות בלם", "brakes")] == "rejected"
    assert st[(f"{tag} brake", "brakes")] == "pending" and st[(f"{tag}", "engine")] == "pending"
    assert st[(f"{tag} brake pad", "brakes")] == "pending"
    assert replies[0].startswith(f"🚫 *{tag} רפידות בלם* נדחתה")


@pytestmark_e2e
def test_reject_multiword_english_never_hits_the_first_word_token():
    tag, replies, st, _ = _e2e(lambda t: [f"דחהמילה {t} brake pad"])
    assert st[(f"{tag} brake pad", "brakes")] == "rejected" and st[(f"{tag} brake pad", "engine")] == "rejected"
    assert st[(f"{tag} brake", "brakes")] == "pending"           # the old parser would have hit "<tag>"
    assert st[(f"{tag}", "engine")] == "pending"


@pytestmark_e2e
def test_approve_multiword_with_force_approves_the_winning_row_only():
    tag, replies, st, _ = _e2e(lambda t: [f"אשרמילה {t} brake pad בכוח"])
    assert st[(f"{tag} brake pad", "brakes")] == "approved"      # winning category (9 votes)
    assert st[(f"{tag} brake pad", "engine")] == "rejected"      # the losing vote row
    assert st[(f"{tag} brake", "brakes")] == "pending"
    assert replies[0].startswith(f"✅ *{tag} brake pad* אושרה")


@pytestmark_e2e
def test_single_word_still_works_both_ways():
    tag, replies, st, _ = _e2e(lambda t: [f"אשרמילה {t} בכוח", f"דחהמילה {t} brake"])
    assert st[(f"{tag}", "engine")] == "approved"
    assert st[(f"{tag} brake", "brakes")] == "rejected"
    assert st[(f"{tag} brake pad", "brakes")] == "pending"


@pytestmark_e2e
def test_nonexistent_or_partial_token_changes_nothing_and_is_not_blocklisted():
    tag, replies, st, rejected = _e2e(lambda t: [f"דחהמילה {t} brake p", f"אשרמילה {t} רפידות בכוח"])
    assert all(v in ("pending", "approved") for v in st.values())               # nothing rejected/approved
    assert st[(f"{tag} רפידות בלם", "brakes")] == "approved"                    # untouched
    assert f"{tag} brake p" not in rejected                                       # not blocked until restart
    assert "לא מצאתי מילה" in replies[0] and "לא נדחה דבר" in replies[0]
    assert "לא מצאתי מילה" in replies[1] and "לא אושר דבר" in replies[1]
    # read-only suggestions = stored tokens that extend the input by whole words
    assert f"*{tag} brake pad*" not in replies[0] or "אולי התכוונת" in replies[0]
    assert "אולי התכוונת ל:" in replies[1] and f"*{tag} רפידות בלם*" in replies[1]
