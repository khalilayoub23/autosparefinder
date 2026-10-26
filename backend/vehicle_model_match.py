"""
Script: vehicle_model_match.py
Purpose: The ONE rule for "does a fitment row's model fit the customer's model?" — used by
         search (routes/parts.py), the chat agents (BACKEND_AI_AGENTS.search_parts_in_db) and
         the partner API (routes/public_api.py). FIXES_TRACKER #69 §9.

Root cause it replaces: every caller matched by word PREFIX in both directions
  (customer "corolla" ↔ fitment "corolla cross g10"; customer "range rover evoque" ↔ fitment
  "range rover"; customer "land cruiser prado" → bare prefix "land"), and the JSON lane by
  substring ("%corolla%"). A shared prefix is not the same vehicle.

Rules (a fitment model f is offered for customer model c of make m):
  1. f == c                                  → match.
  2. f extends c ("corolla e21")             → match, UNLESS f lies inside a registered model
     name r of make m that itself extends c with a non-variant word ("corolla cross").
  3. c extends f (fitment more general)       → match only if f is itself a registered model
     name of m (drops junk like "land"), and c does not lie inside a registered model r that
     extends f with a non-variant word (Evoque is not the full-size Range Rover).
  3a. A specific customer model drops the generic rows only when it is a registered model of its
      own AND has its own fitment series (>= OWN_SERIES_MIN_ROWS rows); a registered trim with
      no own series keeps the generic rows.
  "Registered model names" = the government registry vehicle_market_il.kinuy_mishari for the
  make (authoritative commercial names). The registry truncates names at 15 characters
  ("range rover evo"), so a 15-character name is compared as a character prefix.
  Variant words (powertrain / body: hev, hybrid, hsd, sdn, …) never make a different model —
  matching for those is UNCHANGED pending an owner decision (see the tracker).
  A make with no registry names (not sold in Israel) gives no evidence either way: matching
  falls back to the previous whole-word prefix behaviour.

Data Imported/Modified: NONE (read-only).
Data Sources: part_vehicle_fitment (candidate models), vehicle_market_il (registered names).
Missing Data Delegation: n/a.
Last Updated: 2026-10-19
"""
from __future__ import annotations

import os
import re
import time
from contextlib import asynccontextmanager
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlalchemy import text

# Words that describe a powertrain, body, generation or engine version of the SAME model. They
# never make a different model. (Trim / sub-series words — "type r", "plus", "vigo", "carrera" —
# are NOT here: they are an owner decision, see FIXES_TRACKER #69 §9.)
VARIANT_WORDS: frozenset = frozenset({
    "hybrid", "hev", "phev", "mhev", "hsd", "hv", "hybryd", "plug", "in", "plugin", "ev",
    "electric", "e", "diesel", "petrol", "turbo", "auto", "manual",
    "sdn", "sedan", "saloon", "hb", "hatch", "hatchback", "liftback", "fastback", "ts", "sw",
    "estate", "wagon", "combi", "kombi", "touring", "tourer", "sportswagon", "sportback", "coupe",
    "cabrio", "cabriolet", "convertible", "roadster", "van", "pickup", "pick", "up", "cab", "crew",
    "double", "single", "lwb", "swb", "door", "doors", "dr", "4x4", "awd", "4wd", "2wd",
    # generation numerals and facelift — the same model in another generation
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "fl", "facelift",
})
REGISTRY_TRUNCATION = 15
# A specific model that has at least this many fitment rows of its OWN is a separate catalogue
# series (TecDoc lists "range rover evoque" apart from "range rover"); generic rows of the parent
# then describe the parent line, not the customer's car.
OWN_SERIES_MIN_ROWS = 50
_CACHE_TTL_S = 600.0
_CACHE_MAX = 2000


def norm(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_/]", " ", str(s or "").lower())).strip()


def _word_extends(longer: str, shorter: str) -> bool:
    return bool(shorter) and longer.startswith(shorter + " ")


def _is_variant_word(w: str, truncated: bool = False) -> bool:
    if w in VARIANT_WORDS:
        return True
    # a version code: 2+ characters with a digit ("t5", "b4", "1500", "2.0", "e21", "p300e", "5d").
    # A lone digit stays significant — "mazda 2" and "mazda 3" are different models.
    if len(w) >= 2 and any(ch.isdigit() for ch in w):
        return True
    # the registry cuts names at 15 characters: "lancer sportbac" is "sportback". A short stub is
    # ambiguous — "range rover spo" is Range Rover SPORT, not a "sportback" body — so >= 5 chars.
    return truncated and len(w) >= 5 and any(v.startswith(w) for v in VARIANT_WORDS)


def _is_variant_extension(name: str, base: str) -> bool:
    ext = name[len(base):].split()
    truncated = len(name) >= REGISTRY_TRUNCATION
    return bool(ext) and all(_is_variant_word(w, truncated and i == len(ext) - 1)
                             for i, w in enumerate(ext))


def _inside(name: str, registered: str) -> bool:
    """name is (or lies inside) the registered model name."""
    if len(registered) >= REGISTRY_TRUNCATION:
        return name.startswith(registered)
    return name == registered or _word_extends(name, registered)


