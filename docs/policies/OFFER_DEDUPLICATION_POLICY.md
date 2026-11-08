<!-- policy-id: offer-deduplication -->
<!-- policy-version: 1.1.0 -->
# Offer Deduplication Policy

**Policy id:** `offer-deduplication` · **Version:** `1.1.0` · **Effective:** 2026-11-07
**Owner:** the platform owner (Khalil). Only the owner approves a change to this file.
**Machine-readable counterpart:** `backend/offer_policy.py` (loader + classifier + write gate) and
`backend/variant_identity.py` (the single canonical-variant module, §1a). The SHA-256 of THIS file's bytes is the
policy hash every agent/report must quote; production pins it (§8). The version above is informational; the hash is authoritative.

This is the ONLY definition of offer identity, duplicate, variant, price update, misattribution and offer restoration in
AutoSpareFinder. It replaces every earlier, partial or conflicting definition (SKU-only merging, "same price = duplicate",
"retire the copy because another supplier sells it", `merge_master_parts` / `bmw_oem_dedup` / `dedup_catalog_parts`
heuristics where they touch `supplier_parts`). No agent, importer, script or prompt may define its own.

## 1. Vocabulary

* **Offer** — one row of `supplier_parts`: one supplier's commercial offer for one catalog part.
* **Identity of an offer** — the tuple *(supplier, product identity, variant)*:
  * **supplier** = `supplier_id` (a field value; see §3 rule 7 — not proof of source).
  * **product identity** = the catalog part (`part_id`) plus the normalized OEM/manufacturer it stands for.
  * **variant** = the offer's **canonical variant** (§1a): `oem`, `oe_equivalent`, `aftermarket`, `remanufactured`,
    `used`; NULL = legacy, inherits the catalog part's class — together with brand/specification the source states.
* **Evidence** — facts from the row or its provenance: `supplier_sku`, `supplier_url`, `part_type`, price + `updated_at`,
  `price_history`, import stamp, source file/page, a dated backup. A price or SKU is evidence only in combination (§3).

### 1a. Canonical variant identity

Every comparison of variants — classification (§2), the database uniqueness rule (one offer per supplier + part +
variant), duplicate checks, imports and restorations (§5a) — is made on the **canonical variant** produced by ONE shared
module (`variant_identity`: `canonical_variant` / `variant_key`; its SQL twin `INDEX_KEY_SQL` is the unique-index
expression). It is never made on the raw `part_type` text. The module is case- and whitespace-insensitive and collapses the
synonyms the platform ingests (English `Original`/`Genuine`/`OE`/`OEM` and the Hebrew `מקורי` are all the `oem` class — the
stored spellings `original` and `oem` are one identity; likewise for the other classes).

* A label the function does not recognise is **neither the same as nor different from** any variant: the offer is **F**
  (unresolved) and is never a basis to bypass a uniqueness check or to write.
* Canonicalisation happens **before** any uniqueness or duplicate check. Reference defect (2026-11-06): seven restored
  offers labelled `מקורי` were compared as a *different* variant from `oem`, passed the uniqueness check and then collided
  with the canonical part's `oem` offers; they were rolled back.

## 2. The six classifications (exhaustive and mutually exclusive)

| Code | Name | Definition | Treatment |
|---|---|---|---|
| **A** | `COMPETING_OFFERS` | Same part offered by **different suppliers**, whatever their prices. | Keep every valid offer independently. |
| **B** | `DISTINCT_VARIANTS` | **Same supplier**, same/similar part number, but offers differ in canonical variant, brand, specification or other meaningful characteristic (Genuine, OEM, OEM-equivalent, Compatible, Aftermarket/Replacement). | Keep each variant as its own offer. Never merge on SKU/part number. |
| **C** | `PRICE_UPDATE` | Same supplier, same commercial offer, same product and variant — **the price changed**. | Update the one correct offer; keep price history (`price_history`), timestamp, source, currency. |
| **D** | `TRUE_DUPLICATE` | The same commercial offer from the same supplier for the same product **and** variant with the same relevant characteristics, stored more than once. | Resolve **reversibly**: keep the canonical record, preserve history/source evidence, log before/after. |
| **E** | `MISATTRIBUTED` | An offer is assigned to a supplier that is not its actual source. | Correct the attribution **only** with reliable source evidence; keep the original attribution in the audit log. |
| **F** | `UNRESOLVED` | Evidence is insufficient to establish supplier, product, variant or source. | **Leave unchanged** and flag for investigation. Never merge, delete, hide, deactivate or reassign. |

### Decision order (first match wins)

1. Different `supplier_id` → **A** (unless E's evidence applies to one of them, then E is decided for that offer on its own).
2. Same supplier, either side's variant cannot be established (both `part_type` NULL **and** no catalog class **and** the two
   differ in name/brand/URL), or a label is not recognised (§1a) → **F**.
