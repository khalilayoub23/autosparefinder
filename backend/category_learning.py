"""
category_learning.py — persistence for keywords the LLM assist teaches the
deterministic categorizer.

THE MODEL: the LLM is a HELPER AND GUIDE for a STUCK WORKER, never the worker.

  1. `category_map` (keyword rules) classifies the catalog. It is deterministic,
     free, and handles ~74% of the historical backlog on its own.
  2. When it genuinely cannot place a part, that part is recorded as stuck.
  3. `db_cleanup_agent.task3b` asks the LLM about a SMALL, RATE-LIMITED sample of
     the stuck rows.
  4. The answers are mined for the token that distinguishes them, and — only when
     several independent parts agree — that token is written here as a LEARNED
     KEYWORD.
  5. From then on the keyword matcher handles that shape of name by itself. The
     LLM is needed less every cycle instead of more.

This is why the 2026-07-27 quota blowout must not recur: that design called the
LLM on 500 parts every 30 seconds *forever*, because nothing it produced was ever
fed back into the rules. Work the LLM does here is permanent.

`category_map` stays free of DB imports on purpose — it is imported by importers,
scrapers and the API. All persistence lives in this module.

Data Modified: category_learned_keywords (created on demand)
Last Updated:  2026-07-27
"""
from __future__ import annotations

import logging
import os
import re
from typing import Dict, Iterable, List, Sequence, Tuple

from sqlalchemy import text

import category_map

logger = logging.getLogger("category_learning")

_TABLE_READY = False

# A token is only promoted to a rule once this many DIFFERENT parts got the same
# category from the LLM. One agreeing example is an anecdote, not a rule.
# Counted CUMULATIVELY across calls, never within a single batch.
MIN_CONSENSUS = 3
# ...and at least this share of the observations for that token must agree.
MIN_AGREEMENT = 0.8



# ── NEVER-LEARN BLOCKLIST ─────────────────────────────────────────────────────
# Measured 2026-07-27 over the real 403,327-part stuck population: the most
# FREQUENT unknown tokens are overwhelmingly things that must never become a
# category rule. Without this guard the LLM would have taught, at scale:
#   • brand names  — 'rover' 13,068 parts, 'land' 13,144, 'ssangyong' 3,878,
#     'mercedes' 2,623, 'benz' 2,585. One bad vote files every Land Rover part
#     under one category. This is the catastrophic case.
#   • bare fasteners — 'bolt' 16,541, 'washer' 10,444, 'screw' 8,898,
#     'בורג' 8,885, 'shim', 'ring', 'nut', 'snap', 'retainer'. Deliberately
#     left in 'כללי' (a wrong category is worse than the catch-all); the LLM
#     had already voted 'bolt'→service-general, which would have mis-filed
#     16,541 parts.
#   • position/orientation words — 'קד' (front) 4,106, 'אח' (rear) 3,042,
#     'ימין' (right) 2,775. They describe WHERE a part sits, not what it is.
#   • sizes / packaging — 'xxl', quantities, dimension codes.
#
# Brand names are loaded LIVE from car_brands + parts_catalog.manufacturer so the
# list cannot go stale as new brands are imported.
_STATIC_BLOCKLIST = {
    # bare fasteners / hardware — no category without knowing what they fasten
    "bolt", "screw", "nut", "washer", "shim", "ring", "snap", "circlip",
    "retainer", "clip", "pin", "stud", "rivet", "spacer", "dowel", "grommet",
    "בורג", "ברגים", "אום", "דסקית", "טבעת", "מסמרת",
    "مسمار", "برغي", "صامولة", "حلقة",
    # position / orientation — describes where, not what
    "front", "rear", "back", "left", "right", "upper", "lower", "inner",
    "outer", "side", "middle", "centre", "center", "top", "bottom",
    "קד", "קדמי", "קדמית", "אח", "אחורי", "אחורית", "ימין", "שמאל",
    "ימני", "שמאלי", "עליון", "תחתון", "פנימי", "חיצוני",
    "أمامي", "خلفي", "يمين", "يسار", "علوي", "سفلي",
    # Occupant/seat-position words — same class: they say WHERE, not WHAT.
    # The embedding assist proposed `נוסע` (passenger) -> safety-systems on its
    # first live run; every seat, mirror, door and airbag row contains it.
    "נוסע", "נהג", "passenger", "driver", "עזר",
    # size / packaging / commercial noise
    "xxl", "xxxl", "large", "small", "medium", "pack", "pcs", "piece",
    "unit", "std", "std.", "size", "length", "width", "diameter",
    # generic descriptors that carry no part identity
    "assembly", "assy", "sub", "comp", "genuine", "aftermarket", "quality",
    "premium", "standard", "universal", "replacement",
}

