# Campaign Brief — AutoSpareFinder System Launch (system_launch_2026)

> Produced via the `dept-campaign-launch` skill, 2026-08-04. Tier 1 launch
> (whole system going public). Every number below is live-queried — Truth-Only
> guardrail. Feeds `dept-cmo` Target Management history.

## Objective (SMART, real conversion event)
Drive first-time visitors to complete a **plate/VIN search → fitment-verified
result → checkout** (the real conversion event: web checkout completion /
`create_whatsapp_checkout`). Target for launch month: **10× the current order
run-rate** — from **3 orders in the last 30 days** to **≥30**, and **+250
registered users** over the current **179**. These are the honest pre-launch
baseline figures; the post-mortem compares against them, not a self-report.

## Positioning angle (from dept-positioning, live in NOA's prompt)
**"Verify the part fits your car BEFORE you pay, then compare real supplier
prices."** Competitors show a price first and fitment never. Lead line:
*"לא תזמינו חלק לא מתאים שוב"* — stronger than "cheapest price."

## Audience
- **B2C (primary):** individual car owners, search by plate/VIN/part name.
  **Trilingual — Hebrew / Arabic / English** (detect + match, never mix).
- **B2B (secondary, later phase):** repair shops / fleets. **No B2B program
  claims** (bulk tier / NET-30 / account manager) — none exists yet.

## Real fact base (live-queried 2026-08-04 — the only claims allowed)
- **4,056,584** active parts · **1,858,853** priced · **83** car brands
- **5,413,384** fitment rows (this is what makes "verified fit" real)
- **79** active suppliers · **247,741** clean part images
- Search by license plate, trilingual support, ship nationwide direct from supplier
- **Do NOT claim:** coupons, discounts, loyalty/referral program, GBP/local
  listing, flat VAT. None exist (VAT is conditional per `get_supplier_vat_rate`).

## Channel plan (organic-first, NOA-driven)
Publish-configured live channels (verified via `social/registry`):
**Facebook · Telegram · TikTok · Discord.** Plus **WhatsApp** (direct contact)
and the **website**. **Instagram** is drafted but held — the account is still
under Meta review (`instagram_business_account:none`); its post activates the
moment a token lands. Every post carries the **QR → channel-picker hub**
(`/api/v1/go`) so the audience self-selects their channel.

## Budget allocation guidance (framework, not an invented number)
No real channel-ROI history exists yet (pre-launch), so the correct default is
**70 / 20 / 10**: 70% to the proven-cheap organic channels NOA already runs
(Facebook/Telegram/TikTok/Discord + WhatsApp), 20% to one promising paid test
(Meta or TikTok ads pointing at plate-search, UTM-tagged), 10% experimental.
**The actual shekel budget is the owner's to set** — this brief does not invent
one. Re-allocate by real CPA/ROAS from `dept-analytics` once the first weeks of
data exist.

## UTM plan (reuse the live taxonomy, don't fork it)
`utm_source=<platform>` · `utm_medium=social` (or `=qr` from the hub) ·
`utm_campaign=system_launch_2026`. The QR posts already emit
`?src=qr_launch_<platform>` → the hub appends the UTM. Consistency here is what
makes attribution real instead of guessed.

## Timeline / phases
- **Phase 0 — pre-launch (now):** 5 launch posts queued for owner approval;
  baseline captured (above).
- **Phase 1 — launch week:** owner approves → NOA publishes across the live
  channels; QR drives channel choice; monitor plate-search → checkout.
- **Phase 2 — sustain (weeks 2-4):** NOA's normal daily loop keeps momentum on
  the real-part angle; fold in the best-performing launch hook.
- **Phase 3 — post-mortem:** real before/after vs the baseline → `dept-cmo`.

## KPIs (all real, from live data — not proxies)
1. Orders / month vs baseline **3** → target **≥30**.
2. Registered users vs baseline **179** → **+250**.
3. Plate/VIN searches → checkout conversion (search API → checkout event).
4. Per-channel clicks via the `system_launch_2026` UTM (which channel converts).

## Launch content (queued 2026-08-04, `status=pending_approval`)
5 posts through NOA's real pipeline, each QR-tagged, awaiting owner approval in
the WhatsApp console (`פוסטים` → `אשר <id>`):
| id | platform | angle |
|---|---|---|
| 0dfd8e17 | facebook | official "we're live" + the differentiator |
| 80ebd181 | tiktok | sharp hook: every garage a different price |
| 84fa532d | instagram | emotional driver story (held until IG token) |
| 1aa129e7 | telegram | community announce + 3 benefits |
| 44b2e227 | facebook | trust angle: "ordered a part that didn't fit?" |

## Launch checklist (Tier 1)
- [x] Positioning current (dept-positioning, live in NOA prompt)
- [x] Real fact base captured, truth-only enforced
- [x] UTM plan in place (`system_launch_2026` + QR hub)
- [x] `dept-analytics` baseline captured BEFORE launch (users 179, orders/30d 3)
- [x] Announcement drafted + queued (NOA, 5 posts)
- [ ] **Owner approval → publish** (the gate; owner-controlled)

## Post-mortem (to complete after launch week)
Real objective vs real outcome from live queries (orders, users, UTM clicks),
what worked, what didn't, one concrete change → `dept-cmo` Boost Decision. NOT a
self-report — the live before/after comparison is mandatory.
