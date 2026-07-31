# Importer Rules — the build standard for every importer, harvester and scraper

**Status:** authoritative. **Enforced by** `backend/maintenance/audit_importers.py`
(exit code ≠ 0 on any ERROR — run it before shipping and before triggering any import).

```bash
python3 /app/maintenance/audit_importers.py          # human-readable
python3 /app/maintenance/audit_importers.py --json   # machine-readable
```

> **Why this file exists.** Every rule below was earned by a real production bug,
> and most were found more than once *because the rule lived only in prose*. On
> 2026-07-28 an audit of the existing 64 importers found **146 ERROR-level
> violations** — including seven mutually-incompatible category vocabularies all
> writing to the same column. Prose is not enforcement. The audit script is.
> When you add a rule here, add a check for it there in the same change.

---

## Rule index — the exact ids the audit prints

When the audit prints `[skip-locked] importers/foo.py:120`, grep this table for the
id and read the section. Kept in sync by
`backend/devtests/importer_rules_sync_test.py`, which fails if a rule is implemented
but undocumented (or documented but unimplemented).

| Rule id | Severity | Section |
|---|---|---|
| `on-conflict-constraint` | ERROR | §3 — `ON CONSTRAINT` only works for real constraints |
| `supplier-parts-conflict-target` | ERROR | §3 — the conflict is `(supplier_id, supplier_sku)` |
| `max-uuid` | ERROR | §3 — Postgres has no `max(uuid)` |
| `skip-locked` | WARN | §3 — batched writes need `FOR UPDATE SKIP LOCKED` |
| `importer-price-case-guard` | ERROR | §4 — never wipe a good price to 0 |
| `part-condition-case` | ERROR | §1 — `part_condition`/`part_type` are lowercase |
| `categorize-on-ingest` | ERROR | §2 — only `categorize_on_ingest()` makes a category |
| `hardcoded-fallback-category` | ERROR | §2 — `'General Parts'`, `'accessories'`, … are not categories |
| `private-categorizer` | ERROR | §2 — no file may hold its own category ruleset |
| `name-from-identifier` | ERROR | §7 — an OEM/SKU is not a name |
| `parts-images-write` | WARN | §8 — write `parts_images` or the part never gets a thumbnail |
| `image-not-captured` | ERROR | §8 — capture the photo at the source |
| `parts-images-source` | INFO | §8 — PDF/spreadsheet source has no photo to capture |
| `image-no-live-source` | INFO | §8 — hardcoded seed data; no page exists to capture from |
| `fitment-not-written` | ERROR/WARN | §8b — write the fitment TABLE, not just a JSONB blob |
| `fitment-placeholder-only` | INFO | §8b — `'All Models'` must NOT be written as fitment |
| `specifications-missing` | ERROR/WARN | §8c — provenance is mandatory |
| `warranty-capture` | WARN | §5 — `warranty_policy.resolve()` |
| `absent-column-guard` | WARN | §6 — a missing column fails OPEN |
| `column-does-not-exist` | ERROR | §3 — an INSERT names a column the table does not have |
| `syntax` | ERROR | file does not parse |

---

## 0. Before you write anything

1. **Does an importer for this source already exist?** Check `backend/importers/`.
   The single largest ASAP failure was not a vendor problem — our half of the
   integration had simply never been written, while a note claimed we were blocked.
2. **Where does it go?** `importers/` for catalog/price loading, `harvesters/` for
   site harvesting, `scrapers/` for HTML/Playwright extraction, `maintenance/` for
   backfills and cleanup. Never the repo root. See CLAUDE.md → *Creating a NEW file*.
3. **How is it triggered?** A file nothing calls is not done. Register a supervised
   loop, add it to a task list, or document the exact invocation.
4. **Read the source's REAL header/response first.** Never map a column you have not
   seen. (A guard built on an absent column fails **open** — see §6.)

---

## 1. What every importer MUST write