_brand_blocklist: set = set()
_rejected_tokens: set = set()


async def load_brand_blocklist(db) -> int:
    """
    Load brand/manufacturer names from the LIVE DB so the LLM can never learn one
    as a category keyword. Refreshed on startup; cheap (two indexed reads).
    """
    global _brand_blocklist
    names: set = set()
    for sql in (
        "SELECT name FROM car_brands WHERE is_active",
        "SELECT DISTINCT manufacturer FROM parts_catalog WHERE is_active "
        "AND manufacturer IS NOT NULL",
    ):
        try:
            for row in (await db.execute(text(sql))).fetchall():
                raw = (row[0] or "").strip().lower()
                if not raw:
                    continue
                names.add(raw)
                # Multi-word brands must also block each component word:
                # "Land Rover" blocks 'land' and 'rover'. Split on hyphens and
                # slashes too — "Mercedes-Benz" must block 'mercedes' AND 'benz'.
                for word in re.split(r"[\s\-/_.,()]+", raw):
                    if len(word) >= 3:
                        names.add(word)
        except Exception as exc:  # a missing table must not break learning
            logger.warning("brand blocklist source failed (%s): %s", sql[:40], exc)
    _brand_blocklist = names
    logger.info("brand blocklist loaded: %d names/words", len(names))
    return len(names)


def is_blocked(token: str) -> bool:
    """True if this token must never become a category keyword."""
    t = (token or "").strip().lower()
    if not t or t in _STATIC_BLOCKLIST or t in _brand_blocklist:
        return True
    # Pure digits / alphanumeric codes carry no category meaning.
    if any(ch.isdigit() for ch in t):
        return True
    return False


