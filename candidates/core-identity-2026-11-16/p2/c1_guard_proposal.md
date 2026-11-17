# C1: ICE-only and misattributed fitment on pure-BEV models (Consultant, 2026-11-17, read-only, nothing applied)

## Deliverables (this directory)
| File | Content |
|---|---|
| c1_rows.json | `meta` (51 verified BEV models with reasons, the excluded hybrid/PHEV models, rule sets, counts), `tiers.ICE_ON_BEV` (8,524), `tiers.MISATTRIBUTED` (2,469), and `excluded` (24,000, each with a reason). Each row has fid, pid, sku, name, model, name_cat, slug, vehicle_slug, scope and evidence. |
| c1_backup_pvf.json | The full part_vehicle_fitment rows for all 10,993 manifest fids. This is the rollback source and has the same shape as cleanup_bev/backup_pvf.json. |
| c1_compat.json | The parts_catalog.compatible_vehicles plan `{pid: {old, new, removed}}`: 1,719 parts, 1,966 elements. Same shape as cleanup_bev/backup_compat.json. |
| c1_compat_only_elements.json | One compat element on a BEV model that has no pvf row (FACET lambda sensor on Audi Etron). It is not in the plan; it is listed here for the owner. |
| c1_bev_models.json | Proposed backend/data/bev_models.json: 119 vehicle_slugs (51 in C1 scope plus 68 extended) and a `not_bev` list. |

## Counts
- **Candidates:** every pvf row on the 51 verified BEV (manufacturer, model) pairs, 34,993 rows. Of these, 11,226 are XPeng IL-importer OEM rows with Hebrew names. They are out of scope and left untouched.
- **ICE_ON_BEV: 8,524.**
  - 7,229 are on C1's 44 models. The other 1,295 are on the 7 remaining BEV pairs of the earlier manifest: A6 e-tron x2, eDeliver 9 Van and Enyaq x4.
  - 3,533 of the 8,524 were created on 2026-11-15/16.
  - C1's 1,777 reproduces exactly (the narrow regex on the 44 models), and all 1,777 rows are in this tier.
  - The rest come from the brief's other ICE-only classes: timing chain/belt, camshaft/crankshaft, cylinder head, valve stem seal, piston, clutch/flywheel, exhaust/catalyst/lambda, turbo, engine starter/alternator, fuel pump and engine air filter.
- **MISATTRIBUTED: 2,469** (2,264 on the C1-44 models). A row is in this tier only when all of these hold:
  - the product is not ICE-only;
  - the part title names a different vehicle (make + model), and that vehicle is not an alias or a platform/body sibling;
  - the product is not a universal or multi-fit class;
  - the model's own stored listing never names the model for that SKU. For 1,092 rows the SKU appears only under a make-level or block title. The other 1,377 rows are not in the stored payload at all.
- Every row is in exactly one tier. When a row qualifies for both, ICE_ON_BEV wins.

## Exclusion reasons (24,000)
| Reason | Rows |
|---|---|
| LEGIT_LISTING: the model's own listing names the model | 7,651 |
| NOT_PROVEN: the part name is generic or block-style and names no vehicle | 3,782 |
| SAME_OR_SIBLING (see below) | 842 |
| Universal product: bulbs, fluids, wiper blades, battery, caliper hardware, TPMS, fasteners | 342 |
| Title vehicle cannot be parsed | 157 |
| Out of scope (XPeng IL-importer rows) | 11,226 |

SAME_OR_SIBLING examples: Q8 e-tron on Etron, Elroq on Enyaq, Niro HEV on Niro EV, ZS on ZS EV, MG5 SW EV on EP, and siblings on the MLB-evo, PPE, PPC, CLAR, MEB, CMA, MFA2 and e-TNGA platforms.

## Pure-BEV verification
- All 51 pairs are listed in c1_rows.json `meta.bev_models`, each with its reason. Where the slug came from il_market, the IL-registry evidence is taken from harvest_queue.
- **Excluded as not pure BEV:**
  - i3, because of the REx version
  - Niro Plus, which is ambiguous
  - Seal U, Seal 5 and Sealion 5 (DM-i)
  - Tang, Tang II and Han (DM)
  - Polestar 1 (PHEV)
  - Q3 Sportback e-tron (PHEV)
  - XC40 Recharge T5 (PHEV)
  - MG5, because the name covers both ICE and EV models
  - Chery FX EV, which is unverified
