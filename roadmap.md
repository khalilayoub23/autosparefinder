# AutoSpareFinder Roadmap

Last updated: 2026-04-06
Owner: TBD
Update cadence: Weekly

## 1) Mission
Build a production-grade, AI-assisted auto-parts platform with reliable order/payment flow, strong security, and scalable catalog quality.

## 2) Current Snapshot

### Product and Stack
- Backend: FastAPI + SQLAlchemy async + Alembic
- Frontend: React 18 + Vite + Zustand + Tailwind
- Datastores: PostgreSQL (catalog + PII), Redis, Meilisearch
- Infra: Docker Compose + Nginx + ClamAV
- AI layer: multi-agent orchestration in backend/BACKEND_AI_AGENTS.py

### Baseline Metrics (2026-04-06)
- Route modules: 22 (backend/routes)
- Frontend pages: 16 (frontend/src/pages)
- Alembic catalog migrations: 28 (backend/alembic/versions)
- Alembic PII migrations: 19 (backend/alembic_pii/versions)
- Top-level backend test modules: 5 (backend/tests/test_*.py)

### Existing Tracking Docs
- README.md
- FIXES_TRACKER.md
- PRE_LAUNCH_CHECKLIST.md
- DEPLOYMENT.md

## 3) 2026 Roadmap (Execution Order)

## Phase A: Launch Safety and Readiness (Now -> 2 weeks)
Goals:
- Close production blockers before public growth.
- Make deployment repeatable and verifiable.

Deliverables:
- Finalize go-live secrets and integrations:
  - STRIPE_SECRET_KEY (live)
  - STRIPE_WEBHOOK_SECRET (live)
  - SENDGRID_API_KEY
- Confirm frontend production API URL build configuration.
- Run and verify startup data tasks where required:
  - populate_supplier_parts
  - validate_migrations
- Confirm production-grade DB password and secret rotation plan.

Exit criteria:
- Pre-launch checklist has zero critical unchecked items.
- End-to-end flow validated: register -> login -> cart -> checkout -> payment verify -> invoice.

## Phase B: Commerce Reliability (2 -> 6 weeks)
Goals:
- Reduce payment/order friction and post-payment uncertainty.
- Improve resilience and observability for checkout flows.

Deliverables:
- Harden multi-order payment and verify-session behavior.
- Add regression tests for:
  - single checkout
  - multi checkout
  - payment verify retries
  - invoice idempotency
- Ensure notification and fulfillment triggers are idempotent and traceable.

Exit criteria:
- Payment-related incidents trend to near-zero.
- No duplicate fulfillment or duplicate invoices in repeated verify calls.

## Phase C: Catalog and Fitment Quality (6 -> 10 weeks)
Goals:
- Improve fitment correctness and search relevance.
- Reduce no-result searches and low-confidence matches.

Deliverables:
- Operationalize fitment enrichment passes and reporting artifacts.
- Track and reduce search misses from search_misses.
- Improve brand/model/submodel/year filter coverage.
- Add KPI dashboards for fitment coverage and miss-rate.

Exit criteria:
- Measurable increase in parts with usable compatibility data.
- Measurable decrease in zero-result search sessions.

## Phase D: Growth and Operations (10+ weeks)
Goals:
- Scale safely with strong operational discipline.
- Increase conversion and retention.

Deliverables:
- Admin analytics cleanup and KPI visibility.
- Marketing automation and lifecycle campaigns.
- SLOs and alerting for backend health, queue failures, and payment errors.

Exit criteria:
- Weekly metrics review operational.
- On-call runbook and incident process documented.

## 4) Workstream Tracker (Keep Updated)

| Workstream | Priority | Owner | Status | Target Date | KPI | Last Update |
|---|---|---|---|---|---|---|
| Production secrets and go-live configs | P0 | TBD | TODO | 2026-04-13 | 100% checklist complete | - |
| Stripe live webhook validation | P0 | TBD | TODO | 2026-04-13 | successful live test payment | - |
| Multi-payment reliability regression suite | P1 | TBD | TODO | 2026-04-20 | 0 critical regressions in CI | - |
| Verify-session idempotency hardening | P1 | TBD | TODO | 2026-04-20 | no duplicate invoice/fulfillment | - |
| Fitment enrichment pipeline operations | P1 | TBD | TODO | 2026-04-27 | +X% compatible_vehicles coverage | - |
| Search miss reduction loop | P2 | TBD | TODO | 2026-05-04 | -Y% zero-result searches | - |
| Ops monitoring and alerting | P2 | TBD | TODO | 2026-05-11 | SLO dashboard online | - |

Status values:
- TODO
- IN_PROGRESS
- BLOCKED
- DONE

## 5) Weekly Update Log Template

Copy this block each week:

### Week of YYYY-MM-DD
- Completed:
  -
- In progress:
  -
- Blockers:
  -
- Decisions:
  -
- KPI snapshot:
  - Checkout success rate:
  - Payment verification failures:
  - Search zero-result rate:
  - Fitment coverage:
- Next week plan:
  -

## 6) Definition of Done (Per Roadmap Item)
- Code merged on main.
- Tests added/updated and passing.
- Monitoring/logging in place for the change.
- Tracker row updated with date and outcome.

## 7) Change Control
Before marking any item DONE:
1. Link the PR/commit.
2. Record measurable outcome (not only implementation).
3. Update FIXES_TRACKER.md or PRE_LAUNCH_CHECKLIST.md when relevant.
4. Add one-line summary to the weekly log.