async def ensure_table(db) -> None:
    """Create the learned-keyword table if it does not exist (idempotent)."""
    global _TABLE_READY
    if _TABLE_READY:
        return
    # One row per (token, category) VOTE. Consensus accumulates ACROSS calls:
    # a 25-part batch almost never contains the same token 3x on its own, so
    # gating inside a single batch would learn nothing, ever. Votes are tallied
    # here and a token only becomes ACTIVE once its winning category clears
    # MIN_CONSENSUS observations and MIN_AGREEMENT of all votes for that token.
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS category_learned_keywords (
            token        TEXT NOT NULL,
            category     TEXT NOT NULL,
            observations INTEGER NOT NULL DEFAULT 1,
            source       TEXT    NOT NULL DEFAULT 'llm_assist',
            -- 'pending'  = consensus reached, AWAITING OWNER APPROVAL
            -- 'approved' = live in the matcher
            -- 'rejected' = never load, never re-propose
            status       TEXT    NOT NULL DEFAULT 'pending',
            -- Which INPUT TYPE produced this suggestion (english short
            -- descriptive / hebrew descriptive / ...). Stamped at proposal time
            -- so every owner approve/reject becomes attributable evidence for
            -- the Phase-2 promotion decision. Without this, "enable auto-write
            -- for the types that proved accurate" has no data to stand on.
            input_type   TEXT,
            created_at   TIMESTAMP NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMP NOT NULL DEFAULT NOW(),
            PRIMARY KEY (token, category)
        )
    """))
    # Additive migration for tables created before the approval gate existed.
    await db.execute(text(
        "ALTER TABLE category_learned_keywords "
        "ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'"
    ))
    await db.execute(text(
        "ALTER TABLE category_learned_keywords "
        "ADD COLUMN IF NOT EXISTS input_type TEXT"
    ))
    await db.commit()
    _TABLE_READY = True


async def load_into_matcher(db) -> int:
    """
    Load every persisted learned keyword into the in-process matcher.
    Call at startup and whenever new keywords are written.
    Returns the number accepted (some may be rejected as already hand-written).
    """
    await ensure_table(db)
    if not _brand_blocklist:
        await load_brand_blocklist(db)
    rows = (await db.execute(text(f"""
        SELECT token, category FROM (
            SELECT token, category, observations, status,
                   SUM(observations) OVER (PARTITION BY token)    AS total,
                   ROW_NUMBER() OVER (PARTITION BY token
                                      ORDER BY observations DESC) AS rn
            FROM category_learned_keywords
        ) v
        WHERE rn = 1
          AND status = 'approved'
          AND observations >= {MIN_CONSENSUS}
          AND observations::float / total >= {MIN_AGREEMENT}
    """))).fetchall()
    # BLOCKLIST IS ENFORCED HERE TOO, NOT ONLY AT MINE TIME.
    # Filtering only on write does not protect rows written BEFORE the filter
    # existed — proved live 2026-07-27: 'bolt' and 'washer' had already been
    # voted in and activated as service-general from a pre-blocklist run, and
    # would have been bulk-applied to ~27,000 parts. A guard must run on the
    # path that USES the data, not just the path that produces it.
    kept = {r[0] for r in rows if not is_blocked(r[0])}
    blocked = [r[0] for r in rows if r[0] not in kept]
    if blocked:
        logger.warning(
            "ignoring %d blocklisted learned keyword(s): %s",
            len(blocked), ", ".join(sorted(blocked)[:10]),
        )
    accepted = category_map.register_learned_keywords(
        [(r[0], r[1]) for r in rows if r[0] in kept]
    )
    if rows:
        logger.info(
            "loaded %d learned keywords (%d accepted) — matcher now %s",
            len(rows), accepted, category_map.learned_stats(),
        )
    return accepted


def mine_tokens(
    classified: Sequence[Tuple[str, str]],
) -> List[Tuple[str, str, int, float]]:
    """
    Given [(part_text, llm_category), ...], find tokens that consistently predict
    a category.

    Returns [(token, category, observations, agreement), ...] for tokens that
    clear MIN_CONSENSUS / MIN_AGREEMENT and are not already handled by a
    hand-written rule.

    Only tokens the deterministic matcher does NOT already understand are worth
    learning — anything it can already place is, by definition, not why the
    worker got stuck.
    """
    import collections
    import re as _re

    # RTL tokens need >=3 chars, same floor as category_map._RTL_SHORT_MAX.
    # A 2-char Hebrew fragment carries no part meaning — the live test proposed
    # 'לת' (a broken fragment of a longer word) as a body-exterior keyword.
    WORD = _re.compile(r"[a-zA-Z]{3,}|[֐-׿؀-ۿ]{3,}")
    STOP = {
        "assy", "assembly", "for", "the", "and", "with", "rear", "front",
        "left", "right", "upper", "lower", "inner", "outer", "side", "kit",
        "set", "part", "oem", "new", "sub", "comp", "complete", "genuine",
        "original", "type", "size", "black", "white", "grey", "gray",
    }

    counts: Dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter
    )
    for text_blob, cat in classified:
        if cat not in category_map.CANONICAL or cat == category_map.CATCH_ALL:
            continue
        seen = set()
        for w in WORD.findall((text_blob or "").lower()):
            if w in STOP or w in seen:
                continue
            seen.add(w)
            counts[w][cat] += 1

    # Emit every (token, category) VOTE seen in this batch. The consensus and
    # agreement gates are applied later against the ACCUMULATED tally in the DB —
    # requiring 3 agreeing parts inside one 25-part prompt would learn nothing.
    out: List[Tuple[str, str, int, float]] = []
    for token, cat_counts in counts.items():
        # Already understood deterministically -> nothing to learn.
        if category_map.categorize(name=token) != category_map.CATCH_ALL:
            continue
        # Brand name, fastener, position word, size code -> must never be a rule.
        if is_blocked(token):
            continue
        # The owner already rejected this token — do not keep re-proposing it.
        if token in _rejected_tokens:
            continue
        total = sum(cat_counts.values())
        for cat, n in cat_counts.items():
            out.append((token, cat, n, n / total))
    return out


async def persist(db, learned: Iterable[Tuple[str, str, int, float]]) -> int:
    """
    UPSERT mined keywords and register them in the live matcher.
    Returns how many were newly accepted by the matcher.
    """
    learned = list(learned)
    if not learned:
        return 0
    await ensure_table(db)
    for token, cat, obs, _agreement in learned:
        await db.execute(text("""
            INSERT INTO category_learned_keywords
                   (token, category, observations, source)
            VALUES (:t, :c, :o, 'llm_assist')
            ON CONFLICT (token, category) DO UPDATE SET
                observations = category_learned_keywords.observations
                               + EXCLUDED.observations,
                updated_at   = NOW()
        """), {"t": token, "c": cat, "o": obs})
    await db.commit()

    # Re-read the accumulated tally and activate only what now clears the gates.
    accepted = await load_into_matcher(db)
    logger.info(
        "recorded %d keyword vote(s); %d token(s) active. matcher=%s",
        len(learned), accepted, category_map.learned_stats(),
    )
    return accepted


async def purge_blocklisted(db) -> int:
    """
    Delete votes for tokens that must never become rules. Run at startup so rows
    recorded before a blocklist entry existed cannot activate later.
    """
    await ensure_table(db)
    if not _brand_blocklist:
        await load_brand_blocklist(db)
    tokens = [
        r[0] for r in (await db.execute(text(
            "SELECT DISTINCT token FROM category_learned_keywords"
        ))).fetchall()
        if is_blocked(r[0])
    ]
    if not tokens:
        return 0
    await db.execute(
        text("DELETE FROM category_learned_keywords WHERE token = ANY(:t)"),
        {"t": tokens},
    )
    await db.commit()
    logger.warning("purged %d blocklisted token(s): %s",
                   len(tokens), ", ".join(sorted(tokens)[:12]))
    return len(tokens)


# ── OWNER REVIEW GATE ─────────────────────────────────────────────────────────
# A single keyword can move thousands of parts ('bolt' matches 16,541), so a
# newly-agreed token does NOT go live on its own. It waits for the owner exactly
# like NOA's post drafts. If the owner is away, learning pauses — which is the
# correct failure mode for something that can mis-file 16,000 parts.

async def pending_for_owner(db, limit: int = 15) -> list:
    """Tokens that reached consensus and await the owner's approve/reject."""
    await ensure_table(db)
    rows = (await db.execute(text(f"""
        SELECT token, category, observations, total FROM (
            SELECT token, category, observations, status,
                   SUM(observations) OVER (PARTITION BY token)    AS total,
                   ROW_NUMBER() OVER (PARTITION BY token
                                      ORDER BY observations DESC) AS rn
            FROM category_learned_keywords
        ) v
        WHERE rn = 1 AND status = 'pending'
          AND observations >= {MIN_CONSENSUS}
          AND observations::float / total >= {MIN_AGREEMENT}
        ORDER BY observations DESC
        LIMIT :lim
    """), {"lim": limit})).fetchall()
    out = []
    for tok, cat, obs, tot in rows:
        if is_blocked(tok):
            continue
        out.append({"token": tok, "category": cat,
                    "observations": obs, "agreement": obs / max(tot, 1)})
    return out