| Field | Column | Rule |
|---|---|---|
| Identity | `parts_catalog.sku`, `oem_number` | SKU stable across re-runs |
| Name | `name`, `name_he` | A real description — **never the OEM number** (§7) |
| Category | `category` | Only via `categorize_on_ingest()` (§2) |
| Price | `importer_price_ils`, `base_price`, `max_price_ils` | Formula below; guarded on update (§4) |
| Offer | `supplier_parts` | `price_ils`, `is_available`, warranty (§5) |
| Image | `parts_images` | If the source exposes one (§8) |
| Fitment | `part_vehicle_fitment` | If the source provides it (§3) |

**Price formula** (consumer price incl. VAT → our numbers):

```
cost               = consumer_price / 1.18
importer_price_ils = cost
base_price         = cost * 1.45          # uniform 45% margin, no exceptions
max_price_ils      = consumer_price       # consumer reference
part_condition     = 'new'                # ALWAYS lowercase
part_type          = 'oem' | 'aftermarket' | 'oe_equivalent'   # lowercase
```

If the source gives an **ex-VAT** price, `importer_price_ils = price` and
`max_price_ils = price * 1.18`. Never double-apply VAT.

---

## 2. Categorization — ONE source of truth

**`category_map.categorize_on_ingest()` is the only way to produce a category.**

```python
from category_map import categorize_on_ingest
category = categorize_on_ingest(name=name_en, name_he=name_he, extra=source_label)
```

- **Never write a private categorizer.** Not `guess_category()`, not `categorise()`,
  not `map_cat()`, not `category()`. The audit rejects any function whose return
  literals are not canonical.
- **Never invent a vocabulary.** `parts_catalog.category` stores **English slugs**
  plus `כללי`. Not `Engine Parts`, not `fuel_system`, not `brakes-clutch`, not
  `General Parts`, not `Parts & Accessories`, and **not Hebrew display names**
  (`בלמים`, `מנוע`) — those are display-only and are never storable.
- **The only fallback is `כללי`.** Never default to `general`, `service-general`,
  `accessories` or `tools-equipment`. Those are real categories reachable only by a
  genuine match — and because the self-healing categorizer **only re-processes
  `כללי`**, a real-but-guessed category *looks finished and is never revisited*.
- **A raw source label is a hint, never the stored value** — pass it as `extra=`.
- **`INSERT … SELECT`** cannot call Python per row. Write `'כללי'` there; the
  cleanup task will categorize it on the next pass.
- Adding a keyword? Add it to `category_map.py` **only**.

*Earned by:* seven live vocabularies across toyota, mazda, champion, delek, bydil,
kia ×2, sng_barratt, kia_israel, lexus, porsche, il_importer_pdf, jaguar_batch —
~900K parts needing recategorization and a normalizer permanently cleaning up after
importers that kept re-poisoning the column.

---

## 3. SQL patterns that have bitten us

### `ON CONFLICT` — constraint vs index
```sql
-- supplier_parts: the collision on re-import is (supplier_id, supplier_sku)
ON CONFLICT ON CONSTRAINT supplier_parts_supplier_id_supplier_sku_key DO UPDATE SET …

-- part_vehicle_fitment: uix_pvf_… is a UNIQUE INDEX, not a CONSTRAINT
ON CONFLICT (part_id, manufacturer, model, year_from) DO UPDATE SET …
```
- **`ON CONFLICT ON CONSTRAINT` works only for real CONSTRAINTS.** Naming a unique
  *index* raises “constraint does not exist” and **every row fails**.
- **Never `ON CONFLICT (part_id, supplier_id)`** in an importer — that is not the
  constraint that fires, so price and stock updates are silently discarded.
- Check `pg_constraint` vs `pg_indexes` before naming one.

*Earned by:* ASAP fitment writing **0 of 8,210** rows and Fox Factory's 8,088 failing
the same way unnoticed; 4 importers discarding price updates for months.

### Columns must actually exist
Every column an INSERT names is validated against the LIVE schema. A wrong column
name fails only at RUNTIME — `py_compile` passes and a static audit passes, then the
importer dies on its first real row.