- **Kia "Niro Plus Ev" is included.** The IL registry has three separate rows: NIRO PLUS EV (1,747), NIRO PLUS PHEV (9,266) and NIRO PLUS (ambiguous). Only the EV row is in scope.
- **XPeng G6/G9** are BEV as registered in IL. That is ASSUMED for any later range-extender version; none is in the registry row.

## Applying (owner decision, not done)
The combined plan is split into four apply bundles, so each tier and each scope can be approved separately. Each bundle is a directory containing `manifest.json`, `backup_pvf.json`, `backup_compat.json` and its own `SHA256SUMS`, and is consumable as is by `backend/maintenance/cpie_fitment_cleanup_20261117.py --apply --out <dir> --expect-manifest-sha256 .. --expect-backup-sha256 ..`.

| Order | Bundle | pvf rows | compat parts / elements |
|---|---|---|---|
| 1 | c1_apply_1_ice_c1_44 | 7,229 | 1,331 / 1,333 |
| 2 | c1_apply_2_ice_extra7 | 1,295 | 197 / 367 |
| 3 | c1_apply_3_mis_c1_44 | 2,264 | 170 / 171 |
| 4 | c1_apply_4_mis_extra7 | 205 | 51 / 95 |

- **Order matters for compat.** The compat plans are chained in the order above. 30 parts are touched by more than one bundle, and a later bundle's `old` assumes the earlier bundles were applied. If a bundle is skipped or applied out of order, the overlapping parts fail the optimistic check. That failure is closed and safe; re-plan those parts.
- The four bundles together equal c1_backup_pvf.json + c1_compat.json (10,993 rows, 1,966 elements; asserted).
- Do NOT re-run the script's `--plan` for C1. Its evidence text is hard-coded for the small-payload block case, so it would be false here.
- The compat plan was built from live data on 2026-11-17 around 18:55 IL. If a part changed since then, the optimistic check fails closed: re-plan rather than force.

## Deploy order (precondition for the cleanup to hold)
1. Create backend/data/bev_models.json (= c1_bev_models.json) first. The proposed loader fails loud, so the import subprocess would crash without it.
2. Deploy the guard change and the importer call site.
3. Only then apply the cleanup bundles.

If the cleanup runs before the guard, the next 14-day refresh recreates 7,165 ICE rows and 1,092 MISATTRIBUTED rows.

`compatible_vehicles` re-assertion is covered by the same gate (VERIFIED in the importer source):
- When `_fit_ok` is False, `compatible_vehicles` is cleared before the parts_catalog upsert (car_parts_ie_import_generic.py, around line 307), so neither the compat element nor the pvf INSERT loop runs.
- The ON CONFLICT path is `COALESCE(parts_catalog.compatible_vehicles, EXCLUDED...)`, so it never overwrites a stripped, non-null list.

## Sanity checks (VERIFIED, read-only)
- **Foreign keys and triggers:**
  - part_vehicle_fitment has no inbound foreign keys. Its own FKs point to parts_catalog (CASCADE from parts) and car_brands.
  - DELETE fires no trigger: trg_sync_part_vehicle_fitment_mfr runs only BEFORE INSERT OR UPDATE.
  - Updating compatible_vehicles in parts_catalog fires trg_sync_parts_catalog_mfr. That trigger only fills manufacturer_id/manufacturer when they are empty, so it is harmless.
  - The Meili triggers fire only on DELETE or on is_active going from true to false. Neither happens here.
- **Parts stay searchable:**
  - 5,940 parts are affected: 5,809 active and 131 already inactive. Nothing changes is_active.
  - 1,001 parts are left with zero fitment rows. They stay active and stay in the Meili index. The index carries no fitment or compat fields, so text search is unchanged. They only stop appearing in vehicle-filtered results for these BEVs, which is the intent.
- **Compat strip is safe:**
  - No part keeps a pvf row on the same (manufacturer, model) as a deleted row (0 cases). So stripping compat by (manufacturer, model) never removes the evidence for a row that is kept.
  - The strip does not bump updated_at. Meili does not index compatible_vehicles, so there is no stale-search gap.

