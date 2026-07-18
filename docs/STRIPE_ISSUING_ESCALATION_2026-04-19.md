Stripe Issuing Escalation - Sandbox

Date: 2026-04-19 UTC
Environment: Stripe test mode
Account: acct_1SWjhSF0pRS8R6n5

Issue summary

Supplier payouts are routed correctly through Stripe Issuing virtual cards, but almost all authorizations are declined with insufficient_funds for practical amounts.

What is confirmed

1. Routing is correct
- Supplier rail is Stripe Issuing.
- Active virtual cards are used in authorization attempts.

2. Card and cardholder status are healthy
- Active virtual cards found: 5
- Card IDs:
  - ic_1TNQYuF0pRS8R6n5gLQlgX2w (last4 0047)
  - ic_1TNQW3F0pRS8R6n56gGgkvpS (last4 0039)
  - ic_1TNQRIF0pRS8R6n5BufOgbcS (last4 0021)
  - ic_1TNQQpF0pRS8R6n5K6SYR8Dx (last4 0013)
  - ic_1TIwSRF0pRS8R6n5ROKOzcWn (last4 0005)
- Cardholders are active, requirements disabled_reason is null, and past_due is empty.

3. Topups are succeeding
- Recent successful topups include:
  - tu_1TNUDAF0pRS8R6n5wGSk4o1R (8315 usd)
  - tu_1TNTk0F0pRS8R6n5lCfzOGkq (8315 usd)
  - tu_1TNTGtF0pRS8R6n5Jw6T1ZSF (23456 usd)
  - tu_1TNTGmF0pRS8R6n5m04wxm9F (8315 usd)
  - tu_1TNSneF0pRS8R6n5Xd0Urtfx (23456 usd)

4. Balance shows available funds
- Available: 492049 usd cents
- Pending: 1653178 usd cents

5. Authorizations still fail for meaningful amounts across all cards
- Per-card probe on amount 3338 usd cents returned:
  - approved false
  - status closed
  - reason insufficient_funds
- Example auth IDs from per-card probe:
  - iauth_1TNq6zF0pRS8R6n5dJRCPCTi
  - iauth_1TNq6zF0pRS8R6n5erKwzILz
  - iauth_1TNq70F0pRS8R6n578BPnwS9
  - iauth_1TNq70F0pRS8R6n5qlQVkjUZ
  - iauth_1TNq71F0pRS8R6n5PKafFl7r

6. Tiny-threshold behavior indicates effective spend cap
- Same active card probe:
  - 1 cent approved true
  - 5 cents approved true
  - 10 cents approved true
  - 25 cents declined insufficient_funds
  - 50 cents declined insufficient_funds
- Probe card: ic_1TNQYuF0pRS8R6n5gLQlgX2w

What we need from Stripe

1. Explain why Issuing authorizations decline with insufficient_funds despite:
- successful topups
- positive available balance
- active cards and active cardholders

2. Confirm whether Issuing spendable balance in this sandbox account is separate from the reported account available balance.

3. Confirm if additional Issuing-specific setup is required in this sandbox to make virtual-card authorizations approve for amounts above 10 cents.

4. Provide exact steps to make test Issuing authorizations approve reliably at practical amounts (for example 3338 and 18312 cents).

Operational impact

- Customer payments can succeed.
- Supplier Issuing spend cannot complete for practical amounts.
- This blocks end-to-end supplier payout flow in test.

Repro endpoint used

- POST /v1/test_helpers/issuing/authorizations

Additional note

The application now records failure context (auth id, decline reason, topup id/topup status when attempted), and retries across multiple active virtual cards. This is not a client-side routing bug; it appears to be an Issuing funding/authorization state issue in Stripe sandbox.