3. Same supplier, canonical variants differ → **B**.
4. Same supplier, same variant, same commercial offer, and the later record is the same offer observed later with a different
   price → **C**. "Same commercial offer" requires the *same* `supplier_sku` or the same row id/URL — not a price relation.
5. Same supplier, same variant, same commercial offer, identical relevant characteristics (price, availability, URL) → **D**.
6. Anything else → **F**.

## 3. Mandatory rules

1. Matching SKU or part number alone never proves duplication.
2. Similar or identical prices never prove duplication.
3. Another supplier having the same part never justifies removing, hiding or deactivating an offer.
4. Different suppliers keep independent commercial offers (A).
5. Different variants stay separate (B). Missing variant information is **F**, not "same".
6. A price difference is not, by itself, a price update (C needs same supplier + same commercial offer + same variant).
7. The `supplier_id` column is not proof of who actually sourced the offer. Reassign (E) only with source evidence
   (import stamp/file, source URL on the real supplier's domain, a matching real offer from that supplier). Price-derivation
   evidence (e.g. `price = max_price_ils/1.18`, one bulk stamp) shows the attribution is *wrong*; it does **not** by itself
   show who the right supplier is.
8. Uncertain → **F**, unchanged.
9. A database uniqueness rule must not prevent legitimate competing offers or variants (see §6), and must be evaluated on
   the canonical variant (§1a).
10. No agent may add a duplicate definition or override this policy. Nothing is hard-deleted: resolve by recoverable state
    change with an audit row; an offer must never be removed if it is a product's only orderable offer. The single
    exception is the **reversal of an insert made by an authorised restoration run (§5a)**, using that run's manifest, and
    only while the restored row is still unchanged.
11. Every write that touches offer identity is preceded by a dry-run and carries the policy hash in its audit row.
12. Variants are compared only after canonicalisation (§1a); raw-label comparison, a private normalisation table, or a
    uniqueness check on raw `part_type` text is prohibited.
13. Restoring a previously deleted offer is an INSERT governed by §5a. It never overwrites, updates, merges or re-labels a
    live offer.

## 4. Required evidence per classification

* **A** — two `supplier_id`s on the same `part_id` (or same normalized OEM+manufacturer).
* **B** — same `supplier_id`; differing canonical variant, or differing catalog class/brand/name stated by the source.
* **C** — same `supplier_id`, same `supplier_sku` (or same row), same variant; `price_history` or two observations.
* **D** — as C, plus equal price, availability and URL (an exact re-import).
* **E** — evidence under rule 7 AND a named correct supplier; otherwise **F**.
* **F** — default.

## 5. Prohibited actions

SKU-only or price-only merging; deleting an offer because another supplier lists the part; overwriting one supplier's
offer with another's data (including via `ON CONFLICT` on a key that does not include the supplier); reassigning supplier
without evidence; touching F records; any bulk write without dry-run, audit rows and a rollback path; running a legacy
duplicate routine that bypasses §2; a restoration that skips any requirement of §5a.

## 5a. Restoration of previously deleted offers (write action `restore`)

An offer that an earlier routine deleted (for example the old `merge_master_parts` conflict branch) may be re-inserted
only when **all** of the following hold. The write gate (`offer_policy.assert_write_allowed("restore", …)`) refuses the
action unless the first four are supplied as evidence, and the restoration script refuses to start without them.

* **R1 Authorization.** The exact scope — the list of offer ids, the source backup and its SHA-256 — has been approved by
  the owner for that run. The approval reference and the input file's SHA-256 are passed to the run and recorded in its
  manifest. A different scope needs a new approval.
* **R2 Evidence-based eligibility.** The original row comes from a dated backup (provenance recorded); its catalog part is
  now soft-merged into an active canonical part (`dedup_merged_into`); the canonical part holds an offer of the **same
  supplier**; the original id and the original `(supplier_id, supplier_sku)` are absent from `supplier_parts`.
* **R3 Class.** Only **B** or **F** relative to every same-supplier offer on the canonical part. A, C, D and E are never restored.
* **R4 Canonical variant first.** The variant of the restored row and of every existing same-supplier offer is canonicalised
  (§1a) **before** the uniqueness check. An unrecognised label is not restorable. If the canonical `(supplier, part, variant)`
  slot is already taken the row is **blocked**, never forced.
* **R5 No overwrite.** The row is inserted with `ON CONFLICT DO NOTHING`; no live offer is updated. Supplier, SKU, variant and
  price are preserved as in the source. The price is a historical value: the restored row is unavailable
  (`is_available = FALSE`, `availability = 'out_of_stock'`) until the supplier's own sync refreshes it, so it cannot be offered
  to a customer.
* **R6 Price history.** The run writes no `price_history`. The deleted offer's history stays in the backup; re-attaching it needs
  its own approval.
* **R7 Dry run, bounded apply.** A dry run on the same input precedes the apply and its verdict counts are recorded. The apply
  works in bounded batches with a savepoint per row.
* **R8 Reversible.** A manifest of the inserted ids is written before each batch commits; `--rollback` deletes exactly those
  ids and only while they are unchanged. Rollback is tested in a disposable database before production use.
* **R9 Hash enforcement.** The run starts only if the policy loads and, in production, matches the pinned hash (§8); the
  policy version and hash are in the manifest.
* **R10 Binding.** The script is listed in `offer_policy.POLICY_BOUND_ACTORS`; no other code path may insert a restored offer.

## 6. Known gaps (recorded, not hidden)

* **Resolved 2026-11-06:** the single-offer-per-supplier-per-part index `uq_supplier_parts_part_supplier` blocked class **B**
  on one catalog part. The replacement partial unique indexes (`uq_sp_part_supplier_variant`, `uq_sp_part_supplier_untyped`)
  were built 15:16–15:20 IL and the old index was dropped 19:31 IL. The variant index expression acts on the STORED value
  (it lists only `original/genuine/oem/oe`), so every writer must store a value from `variant_identity` (§1a); normalising
  legacy non-canonical stored labels is tracked as a follow-up.
* `supplier_parts` has no provenance column (source file/stamp); evidence for E/D comes from `updated_at` stamps,
  `supplier_url`, `price_history`, importer logs and dated backups.
* Legacy `part_type` data contains non-canonical labels (for example Hebrew `מקורי`, 31 rows in the 2026-06-30 backup) and
  blank values; they are treated as **F** until normalised.

## 7. Using the policy (agents and code)

* Load through `backend/offer_policy.py::require_policy()`: returns `(version, sha256)` or raises `PolicyUnavailable`.
* On `PolicyUnavailable` an agent/script performs **no** destructive or irreversible offer write, falls back to **no** older
  matching logic, reports the exact problem and leaves the records unchanged.
* Every report/audit row states `policy_version` and `policy_sha256`.
* Classification and write gating are done by `offer_policy.classify_pair` / `assert_write_allowed` — never re-implemented.
* A new agent gets offer/product write permission only after (a) it calls `require_policy()` before such work,
  (b) it appears in `offer_policy.POLICY_BOUND_ACTORS`, and (c) `tests/test_offer_policy.py` passes.

## 8. Change control and hash pin

One canonical file; no copies. A change is explicit, reviewed by the owner, version-bumped below, and followed by
`tests/test_offer_dedup_policy_pin.py` plus a re-run of the dry-run audit. The hash changes with every edit, so stale
reports are detectable.

**Runtime pin.** `docker-compose.yml` sets, for the backend, `OFFER_POLICY_EXPECTED_SHA256` (the exact SHA-256 of this file;
several hashes may be listed, separated by commas or spaces) and `OFFER_POLICY_REQUIRE_PIN=1`. With the require flag set the
loader refuses to load the policy when no pin is configured; with a pin set it refuses a file whose hash is not listed. The
sibling file `OFFER_DEDUPLICATION_POLICY.md.sha256` records the same hash for review; a test fails if the file, the sibling
and the compose pin disagree.

**Bump procedure (version change without a window of refused writes).**
1. Edit this file (text + change-history row) — nothing else in the same change.
2. Compute its SHA-256 and write it to the sibling `.sha256` file.
3. In `docker-compose.yml` list the **new** hash **and** the old one in `OFFER_POLICY_EXPECTED_SHA256`; recreate the backend
   (`--no-deps`, preflight SAFE). The running container now accepts both files.
4. Deploy the new policy file (the directory is bind-mounted read-only, so the container sees it at once).
5. Run `maintenance/verify_policy_pin.py` in the container; it must report file hash == a listed pin == runtime hash.
6. At the next recreate, remove the old hash from the list.
Skipping step 3 makes every gated offer write fail closed until the pin is updated — safe, but a stop. A hash mismatch is
never "fixed" by clearing the pin.

## 9. Change history

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-11-06 | Initial unified policy (classes A–F, decision order, fail-closed use, known gaps). |
| 1.1.0 | 2026-11-07 | Canonical variant identity (§1a, rules 9/12); restoration of deleted offers as the gated `restore` action (§5a, rule 13, rule 10 reversal exception); hash pin and bump procedure (§8); known gaps updated (indexes built, old index dropped). Minor bump: new normative rules, no change to classes A–F or the decision order other than §2 step 2/3 using the canonical variant. |
