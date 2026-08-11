---
name: dept-crm-email
description: Designs and reviews email copy for AutoSpareFinder's real, already-wired Gmail SMTP + email_templates.py infrastructure — SendGrid is dead code now, Gmail (autosparefinder2024@gmail.com) is the live provider, with real Gmail-relay sending-limit constraints this skill must respect.
---

# AutoSpareFinder CRM & Email Automation

Adapted from `digital-marketing-pro`'s `email-sequence` skill (635★,
verified clean) and `agency-agents`' `marketing-email-strategist` (136.8k★,
verified clean). Fills the "CRM & Email Automation Specialist" gap from
the original architecture proposal — corrected 2026-07-27 after checking
the actual live code: SendGrid is no longer the active path.

## Real infrastructure (verified against live code, not assumed)

- **Provider is Gmail SMTP, not SendGrid.** `routes/email_utils.py` is
  provider-agnostic: it uses generic SMTP if `SMTP_HOST` is set, and only
  falls back to the SendGrid HTTP API if `SMTP_HOST` is empty. Live
  `.env` has `SMTP_HOST=smtp.gmail.com`, `SMTP_FROM=autosparefinder2024@gmail.com`
  — the canonical business Google account (per the Canonical Google
  Account rule). SendGrid is present in code as an unused fallback only.
- **11 of 13 templates in `email_templates.py` are genuinely wired into
  live code** (verified by grepping actual call sites, not just checking
  the file exists):

  | Template | Called from |
  |---|---|
  | `welcome`, `verify_email` | `routes/auth.py` |
  | `password_reset` | `BACKEND_AUTH_SECURITY.py` |
  | `payment_received`, `invoice`, `missing_details`, `refund_confirmation` | `routes/payments.py` |
  | `delivery_update`, `review_request` | `routes/admin.py` |
  | `abandoned_cart`, `price_drop` | `BACKEND_API_ROUTES.py` |

  **`order_confirmation` and `password_changed` are defined but have zero
  call sites anywhere** — dormant, not wired to any trigger. Don't assume
  they fire; if a task needs one of these, that's a real gap to flag, not
  something already working.
- **This skill's real job is narrower than "design new sequences"** —
  the sequence infrastructure already exists and is wired. The actual
  work is: (a) review/improve copy inside the existing `_shell()` HTML
  template system in `email_templates.py`, (b) decide whether
  `order_confirmation`/`password_changed` are worth wiring up, (c) apply
  the Gmail-specific constraint below to anything that could scale in
  volume.

## Gmail SMTP relay — a real constraint, not a generic ESP

`smtp.gmail.com` is **not built for bulk/marketing sending** the way
Brevo/SendGrid/Mailgun are. This changes the guidance materially from the
generic "email-sequence" source skill:

- **Real Gmail sending limits**: ~500 messages/day for a standard Gmail
  account, ~2,000/day for Google Workspace — far below the "5,000+/day"
  bulk-sender thresholds the deliverability rules below assume.
- **Google actively flags/suspends accounts** that send bulk or
  marketing-styled content through the personal/Workspace SMTP relay —
  this is meant for transactional mail (which is exactly what
  `email_templates.py`'s wired templates are: order/payment/delivery
  notifications, not marketing blasts). **Do not propose scaling
  `abandoned_cart` or `price_drop` sends to a large customer segment
  through this same relay** without first flagging to the owner that
  Gmail SMTP is the wrong transport for that volume — a proper ESP
  (Brevo is explicitly documented as the recommended free option in
  `email_utils.py`'s own docstring, 300/day free) would be the real fix,
  not something this skill should silently work around.
- **WhatsApp is still the primary customer channel** — per project
  memory, cart reminders/order updates/NOA's brief already go through
  WhatsApp. Treat email as secondary (transactional receipts, occasional
  digest), never propose replacing WhatsApp flows with email ones.

## Process

1. **Confirm which template/trigger is real** before writing copy — check
   the call-site table above, don't assume a sequence exists.
2. Write copy inside the existing `_shell()`/`_btn()`/`_info_box()` HTML
   helpers already in `email_templates.py` — match the existing visual
   system, don't introduce a new one.
3. Write in the customer's detected language (He/Ar/En — same trilingual
   rule as every customer-facing surface).
4. **Reminder caps are LIFETIME, never a rolling window** — root-caused
   bug (`CLAUDE.md`, 2026-07-20: one cart got 48 reminders because a "3
   reminders" cap was actually a rolling 3-day window). Any cadence
   change to `abandoned_cart`/`price_drop` must keep the lifetime-cap +
   minimum-gap pattern already fixed — never reintroduce a rolling window.
5. If proposing new volume that could approach Gmail's daily limits,
   **flag the ESP-migration question explicitly** rather than letting it
   silently risk the account.

## Truth-Only Guardrail (MANDATORY)

Never claim a discount, loyalty benefit, or coupon in any email — none
exist (Mistake Log, 2026-07-05). Any price shown must route through
`_customer_price_fields`, never a flat estimate or the raw `unit_price`
(that's supplier cost). Never invent an inactivity/segment number
("you're one of our top customers!") without a real query backing it.

## Output

For copy review/creation: which real template + call site it affects,
the copy draft in the existing HTML helper style, language, and a note if
this change could push volume toward Gmail's sending limits.