async def approve(db, token: str) -> dict:
    """Approve a learned keyword → it goes live in the matcher."""
    await ensure_table(db)
    tok = (token or "").strip().lower()
    row = (await db.execute(text(
        "SELECT category, observations FROM category_learned_keywords "
        "WHERE token = :t ORDER BY observations DESC LIMIT 1"
    ), {"t": tok})).fetchone()
    if not row:
        return {"ok": False, "error": "not_found"}
    if is_blocked(tok):
        return {"ok": False, "error": "blocklisted"}
    await db.execute(text(
        "UPDATE category_learned_keywords SET status='approved', updated_at=NOW() "
        "WHERE token = :t"
    ), {"t": tok})
    await db.commit()
    await load_into_matcher(db)
    return {"ok": True, "token": tok, "category": row[0]}


async def reject(db, token: str) -> dict:
    """Reject a learned keyword → never loaded, never re-proposed."""
    await ensure_table(db)
    tok = (token or "").strip().lower()
    res = await db.execute(text(
        "UPDATE category_learned_keywords SET status='rejected', updated_at=NOW() "
        "WHERE token = :t"
    ), {"t": tok})
    await db.commit()
    _rejected_tokens.add(tok)
    category_map.LEARNED.pop(tok, None)
    category_map._build_flat_rules()
    return {"ok": bool(res.rowcount), "token": tok}