*Earned by:* `scrapers/bdv_playwright_scraper.py` wrote `sku`, `currency` and
`in_stock` to `supplier_parts`, whose real columns are `supplier_sku` and
`is_available`. Every supplier offer it ever attempted would have failed.

The check degrades safely: if the DB is unreachable it reports nothing rather than
guessing at schema.

### Read your failure tallies
A per-row savepoint that counts failures must be **read**. `8,210/8,210 skipped` is
not a warning, it is a total outage.

### Batched writes
Any batched `UPDATE`/`DELETE` on `parts_catalog`, `supplier_parts` or
`part_vehicle_fitment` (tables the harvester writes continuously) MUST:
- use `FOR UPDATE SKIP LOCKED`, or it queues behind the harvester and dies on the
  statement timeout **having written nothing**;
- drive its cursor from an **indexed** column — keyset on the PK (`WHERE id > $1
  ORDER BY id LIMIT n`) or a selective indexed predicate;
- decide termination from the batch's own returned row count, **never** a fresh
  full-table `COUNT(*)`;
- set `SET LOCAL statement_timeout`.

**`MAX(id)` on a uuid does not exist** in Postgres — take a cursor with
`ORDER BY id DESC LIMIT 1`.

### OEM matching
IL price lists often drop dashes (`517592B300` vs `51759-2B300`). Try exact first,
then normalized:
```sql
WHERE REPLACE(REPLACE(UPPER(oem_number),' ',''),'-','') = $1
```

---

## 4. Price guards

Never let a re-run destroy a good price.

```sql
importer_price_ils = CASE WHEN EXCLUDED.importer_price_ils > 0
                          THEN EXCLUDED.importer_price_ils
                          ELSE parts_catalog.importer_price_ils END
```
The same guard applies to a direct `UPDATE … SET importer_price_ils = $n` — use
`CASE WHEN $n > 0 THEN $n ELSE parts_catalog.importer_price_ils END`.

*Earned by:* 15 importers that wiped `importer_price_ils` to 0 on re-import.

---

## 5. Warranty

```python
from warranty_policy import resolve
months, source = resolve(row.get("warranty_months"), row.get("warranty"))
```
`resolve()` always returns a usable `(months, source)`. Write **both** —
`warranty_source` records whether the supplier stated it (`'supplier'`) or we applied
the platform default (`'platform_default'`). Never collapse the two: a surface that
says "supplier warranty" must call `warranty_policy.is_supplier_stated()`.

*Earned by:* REX capturing no warranty at all — 293,263 rows.

---

## 6. Availability / discontinued

**A field read from a column that does not exist fails OPEN, silently.** Verify each
mapped column against the real header before building a guard on it.

```python
discontinued = _f(row, "discontinued_item").strip().lower() in ("true", "1", "yes")
in_stock = (not discontinued) and _f(row, "availability").lower() not in (
    "discontinued", "out of stock", "unavailable", "0")
```
Discontinued parts stay in the catalog (searchable) but must be
`supplier_parts.is_available = FALSE`.

*Earned by:* 566 discontinued ASAP parts advertised as buyable because the importer
read an `availability` column the sheet does not have.

---

## 7. Names — enforced by `name-from-identifier`

The part `name` must be a real description. **Never substitute an OEM/SKU**: the part
becomes unsearchable by keyword and its category is then guessed from a part code
(`IL333` → `engine`).

```python
name_missing = not (name_en or name_he)      # decide BEFORE the fallback
name = name_en or name_he or sku             # still create the part…
...
needs_oem_lookup = name_missing               # …but FLAG it for enrichment
```

The rule accepts either form: capture a real name, **or** fall back and set
`needs_oem_lookup = TRUE` so `ai_catalog_builder` fills one. It reads the value
actually bound to that column (not merely its presence), so an importer that already
passes `true` for every row is not nagged.

Never delete or deactivate the part — the platform rule is that every part stays
searchable.