def _distinct_extensions(base: str, registered: Iterable[str]) -> List[str]:
    """Registered names that are a DIFFERENT model more specific than base."""
    return [r for r in registered
            if _word_extends(r, base) and not _is_variant_extension(r, base)]


def _is_junk_prefix(f: str, registered: Set[str], makes: Iterable[str] = ()) -> bool:
    """A bare word that the registry only ever uses as the start of OTHER model names
    ("land" → only "land cruiser"). Needs evidence: without registry names starting with it,
    nothing can be concluded (the registry has no Chevrolet Silverado at all). The make's own
    name is never junk — generic "mini" rows for MINI (owner decision D4)."""
    if f in registered:
        return False
    if any(f == w for m in makes for w in norm(m).split()):
        return False
    continuations = [r for r in registered if _word_extends(r, f)]
    return bool(continuations) and all(not _is_variant_extension(r, f) for r in continuations)


def generation_letters(series_first_year: Dict[str, int], base: str) -> Set[str]:
    """Single-letter series of `base` that are GENERATIONS (owner decision D1): at least two
    letters whose first fitment years rise in alphabetical order (Corsa B 1993 < C 2000 < D 2006).
    A lone letter (Prius C, X5 M, Cooper S) is never inferred to be a generation."""
    first: Dict[str, int] = {}
    for model, year in series_first_year.items():
        m = norm(model)
        if not _word_extends(m, base) or not year:
            continue
        w = m[len(base):].split()[0]
        if len(w) == 1 and w.isalpha():
            first[w] = min(first.get(w, year), year)
    if len(first) < 2:
        return set()
    ordered = sorted(first)
    years = [first[w] for w in ordered]
    return set(ordered) if all(a < b for a, b in zip(years, years[1:])) else set()


def explain(fitment_model: str, customer_model: str, registered: Set[str],
            series_rows: Optional[Dict[str, int]] = None, makes: Iterable[str] = (),
            gen_letters: Optional[Dict[str, Set[str]]] = None) -> Tuple[bool, str]:
    """(fits, reason). series_rows: fitment-row count per normalised fitment model;
    gen_letters: verified generation letters per base model (see generation_letters)."""
    f, c = norm(fitment_model), norm(customer_model)
    gen = gen_letters or {}
    if not f or not c:
        return False, "empty"
    if f == c:
        return True, "exact"
    if _word_extends(f, c):                                   # fitment is more specific
        first = f[len(c):].split()[0]
        if first in gen.get(c, set()):
            return True, f"fitment_generation_letter:{first}"     # D1: year filter separates
        for r in _distinct_extensions(c, registered):
            if _inside(f, r) and not _inside(c, r):
                return False, f"fitment_is_distinct_model:{r}"
        return True, "fitment_version_of_customer_model"
    if _word_extends(c, f):                                   # fitment is more general
        if len(c.split()) > 1 and len(f.split()) == 1 and _is_junk_prefix(f, registered, makes):
            return False, "generic_is_junk_prefix"
        first = c[len(f):].split()[0]
        if first in gen.get(f, set()):
            return True, f"generic_of_customer_generation:{first}"  # D1
        rows = series_rows or {}
        candidates = list(_distinct_extensions(f, registered))
        # D5: the extension is itself a registered model ("prado" for "land cruiser prado")
        if first in registered and not _is_variant_word(first):
            candidates.append(f"{f} {first}")
        for r in candidates:
            if _inside(c, r):
                own = sum(n for m, n in rows.items() if _inside(m, r))
                if own >= OWN_SERIES_MIN_ROWS:
                    return False, f"customer_is_distinct_model_with_own_series:{r}({own})"
                return True, f"generic_kept_customer_registered_trim:{r}({own})"
        return True, "generic_of_customer_model"
    return False, "unrelated"


def fitment_model_fits(fitment_model: str, customer_model: str, registered: Set[str],
                       series_rows: Optional[Dict[str, int]] = None, makes: Iterable[str] = (),
                       gen_letters: Optional[Dict[str, Set[str]]] = None) -> bool:
    return explain(fitment_model, customer_model, registered, series_rows, makes, gen_letters)[0]


def candidate_terms(customer_models: Sequence[str]) -> Tuple[List[str], List[str]]:
    """The superset the DB is asked for: each model and its word prefixes (equality) plus
    "<model> %" (LIKE). Same superset the matching used before; the rule then filters it."""
    terms: Set[str] = set()
    fwd: List[str] = []
    for m in customer_models:
        v = str(m or "").strip().lower()
        if not v:
            continue
        fwd.append(v)
        words = v.split()
        for i in range(1, len(words) + 1):
            terms.add(" ".join(words[:i]))
    return sorted(terms), fwd


_cache: Dict[tuple, Tuple[float, List[str]]] = {}