async def load_rejected(db) -> int:
    """Rehydrate the rejected set so rejections survive a restart."""
    await ensure_table(db)
    rows = (await db.execute(text(
        "SELECT DISTINCT token FROM category_learned_keywords WHERE status='rejected'"
    ))).fetchall()
    _rejected_tokens.update(r[0] for r in rows)
    return len(_rejected_tokens)

# ── EMBEDDING ASSIST (local model, no quota) ─────────────────────────────────
# Same contract as the LLM assist, different oracle. The local MiniLM model costs
# nothing per call, so it can propose keywords continuously where the LLM was
# capped at 150 calls/day.
#
# It feeds the SAME vote table and therefore inherits EVERY existing guard:
#   • the never-learn blocklist (brands / fasteners / positions / size codes),
#     enforced at mine, load AND startup-purge;
#   • MIN_CONSENSUS cumulative observations with MIN_AGREEMENT;
#   • the owner approval gate — nothing goes live until `אשרמילה`.
# So a wrong embedding suggestion costs a rejected proposal, never a mis-filed
# part. That distinction is why this path is safe even though the model's raw
# accuracy is uneven (measured 2026-07-28: good on Latin part names, weak on
# Hebrew automotive vocabulary).

EMBED_MIN_SCORE_FOR_LEARNING = float(
    os.getenv("EMBED_LEARN_MIN_SCORE", "0.80"))


async def suggest_from_embeddings(db, texts, limit: int = 400) -> int:
    """
    Ask the local embedding model about stuck part names and record the resulting
    (token, category) votes as PENDING suggestions.

    Returns the number of votes recorded. Never activates anything on its own.
    """
    try:
        import category_embed as ce
    except ImportError:
        return 0
    if not ce.available():
        return 0

    sample = [t for t in texts if t and t.strip()][:limit]
    if not sample:
        return 0

    await ensure_table(db)
    if not _brand_blocklist:
        await load_brand_blocklist(db)

    import category_input_type as cit

    # PER-TYPE GATING. A code at 0.82 and a descriptive name at 0.82 are not
    # equally trustworthy, so a single global threshold is the wrong control.
    # Types that may not propose at all (size/spec, part numbers, supersession
    # placeholders, brand-only) are dropped BEFORE the model runs — supersession
    # rows measured 33% precision and, unlike codes, they do fire.
    typed = [(t, cit.classify_input_type(t, is_blocked)) for t in sample]
    eligible = [(t, ty) for t, ty in typed if cit.may_propose(ty)]
    if not eligible:
        return 0

    preds = []
    texts_only = [t for t, _ in eligible]
    for i in range(0, len(texts_only), 128):
        preds += ce.classify(texts_only[i:i + 128])

    classified = []          # (text, category)
    type_of = {}             # text -> input type, for the evidence stamp
    for (t, ty), (cat, score) in zip(eligible, preds):
        if cat and score >= max(cit.min_score_for(ty), EMBED_MIN_SCORE_FOR_LEARNING):
            classified.append((t, cat))
            type_of[t] = ty
    if not classified:
        return 0

    mined = mine_tokens(classified)          # blocklist + already-known filters
    if not mined:
        return 0

    # Attribute each token to the most common input type among the parts that
    # produced it, so the Phase-2 scorecard can be computed per type.
    import collections as _c
    tok_types: dict = _c.defaultdict(_c.Counter)
    for t, _cat in classified:
        low = t.lower()
        for token, _c2, _o, _a in mined:
            if token in low:
                tok_types[token][type_of[t]] += 1

    for token, cat, obs, _agr in mined:
        ty = (tok_types[token].most_common(1)[0][0]
              if tok_types.get(token) else None)
        await db.execute(text("""
            INSERT INTO category_learned_keywords
                   (token, category, observations, source, status, input_type)
            VALUES (:t, :c, :o, 'embed_assist', 'pending', :ty)
            ON CONFLICT (token, category) DO UPDATE SET
                observations = category_learned_keywords.observations
                               + EXCLUDED.observations,
                input_type   = COALESCE(category_learned_keywords.input_type,
                                        EXCLUDED.input_type),
                updated_at   = NOW()
        """), {"t": token, "c": cat, "o": obs, "ty": ty})
    await db.commit()
    logger.info("embed_assist: %d parts eligible of %d, %d classified, "
                "%d keyword vote(s) recorded (PHASE 1 — awaiting owner approval)",
                len(eligible), len(sample), len(classified), len(mined))
    return len(mined)