*Earned by:* 28,183 active parts whose name is a bare part code — 21,795 from one
`if not name: name = oem_raw` that also hard-coded `needs_oem_lookup = FALSE`, so
nothing downstream ever knew; plus colmobil's parser capturing `{oem, price}` and
discarding the description its own docstring documented.

---

## 8. Images

If the source exposes a part photo, write it to `parts_images` — the thumbnail
pipeline reads that table and **nothing else**, so a part without a row there can
never get a thumbnail.

```sql
INSERT INTO parts_images (id, part_id, url, is_primary, created_at)
SELECT gen_random_uuid(), $1::uuid, $2::varchar, TRUE, NOW()
WHERE NOT EXISTS (SELECT 1 FROM parts_images WHERE part_id=$1::uuid AND url=$2::varchar)
```
- There is **no** unique `(part_id, url)` index → `NOT EXISTS`, never `ON CONFLICT`.
- `url` is `varchar` → cast `$2::varchar` or asyncpg raises `AmbiguousParameterError`.
- PDF/spreadsheet price lists have no image at source; those parts get images from an
  image-bearing source matched by **exact OEM** (owner-confirmed: eBay, AliExpress,
  Amayama, RockAuto and manufacturer/dropship partners; **not** Amazon).

**And capture it at the SOURCE** — enforced by `image-not-captured`. A scraper or
harvester that builds product records from a web page but never reads an image
field permanently denies every part it creates a picture: the thumbnail pipeline
reads `parts_images` and nothing else, so the only recovery is a full re-harvest.
Price-only importers (which match existing parts by OEM) are exempt.

*Earned by:* 92.2% of the catalog having no image, because capture was added in July
after ~3.1M parts had already been imported in June.

---

## 8b. Fitment — enforced by `fitment-not-written`

If the source carries vehicle data, write **`part_vehicle_fitment` rows**. Fitment-first
search (goal G1) JOINs that table — a `compatible_vehicles` JSONB blob is **not** a
substitute, and a part without fitment rows is invisible to a customer searching by
their car.

```sql
INSERT INTO part_vehicle_fitment (id, part_id, manufacturer, model, year_from, year_to, created_at)
VALUES (gen_random_uuid(), $1::uuid, $2::varchar, $3::varchar, $4::int, $5::int, NOW())
ON CONFLICT (part_id, manufacturer, model, year_from)   -- unique INDEX, see §3
DO UPDATE SET year_to = GREATEST(COALESCE(part_vehicle_fitment.year_to,0),
                                 COALESCE(EXCLUDED.year_to,0))
```

**NEVER write a placeholder model.** `"All Models"`, `"Universal"` and friends must not
reach the fitment table: they would make every part falsely match every model of that
make, which is far worse than having no row. The audit reports those as INFO so the
gap stays visible without pushing anyone into corrupting search.

---

## 8c. Specifications — enforced by `specifications-missing`

Every created part carries a `specifications` JSONB with its provenance. Minimum
`{"source": "<importer or site>"}`; add `source_url`, `part_brand`, `discovered_at`
where available.

This is what makes a bad row traceable months later — it is how today's audit could
attribute 21,795 bad names to oempartsonline and 6,033 to colmobil. Without it you
cannot tell which importer to fix or which source to re-harvest.

---

## 9. Before you ship — checklist

- [ ] `python3 /app/maintenance/audit_importers.py` exits **0**
- [ ] `python3 -m py_compile <file>` passes
- [ ] **Every SQL statement's highest `$N` equals the number of arguments passed**,
      and the numbering is contiguous — a mismatch fails only at RUNTIME. If you add
      a parameter, append it **last**; inserting mid-list silently rebinds every
      following `$N` (this nearly wrote `base_price` into `category`).
- [ ] Ran against a real sample and **read the tallies** — `inserted`, `updated`,
      `skipped`, `errors`. A 100% skip rate is an outage, not a no-op.
- [ ] Verified the outcome in the DB, not the script's self-report.
- [ ] Top-of-file docstring updated (Script / Purpose / Process / Data Modified /
      Data Sources / Last Updated).