def _registry_makes(makes: Sequence[str]) -> List[str]:
    """The registry files some vehicles under a shortened maker name — Range Rover Evoque,
    Discovery and Defender appear under "Rover" as well as "Land Rover" — so a multi-word make
    also looks up its last word."""
    out: Set[str] = set()
    for m in makes:
        m = (m or "").strip().lower()
        if m:
            out.add(m)
            if " " in m:
                out.add(m.split()[-1])
    return sorted(out)


async def _registered_names(db, makes: Sequence[str]) -> Set[str]:
    rows = (await db.execute(text(
        "SELECT DISTINCT lower(trim(kinuy_mishari)) FROM vehicle_market_il "
        "WHERE lower(trim(manufacturer)) = ANY(:makes) AND kinuy_mishari IS NOT NULL"),
        {"makes": _registry_makes(makes)})).scalars().all()
    return {norm(r) for r in rows if r and norm(r)}


async def _generation_letters_for(db, mfr_sql: List[str], params: Dict, customer_models: Sequence[str],
                                  candidates: Sequence[str]) -> Dict[str, Set[str]]:
    """Verified generation letters for every base that could matter: the customer models and
    every candidate the customer model extends. One query for the single-letter siblings."""
    bases = {norm(c) for c in customer_models}
    for cand in candidates:
        for cm in customer_models:
            if _word_extends(norm(cm), norm(cand)):
                bases.add(norm(cand))
    bases = {b for b in bases if b}
    if not bases:
        return {}
    pats = {f"gb{i}": b for i, b in enumerate(sorted(bases))}
    like = " OR ".join(f"LOWER(TRIM(model)) ~ ('^' || :{k} || ' [a-z]( |$)')" for k in pats)
    rows = (await db.execute(text(
        "SELECT LOWER(TRIM(model)), min(year_from) FROM part_vehicle_fitment "
        f"WHERE ({' OR '.join(mfr_sql)}) AND ({like}) GROUP BY 1"),
        {**{k: v for k, v in params.items() if k.startswith("mk")}, **{k: re.escape(v) for k, v in pats.items()}})).all()
    first_year = {m: int(y) for m, y in rows if m and y}
    return {b: generation_letters(first_year, b) for b in bases}


# Per-statement limit for the fitment lookups a customer search waits on. The engine default is
# 15 min (BACKEND_DATABASE_MODELS); only the result buckets had a lower one (#69 §16).
STATEMENT_LIMIT_MS = int(os.getenv("SEARCH_STATEMENT_TIMEOUT_MS", "30000"))


@asynccontextmanager
async def bounded(db, limit_ms: int):
    """Run the statements inside under `limit_ms` on the CALLER's session without leaking the
    setting: a savepoint + set_config(..., is_local) and the previous value restored after. On an
    error the savepoint rolls back, which also reverts the setting, and the error propagates."""
    async with db.begin_nested():
        prev = (await db.execute(text("SELECT current_setting('statement_timeout')"))).scalar()
        await db.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": f"{int(limit_ms)}ms"})
        yield
        await db.execute(text("SELECT set_config('statement_timeout', :v, true)"), {"v": prev})


async def allowed_fitment_models(db, makes: Sequence[str], customer_models: Sequence[str]) -> List[str]:
    """Exact lower(trim(part_vehicle_fitment.model)) values that fit the customer's model(s),
    for SQL `LOWER(TRIM(pvf.model)) = ANY(:allowed)`. Read-only; cached 10 min."""
    makes_l = tuple(sorted({m.strip().lower() for m in makes if m and m.strip()}))
    models_l = tuple(sorted({str(m).strip().lower() for m in customer_models if m and str(m).strip()}))
    if not makes_l or not models_l:
        return []
    key = (makes_l, models_l)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _CACHE_TTL_S:
        return hit[1]

    terms, fwd = candidate_terms(models_l)
    params = {"terms": terms}
    mfr_sql = []
    for i, mk in enumerate(makes_l):
        params[f"mk{i}"] = mk
        mfr_sql.append(f"(LOWER(TRIM(manufacturer)) = :mk{i} OR LOWER(TRIM(manufacturer)) LIKE '%' || :mk{i} || '%')")
    model_sql = ["LOWER(TRIM(model)) = ANY(:terms)"]
    for i, v in enumerate(fwd):
        params[f"fw{i}"] = v
        model_sql.append(f"LOWER(TRIM(model)) LIKE :fw{i} || ' %'")
    async with bounded(db, STATEMENT_LIMIT_MS):          # a search must not wait on this for minutes
        counted = (await db.execute(text(
            "SELECT LOWER(TRIM(model)), count(*) FROM part_vehicle_fitment "
            f"WHERE ({' OR '.join(mfr_sql)}) AND ({' OR '.join(model_sql)}) GROUP BY 1"), params)).all()
        series_rows = {norm(m): int(n) for m, n in counted if m}
        registered = await _registered_names(db, makes_l)
        gen = await _generation_letters_for(db, mfr_sql, params, models_l, [m for m, _ in counted if m])
    allowed = sorted({cand for cand, _ in counted if cand and any(
        fitment_model_fits(cand, cm, registered, series_rows, makes_l, gen) for cm in models_l)})

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.monotonic(), allowed)
    return allowed
