"""
Script: maintenance/train_embed_exemplars.py
Purpose: Make the LOCAL model smarter using our OWN catalogue — replace its
         hand-written keyword exemplars with real, already-categorized part
         names, and MEASURE whether that actually helped on held-out data.
Process:
  1. Sample real part names per category (train split + held-out test split).
  2. Embed the train split once and store it as the model's exemplar bank.
  3. Score the OLD exemplars and the NEW ones on the SAME held-out rows.
  4. Install the new bank ONLY if it measurably beats the old one.
Data Imported/Modified: writes /app/state/models/exemplars_local.npz. Touches no
  catalogue row — training must never write to the data it learns from.
Data Sources: parts_catalog (names + their existing categories).
Missing Data Delegation: categories with too few clean examples keep their
  keyword exemplars rather than being learned from a thin sample.
Last Updated: 2026-08-06

WHY THIS IS "TRAINING ON LOCAL DATA" WITHOUT FINE-TUNING:
  Fine-tuning the transformer would need torch (~800MB image, ~1.5GB RSS) on a
  box with 12GB and NO SWAP — an OOM here is a hard kill, which is why the ONNX
  runtime was chosen in the first place. But almost all of the achievable gain
  here does not need new weights: this model is used as NEAREST-EXEMPLAR, so its
  accuracy is dominated by WHAT IT IS COMPARED AGAINST. Comparing against 160
  keyword phrases is what put `מסנן שמן מנוע` (engine oil FILTER) in fluids at
  0.99 — 'שמן' (oil) is a fluids keyword and no exemplar represented a real
  filter. Comparing against thousands of REAL filter names fixes that with zero
  new parameters and zero GPU.

WHAT IT MUST NOT LEARN FROM:
  Rows we ourselves guessed. Anything stamped `specifications.category_by`
  (learned keyword / llm_catchall) is EXCLUDED — training on our own output
  would launder guesses into ground truth and make the model confidently repeat
  our mistakes. Only categories that came from importers/TecDoc are used.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

import asyncpg  # noqa: E402

import category_embed as ce  # noqa: E402
from category_map import CATCH_ALL  # noqa: E402

DB = os.environ.get("DATABASE_URL", "").replace("postgresql+asyncpg://", "postgresql://")
OUT_PATH = os.path.join(ce.MODEL_DIR, "exemplars_local.npz")

PER_CAT = int(os.getenv("EMBED_TRAIN_PER_CAT", "400"))   # exemplars per category
TEST_PER_CAT = int(os.getenv("EMBED_TEST_PER_CAT", "120"))
MIN_CAT_ROWS = 150            # below this a category keeps its keyword exemplars


async def _sample(conn):
    """Real names per category, EXCLUDING anything we categorized ourselves."""
    rows = await conn.fetch(
        """
        SELECT category, name FROM (
            SELECT pc.category, pc.name,
                   ROW_NUMBER() OVER (PARTITION BY pc.category ORDER BY random()) rn
            FROM parts_catalog pc
            WHERE pc.is_active
              AND pc.category IS NOT NULL
              AND pc.category <> $1
              AND pc.name ~ '[A-Za-zא-ת]{3,}'
              AND length(pc.name) BETWEEN 4 AND 70
              AND pc.specifications->>'category_by' IS NULL
        ) s WHERE rn <= $2
        """,
        CATCH_ALL, PER_CAT + TEST_PER_CAT,
    )
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(" ".join((r["name"] or "").split()))
    return by_cat


def _split(by_cat):
    train, test = defaultdict(list), defaultdict(list)
    for cat, names in by_cat.items():
        uniq = list(dict.fromkeys(names))
        if len(uniq) < MIN_CAT_ROWS:
            print(f"  [skip] {cat:<26} only {len(uniq)} clean rows — keeping keyword exemplars")
            continue
        random.Random(42).shuffle(uniq)
        n_test = min(TEST_PER_CAT, len(uniq) // 4)
        test[cat] = uniq[:n_test]
        train[cat] = uniq[n_test:n_test + PER_CAT]
    return train, test


def _score(test, classify_fn, floor: float):
    """-> (fire_rate, precision) on held-out rows whose TRUE category we know."""
    total = fired = correct = 0
    for cat, names in test.items():
        for i in range(0, len(names), 128):
            chunk = names[i:i + 128]
            for name, (pred, score) in zip(chunk, classify_fn(chunk)):
                total += 1
                if pred and score >= floor:
                    fired += 1
                    correct += (pred == cat)
    return (100 * fired / max(total, 1)), (100 * correct / max(fired, 1)), total


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true",
                    help="write the new exemplar bank if it beats the old one")
    ap.add_argument("--floor", type=float, default=0.75)
    args = ap.parse_args()

    import numpy as np

    conn = await asyncpg.connect(DB, statement_cache_size=0)
    await conn.execute("SET statement_timeout='900s'")
    print("[train] sampling real catalogue names (excluding our own guesses)...")
    by_cat = await _sample(conn)
    await conn.close()
    train, test = _split(by_cat)
    print(f"[train] {len(train)} categories · "
          f"{sum(len(v) for v in train.values()):,} train · "
          f"{sum(len(v) for v in test.values()):,} held-out\n")

    if not ce.build_centroids():
        print("[train] model unavailable — aborting")
        return 1

    # BASELINE first, on the same held-out rows. Without this the "improvement"
    # is an assertion, not a measurement.
    print("[train] scoring the CURRENT (keyword-phrase) exemplars...")
    old_fire, old_prec, n = _score(test, ce.classify, args.floor)
    print(f"        fire {old_fire:.1f}%  precision {old_prec:.1f}%  (n={n:,})\n")

    print("[train] embedding real-name exemplars...")
    labels, texts = [], []
    for cat, names in train.items():
        for nm in names:
            labels.append(cat)
            texts.append(nm)
    mats = [ce.embed(texts[i:i + 256]) for i in range(0, len(texts), 256)]
    vecs = np.vstack(mats)
    print(f"        {vecs.shape[0]:,} exemplars, dim {vecs.shape[1]}\n")

    # Score each candidate WITHOUT installing it: swap in memory only.
    saved_c, saved_l = ce._centroids, ce._centroid_labels

    def score_bank(v, l):
        ce._centroids, ce._centroid_labels = v, l
        try:
            return _score(test, ce.classify, args.floor)
        finally:
            ce._centroids, ce._centroid_labels = saved_c, saved_l

    print("[train] scoring REAL-NAMES-ONLY exemplars...")
    only_fire, only_prec, _ = score_bank(vecs, labels)
    print(f"        fire {only_fire:.1f}%  precision {only_prec:.1f}%\n")

    # COMBINED = curated keyword phrases + real names (owner's call, 2026-08-06).
    # The two banks are complementary rather than rival: the phrases are clean,
    # deliberate and cover categories with too little clean data to learn from,
    # while real names carry the vocabulary customers and suppliers actually use.
    # Keeping both means adding coverage cannot silently DROP a curated sense.
    print("[train] scoring COMBINED (keyword phrases + real names)...")
    comb_vecs = np.vstack([saved_c, vecs])
    comb_labels = list(saved_l) + labels
    comb_fire, comb_prec, _ = score_bank(comb_vecs, comb_labels)
    print(f"        fire {comb_fire:.1f}%  precision {comb_prec:.1f}%\n")

    print("=" * 68)
    print(f"  {'bank':<28}{'fire':>10}{'precision':>12}")
    print(f"  {'keyword phrases (current)':<28}{old_fire:>9.1f}%{old_prec:>11.1f}%")
    print(f"  {'real names only':<28}{only_fire:>9.1f}%{only_prec:>11.1f}%")
    print(f"  {'COMBINED':<28}{comb_fire:>9.1f}%{comb_prec:>11.1f}%")
    print("=" * 68)

    # Pick by measurement, not by preference.
    cands = [("combined", comb_prec, comb_vecs, comb_labels),
             ("real-names-only", only_prec, vecs, labels)]
    name, prec, best_v, best_l = max(cands, key=lambda c: c[1])

    if not args.install:
        print(f"\n  best = {name} ({prec:.1f}%)   (measurement only — "
              f"pass --install to write it)")
        return 0
    if prec <= old_prec + 1.0:
        print("\n  NOT installing: no candidate beats the current bank by >1pt. "
              "A model that measures worse must not ship because it was expensive.")
        return 0

    np.savez_compressed(OUT_PATH, vecs=best_v, labels=np.array(best_l))
    print(f"\n  installed {name} bank ({len(best_l):,} exemplars) -> {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