---

## 10. THE AUDIT PROCESS — how to run one, and how not to

This is the method that took 146 ERRORs → 0 on 2026-07-28. Follow it when adding a
rule, auditing a new source, or re-auditing after a batch of importers is written.

### 1. Write the check before you write the fix
A rule that lives only in prose is not enforced — that is the whole reason this
document has a script behind it. **146 violations existed of rules already written in
CLAUDE.md.** Add the check in `audit_importers.py` in the same change as the rule.

### 2. Self-test the rule against a fixture of KNOWN bugs
Build a small file that reproduces the defects you are trying to catch and confirm the
rule catches them. A rule that has never fired on a known-bad input is a guess.

### 3. Validate the rule against reality BEFORE fixing anything
Every rule written that day produced false positives on the first run:

| Rule | First run | Real | What it was actually matching |
|---|---|---|---|
| `parts-images-write` | 51 | 2 | PDF/spreadsheet sources with no photo to capture |
| `hardcoded-fallback-category` | 37 | 12 | **comments explaining that the literal was removed**, and raw source labels that DO get mapped |
| `image-not-captured` | 22 | 12 | price-only importers; hardcoded seed data with no HTTP call |
| `fitment-not-written` | 6 | 3 | sources whose only "model" is the placeholder `All Models` |

**A check that is wrong more often than right gets ignored, which is worse than no
check.** Spot-check the first handful of hits by hand. If the rule is mostly wrong,
fix the rule — do not start editing importers.

### 4. Never demand a fix that would corrupt data
Two rules had to be softened to INFO because the "fix" was worse than the finding:
- `'All Models'` written to `part_vehicle_fitment` would make every part falsely match
  every model of that make.
- A hardcoded seed importer has no page to read a photo from.

Report the gap; do not force the corrupting fix.

### 5. Read what the statement ACTUALLY writes
`_insert_value_for(src, column)` maps an INSERT's column list onto its value list.
**Positional SQL is how nearly every defect hid** — `'General Parts'` in the Nth slot
is invisible to a `column = '...'` regex. It also lets a rule EXEMPT correct code
(two importers already passed `needs_oem_lookup=true`, so their fallback was
legitimate).

### 6. Append new parameters LAST
Inserting a parameter mid-list silently rebinds every following `$N`. In one file this
would have written `base_price` into `category`; in another it would have marked parts
`is_safety_critical`. **Always verify column→value mapping after an edit.**

### 7. Compile is not runtime
`py_compile` passes on an unbound name, a wrong module alias, and a mismatched
parameter count. Verify:
- **param/arg parity** — highest `$N` == number of arguments, contiguous;
- **binding order** — by AST, not by eye (`fitment_rows` assigned before use);
- **behaviour** — extract the function and `exec` it (see §10 below), or run the real path.

### 8. Fix dead code last, or not at all
Check `refs=` and whether a supervised loop calls it. Editing an unwired importer that
cannot be tested against a live source is exactly how the silent defects being fixed
here were introduced. An ERROR in code that never runs is correctly *reported* and
correctly *deferred* — the gate is "fix before running".

### 9. Keep the doc and the script in sync mechanically
`backend/devtests/importer_rules_sync_test.py` fails if a rule is implemented but
undocumented, documented but unimplemented, or if CLAUDE.md stops linking either. It
found 14 undocumented rule ids on its first run.

---

## 11. Testing importers

**Never bulk-`import` importer modules to test them** — several do real work at
import time. Use `py_compile` plus AST, and to test one function extract it and
`exec` it in isolation:

```python
src = pathlib.Path(f).read_text()
m = re.search(r'\ndef guess_category\(.*?\n(?=\S)', src, re.S)
ns = {'categorize_on_ingest': categorize_on_ingest}
exec(m.group(0), ns)
ns['guess_category']("דיסק בלם")
```

And remember: **compile is not runtime.** A syntax check proves nothing about a call
path — exercise the real one.