## Guard (proposal; nothing written to backend/)
- **Rule A:** on a BEV vehicle, an ICE-only product never creates fitment.
- **Rule B:** on a BEV vehicle, two kinds of title create no fitment because they carry no vehicle evidence:
  - a make-level fallback title, such as 'AUDI oil filter RIDEX 7O0002' or 'Car brake pads ...'
  - a popular-parts block title, 'Name (BRAND, SKU)'
- Both rules run only when vehicle_slug (or the parsed manufacturer+model) is in bev_models.json. By construction, non-BEV vehicles cannot be affected.

Measured by running the snippet below against all 6,497 stored payloads (/tmp/*_cpie.json, 2,535,524 items):

| Payload group | Files | Items | Effect |
|---|---|---|---|
| Non-BEV vehicles | 6,378 | 2,490,313 | 0 newly suppressed, 0 newly allowed |
| Hybrid/PHEV slugs (seal-u, niro-plus, i3-i01, tang, han, polestar-1-p, xc40-recharget5, ...) | (included in non-BEV) | | 0 suppressed; they are not in the list |
| BEV vehicles | 119 | 45,211 | 21,128 newly suppressed |

- Without the BEV gate, the ICE regex would have matched 946,518 of the non-BEV items. The gate is what makes the rule safe.
- The 21,128 BEV suppressions split as follows:
  - Rule A: 16,315 ICE-only items.
  - Rule B: 4,813 items. These are make-level titles on the 25 BEV slugs whose car-parts.ie page is a brand-level fallback, plus 17 block titles.
- 329 ICE items on BEV pages were already blocked by the current guard.

Coverage of this manifest against the 14-day refresh:
- Every manifest row whose SKU is in its model's stored payload is now blocked: ICE 7,165/7,165 and MISATTRIBUTED 1,092/1,092.
- The other 1,359 ICE rows and 1,377 MISATTRIBUTED rows are not in the current payloads, so the refresh does not recreate them.
- Without rule B, the 1,092 MISATTRIBUTED rows with make-level or block evidence come back on the next refresh.
- Rule B also stops about 4.8k future non-ICE make-level items on BEV pages. None of these is proven wrong today; they simply carry no vehicle evidence. This part is a policy choice.

```python
# ---- proposed addition to backend/cpie_fitment_guard.py (C1, 2026-11-17) ----
import json, os, re

_BEV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bev_models.json")


def _load_bev(path: str = _BEV_FILE):
    """Fails LOUD at import if the file is missing/invalid: a silently empty list would disable the rule."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    slugs = frozenset(s.strip("/").lower() for s in d["vehicle_slugs"])
    models = frozenset((r["manufacturer"], r["model"]) for r in d["c1_scope"] + d["extended"])
    assert slugs and not (slugs & {s.strip("/").lower() for s in d.get("not_bev", {})}), "bev_models.json lists a non-BEV slug"
    return slugs, models


BEV_SLUGS, BEV_MODELS = _load_bev()

_OILNEG = r"transmission|gearbox|gear oil|automatic|\batf\b|differential|axle|power steering|steering|hydraulic|shock|brake|compressor|\bdsg\b|cvt|haldex"
_ICE_ONLY = [(re.compile(p), re.compile(n) if n else None) for p, n in (
    (r"\bengine oil\b|\bmotor oil\b", _OILNEG),
    (r"\boil filter\b", _OILNEG),
    (r"\bsump\b|\boil drain plug\b|\boil pan\b|\bdipstick\b|\boil filler\b", _OILNEG),
    (r"\bcrankcase\b", None),
    (r"\boil cooler\b", _OILNEG),
    (r"\boil pressure (switch|sensor)\b", _OILNEG),
    (r"\bfuel\b", None),
    (r"\bspark ?plugs?\b|\bglow ?plugs?\b|\bignition (coil|lead|leads|cable|cables|cable kit|distributor)\b", None),
    (r"\bair filter\b", r"cabin|pollen|interior|compressor|charcoal|activated|carbon|breather|dryer|drier|suspension"),
    (r"\bexhaust\b|\bsilencer\b|\bmuffler\b|\bcatalytic\b|\bcatalyst\b|\bparticulate\b|\bdpf\b|\blambda\b|\boxygen sensor\b|\begr\b", None),
    (r"\bturbo(charger)?\b|\bintercooler\b|\bcharge air (cooler|hose|pipe)\b", None),
    (r"\bclutch\b|\bflywheel\b|\bdual[- ]mass\b|\brelease bearing\b", r"air conditioning|compressor|a/c|haldex|viscous|fan|magnetic"),
    (r"\btiming (belt|chain)\b|\bcambelt\b|\bcamshaft\b|\bcrankshaft\b|\bvalve stem\b|\btappet\b|\brocker (arm|cover)\b|\bcylinder head\b|\bhead gasket\b"
     r"|\bvalve cover\b|\bpistons?\b|\bconnecting rod\b|\bpiston rings?\b", r"brake|caliper|shock|damper|strut"),
    (r"\bengine starter\b|\bstarter motor\b|\bstarter\b|\balternator\b", r"battery|jump"),
    (r"\binjector\b|\binjection (pump|valve|nozzle)\b|\binjection\b", r"washer|water|windscreen|headlight"),
    (r"\bintake manifold\b|\bthrottle (body|valve|housing)\b|\bair mass\b|\bair flow (meter|sensor)\b|\bmass air\b|\bcarburett?or\b", None),
    (r"\badblue\b|\burea\b|\bdiesel exhaust fluid\b", None),
)]


def is_ice_only(name: str) -> bool:
    n = (name or "").lower()
    return any(p.search(n) and not (neg and neg.search(n)) for p, neg in _ICE_ONLY)


def _is_bev(vehicle_slug: str = "", manufacturer: str = "", model: str = "") -> bool:
    return (vehicle_slug or "").strip("/").lower() in BEV_SLUGS or (manufacturer, model) in BEV_MODELS


def _is_make_level_title(name: str, sku: str) -> bool:
    """car-parts.ie brand-level fallback page ('AUDI oil filter RIDEX 7O0002', 'Car brake pads RIDEX 402B0275'): the title names
    no model and ends with the article number."""
    n, s = (name or "").strip(), (sku or "").strip()
    return bool(s) and n.lower().endswith(s.lower()) and bool(re.match(r"^\S+\s+[a-z]+(\s|$)", n))


def fitment_allowed(payload_size: int, name: str = "", slug: str = "", extra: str = "",
                    vehicle_slug: str = "", sku: str = "", manufacturer: str = "", model: str = "") -> bool:
    # C1 rule A: an ICE-only product never fits a pure battery-electric vehicle (only BEV vehicles enter this branch).
    # C1 rule B: on a BEV listing, a make-level fallback title or a popular-parts block title ('Name (BRAND, SKU)')
    #            carries no vehicle evidence (only BEV vehicles enter this branch).
    if _is_bev(vehicle_slug, manufacturer, model):
        if is_ice_only(name):
            return False
        if _is_make_level_title(name, sku) or _BLOCK_TITLE_RE.match(name or ""):
            return False
    ...  # existing body unchanged (SMALL_PAYLOAD block rule, block-title-on-other-category rule)

# ---- importer call site (backend/importers/car_parts_ie_import_generic.py, the existing _fit_ok line) ----
# _fit_ok = cpie_fitment_guard.fitment_allowed(_payload_size, name, product.get("category") or cpie_fitment_guard.slug_of(product.get("source_url") or ""),
#                                              description_text, vehicle_slug=vehicle_slug or "", sku=sku_raw,
#                                              manufacturer=manufacturer, model=model_name)
```

Regression tests to add with the change:
1. An ICE item on 'audi/etron' returns False.
2. The same item on 'audi/a4-b8' gives the same result as today.
3. An ICE item on 'byd/seal-u' (DM-i) gives the same result as today.
4. A vehicle-specific non-ICE title on a BEV ('BYD Seal Saloon brake pads ...') returns True.
5. A missing bev_models.json raises an import error (fails loud).

This guard does NOT cover the other pvf writers (fitment enrichment passes, ebay_fitment_backfill). Whether they write ICE parts onto BEVs is UNVERIFIED.

## Residual risk and confidence
- **ICE_ON_BEV: high.** A BEV has no such component. The regex was reviewed class by class. The only matches on brand text were block titles that name the ICE product itself.
  - Model-name false positives were checked: 0 rule-A hits on BEV payloads and 0 ICE-tier rows where 'turbo' matched without 'turbocharger' or the brand 'BR Turbo' (for example 'Taycan Turbo').
- **MISATTRIBUTED: medium-high.** The title vehicle is provably different, and there is no vehicle-specific evidence for the BEV. A component of the same make can still occasionally be shared across different platforms. Platform/body siblings and universal products were excluded to keep that risk low.
