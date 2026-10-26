"""vehicle_model_match — the one rule for "does this fitment model fit the customer's model?"
(FIXES_TRACKER #69 §9). Registry names below are real vehicle_market_il.kinuy_mishari values
(normalised), copied 2026-10-19.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vehicle_model_match as vm  # noqa: E402

TOYOTA = {vm.norm(x) for x in (
    "corolla", "corolla cross", "corolla hb", "corolla hb hsd", "corolla hb hv", "corolla hev",
    "corolla hsd hb", "corolla hsd sdn", "corolla hybrid", "corolla hybryd", "corolla runx",
    "corolla sdn", "corolla sdn hsd", "corolla sdn hv", "corolla sedan", "corolla sw",
    "corolla terra", "corolla touring", "corolla ts", "corolla ts hsd", "corolla ts hv",
    "corolla verso", "land cruiser", "prado", "rav 4", "rav4", "rav4 hybrid")}
LAND_ROVER = {vm.norm(x) for x in (       # "Land Rover" + "Rover" rows of the registry
    "range rover", "range rover spo", "range rover vel", "range rover evo", "evoque",
    "discovery", "discovery sport", "defender", "defender 110", "defender 90")}
SKODA = {"octavia", "octavia fl", "octavia iv", "octavia rs", "octavia s", "octavia space"}
HONDA = {"civic", "civic 4d", "civic 5d", "civic 5d 1.5", "civic coupe", "civic hybrid",
         "civic si", "civic tourer", "civic type r"}
KIA = {"sportage", "sportage hev", "sportage phev"}

fits = vm.fitment_model_fits


# ── the three reported defects ─────────────────────────────────────────────────
def test_corolla_does_not_get_corolla_cross_or_verso():
    for f in ("corolla cross", "corolla cross g10", "corolla verso zde12 cde12"):
        assert not fits(f, "corolla", TOYOTA), f


LR_ROWS = {"range rover": 2674, "range rover evoque": 3773, "range rover sport": 4734,
           "range rover velar": 2836}


def test_evoque_does_not_get_full_size_range_rover_and_vice_versa():
    # Evoque is a registered model WITH its own catalogue series (3,773 rows)
    assert not fits("range rover", "range rover evoque", LAND_ROVER, LR_ROWS)
    for f in ("range rover evoque", "range rover sport", "range rover velar"):
        assert not fits(f, "range rover", LAND_ROVER), f


def test_prado_never_gets_the_junk_model_land():
    assert not fits("land", "land cruiser prado", TOYOTA)
    assert not fits("land", "land cruiser", TOYOTA)


# ── valid matches stay ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("f,c,reg", [
    ("corolla", "corolla", TOYOTA),
    ("corolla e11", "corolla", TOYOTA),                  # generation code
    ("corolla saloon e21", "corolla", TOYOTA),
    ("corolla", "corolla e11", TOYOTA),                  # plate slug → generic model
    ("corolla e11", "corolla e11", TOYOTA),
    ("corolla hsd sdn", "corolla", TOYOTA),              # powertrain/body variant (unchanged)
    ("corolla ts hv", "corolla", TOYOTA),
    ("rav4 hybrid", "rav4", TOYOTA),
    ("range rover evoque", "range rover evoque", LAND_ROVER),
    ("range rover", "range rover vogue", LAND_ROVER),    # Vogue = full-size trim, not registered
    ("range rover l322", "range rover", LAND_ROVER),
    ("octavia iv nx3", "octavia", SKODA),                # generation numeral
    ("octavia iv combi nx5", "octavia", SKODA),
    ("civic 5d", "civic", HONDA), ("civic 4d", "civic", HONDA), ("civic tourer", "civic", HONDA),
    ("sportage hev", "sportage", KIA), ("sportage", "sportage hev", KIA),
    ("sportage ql", "sportage", KIA),
])
def test_valid_and_unchanged_matches_remain(f, c, reg):
    assert fits(f, c, reg)


def test_unrelated_models_never_match():
    assert not fits("yaris", "corolla", TOYOTA)
    assert not fits("corollax", "corolla", TOYOTA)       # whole words only
    assert not fits("cx 30", "3", set())


def test_make_without_registry_keeps_the_previous_whole_word_prefix_behaviour():
    # No registry names = no evidence: nothing is excluded (the earlier "drop a bare first word"
    # heuristic was the same mistake that dropped Chevrolet "silverado").
    assert fits("model x 2015", "model x", set())
    assert fits("model x", "model x p100d", set())
    assert fits("model", "model x p100d", set())


def test_registry_lookup_also_reads_the_shortened_maker_name():
    assert vm._registry_makes(["Land Rover"]) == ["land rover", "rover"]
    assert vm._registry_makes(["Toyota"]) == ["toyota"]


# ── against the live catalogue (read-only) ────────────────────────────────────
async def _allowed_many(cases):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool
    from BACKEND_DATABASE_MODELS import DATABASE_URL
    engine = create_async_engine(DATABASE_URL, poolclass=NullPool)   # own loop-local engine
    try:
        async with sessionmaker(engine, class_=AsyncSession)() as db:
            vm._cache.clear()
            return [set(await vm.allowed_fitment_models(db, [mk], [md])) for mk, md in cases]
    finally:
        await engine.dispose()


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="needs the catalogue database")
def test_live_reported_cases_and_valid_generation_codes():
    corolla, evoque, prado, sportage = asyncio.run(_allowed_many([
        ("toyota", "corolla"), ("land rover", "range rover evoque"),
        ("toyota", "land cruiser prado"), ("kia", "sportage")]))
    if not corolla:
        pytest.skip("connected database has no Toyota Corolla fitment rows (isolated/empty database)")
    assert "corolla e11" in corolla and "corolla" in corolla
    assert not {m for m in corolla if m.startswith("corolla cross")}
    assert "range rover" not in evoque and "range rover evoque" in evoque
    assert "land" not in prado and "land cruiser prado" in prado
    assert {"sportage", "sportage hev"} <= sportage


# ── reported wrong drops of the first candidate (impact run 2026-10-19) ─────────
CHEVROLET = {"spark", "spark ls", "spark l.s", "malibu", "captiva", "captiva sport"}   # no Silverado
HONDA_ACCORD = {"accord", "accord diesel", "accord hybrid", "accord i", "accord tourer"}
KIA_NIRO = {"niro", "niro electric", "niro ev", "niro hev", "niro hybrid", "niro phev",
            "niro plus", "niro plus ev", "niro plus phev"}
JAGUAR = {"xk8", "xk8 coupe", "xk coupe", "xkr", "xkr cabriol 4.2", "xkr coupe 4.2"}
VOLVO = {"s60", "s60 2.0", "s60 b4", "s60 b5", "s60 d3", "s60 t4", "s60 t5", "s60 t6", "s60 t8",
         "s60 t8 awd", "xc60", "xc60 t5"}
MITSUBISHI = {"lancer", "lancer evolutio", "lancer sportbac"}
TOYOTA_HILUX = {"hilux", "hilux vigo", "hilux vigo s"}


@pytest.mark.parametrize("f,c,reg,rows", [
    ("silverado", "silverado 1500 crew cab pickup", CHEVROLET, {"silverado": 12526}),   # no registry evidence
    ("silverado", "silverado 2500 hd", CHEVROLET, {"silverado": 12526}),
    ("accord", "accord i sj sy", HONDA_ACCORD, {"accord": 44207}),                       # "i" = 1st generation
    ("xk", "xk 8 coupe qev", JAGUAR, {"xk": 20168}),                                     # "xk" is not junk
    ("xk", "xk convertible j43", JAGUAR, {"xk": 20168}),
    ("s60", "s60 t5", VOLVO, {"s60": 32832}),                                            # engine code
    ("s60 t5", "s60", VOLVO, {}), ("xc60 t5", "xc60", VOLVO, {}),
    ("lancer", "lancer sportback cx a", MITSUBISHI, {"lancer": 6109}),                   # truncated "sportbac"
    ("civic", "civic type r", HONDA, {"civic": 45138, "civic type r": 24}),              # trim, no own series
    ("spark", "spark ls", CHEVROLET, {"spark": 31037}),
])
def test_valid_generic_matches_that_the_first_candidate_wrongly_dropped(f, c, reg, rows):
    ok, why = vm.explain(f, c, reg, rows)
    assert ok, why


def test_a_registered_sub_series_with_its_own_catalogue_series_drops_the_parent_rows():
    # OWNER DECISION class (FIXES_TRACKER #69 §9) — current default, made visible by explain()
    ok, why = vm.explain("niro", "niro plus", KIA_NIRO, {"niro": 21728, "niro plus": 1335})
    assert not ok and why.startswith("customer_is_distinct_model_with_own_series:niro plus")
    ok, why = vm.explain("hilux", "hilux vigo", TOYOTA_HILUX, {"hilux": 40899, "hilux vigo": 60})
    assert not ok and "hilux vigo" in why
    ok, why = vm.explain("hilux", "hilux vigo", TOYOTA_HILUX, {"hilux": 40899, "hilux vigo": 3})
    assert ok and why.startswith("generic_kept_customer_registered_trim")


def test_explain_reasons_for_the_reported_defects():
    assert vm.explain("corolla cross g10", "corolla", TOYOTA)[1] == "fitment_is_distinct_model:corolla cross"
    assert vm.explain("land", "land cruiser prado", TOYOTA)[1] == "generic_is_junk_prefix"
    assert vm.explain("corolla e11", "corolla", TOYOTA)[1] == "fitment_version_of_customer_model"
    assert vm.explain("range rover", "range rover evoque", LAND_ROVER, LR_ROWS)[1].startswith(
        "customer_is_distinct_model_with_own_series:range rover evo")


# ── owner decisions D1-D5 (2026-10-19) ────────────────────────────────────────
OPEL_YEARS = {"corsa b 73 78": 1993, "corsa b box": 1994, "corsa c f08 f68": 2000, "corsa d": 2006,
              "corsa": 1982}
OPEL = {"corsa", "corsa b", "corsa c", "corsa d", "astra", "astra g", "astra h"}


def test_d1_generation_letters_need_two_alphabetical_chronological_series():
    assert vm.generation_letters(OPEL_YEARS, "corsa") == {"b", "c", "d"}
    assert vm.generation_letters({"prius c nhp10": 2011}, "prius") == set()          # lone letter
    assert vm.generation_letters({"astra h": 2004, "astra g": 2006}, "astra") == set()  # not chronological


def test_d1_generation_letter_rows_match_both_ways_and_unverified_letters_do_not():
    gen = {"corsa": {"b", "c", "d"}}
    assert vm.explain("corsa c f08 f68", "corsa", OPEL, {}, ["opel"], gen)[0]
    assert vm.explain("corsa", "corsa c f08 f68", OPEL, {"corsa c f08 f68": 5000}, ["opel"], gen)[0]
    assert not vm.explain("corsa c f08 f68", "corsa", OPEL, {}, ["opel"], {})[0]      # no evidence: unchanged


def test_d4_the_make_name_is_never_a_junk_prefix():
    mini = {"mini cooper", "mini one", "mini countryman"}
    assert vm.explain("mini", "mini 3 door", mini, {}, ["mini"])[0]
    assert not vm.explain("mini", "mini 3 door", mini, {}, [])[0]                    # old behaviour


def test_d5_prado_does_not_get_unvalidated_generic_land_cruiser_rows():
    ok, why = vm.explain("land cruiser", "land cruiser prado", TOYOTA,
                         {"land cruiser": 40957, "land cruiser prado": 310}, ["toyota"])
    assert not ok and why.startswith("customer_is_distinct_model_with_own_series:land cruiser prado")
    assert vm.explain("land cruiser prado", "land cruiser prado", TOYOTA, {}, ["toyota"])[0]
    assert vm.explain("land cruiser 100 uzj100", "land cruiser", TOYOTA, {}, ["toyota"])[0]
