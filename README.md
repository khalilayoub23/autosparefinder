# Auto Spare Finder

Production-focused auto parts platform with a FastAPI backend, React frontend, PostgreSQL, Redis, and AI-assisted workflows.

## What Is In This Repo

- **Backend API**: FastAPI app in `backend/` — routes split into `backend/routes/`
- **Frontend**: React 18 + Vite in `frontend/`
- **Database**: PostgreSQL 16 + pgvector, migrations via Alembic
- **Search**: Meilisearch full-text index
- **Cache / pub-sub**: Redis 7
- **Container orchestration**: `docker-compose.yml`

## Project Layout

```text
autosparefinder/
├── backend/
│   ├── BACKEND_API_ROUTES.py       ← FastAPI app entry point + lifecycle handlers
│   ├── BACKEND_AI_AGENTS.py        ← AI agent definitions (10 agents)
│   ├── BACKEND_AUTH_SECURITY.py    ← JWT, 2FA, password helpers
│   ├── BACKEND_DATABASE_MODELS.py  ← SQLAlchemy models
│   ├── routes/                     ← Route modules (one file per domain)
│   │   ├── admin.py
│   │   ├── auth.py
│   │   ├── brands.py
│   │   ├── cart.py
│   │   ├── chat.py
│   │   ├── files.py
│   │   ├── invoices.py
│   │   ├── marketing.py
│   │   ├── notifications.py
│   │   ├── orders.py
│   │   ├── parts.py
│   │   ├── payments.py
│   │   ├── profile.py
│   │   ├── returns.py
│   │   ├── reviews.py
│   │   ├── support.py
│   │   ├── system.py
│   │   ├── vehicles.py
│   │   └── webhooks.py
│   ├── social/
│   │   ├── telegram_publisher.py
│   │   └── whatsapp_provider.py
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   ├── pages/                  ← Admin, Auth, Cart, Chat, Orders, Parts, Profile …
│   │   ├── components/
│   │   ├── stores/
│   │   └── api/
│   └── Dockerfile
├── database/
│   └── init.sql/
└── docker-compose.yml
```

## Prerequisites

- Docker + Docker Compose (recommended)
- Or local tooling:
  - Python 3.11+
  - Node.js 20+
  - PostgreSQL 16
  - Redis 7

## Quick Start (Docker)

1. Copy and fill in the root `.env` file:

```bash
cp .env.example .env   # then edit DB_PASSWORD, REDIS_PASSWORD, JWT_SECRET_KEY, etc.
```

2. Start the stack:

```bash
docker compose up -d
```

3. Check services:

```bash
docker compose ps
docker compose logs -f backend
```

> **Note**: The backend runs `alembic upgrade head` automatically on startup. No manual migration step is needed on first run.

## Local Development

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in required values
alembic -c alembic.ini upgrade head
uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Vite dev server: `http://localhost:5173`

## Dependencies

All backend dependencies are in `backend/requirements.txt` and have been audited — only packages actually used by the API or its CLI tools are listed. Notable packages:

| Package | Purpose |
|---|---|
| `fastapi` / `uvicorn` | Web framework |
| `sqlalchemy[asyncio]` + `asyncpg` | Async database ORM |
| `alembic` | DB schema migrations |
| `pgvector` | Vector similarity (parts image/text search) |
| `python-jose` / `passlib` | JWT auth + password hashing |
| `redis` | Session store, rate limiting, pub-sub |
| `httpx` | Async HTTP client (HuggingFace API, scraper) |
| `meilisearch-python-sdk` | Full-text search sync |
| `sentence-transformers` | Local AI embeddings (`paraphrase-multilingual-MiniLM-L12-v2`) |
| `reportlab` | PDF invoice generation |
| `openpyxl` | Excel parts import |
| `beautifulsoup4` | Catalog scraper |
| `sse-starlette` | Server-sent events (notifications) |
| `clamd` | ClamAV antivirus scanning |
| `email-validator` | Pydantic `EmailStr` validation |

## URLs

| URL | Description |
|---|---|
| `http://localhost:8000/docs` | Interactive API docs (Swagger) |
| `http://localhost:8000/api/v1/system/health` | Health check |
| `http://localhost:5173` | Frontend (local Vite dev) |
| `http://localhost` | Frontend (Docker / nginx) |

## Environment Variables

Key groups (full list in `backend/.env.example`):

| Group | Variables |
|---|---|
| Database | `DATABASE_URL`, `DATABASE_PII_URL` |
| Redis | `REDIS_URL` |
| Security | `JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY`, `ENCRYPTION_KEY` |
| Search | `MEILI_URL`, `MEILI_MASTER_KEY` |
| Integrations | `TWILIO_*`, `STRIPE_*`, `SENDGRID_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID`, `TELEGRAM_WEBHOOK_SECRET` |
| AI | `HF_TOKEN` (HuggingFace — agents, whisper; **optional** — local embeddings work without it) |
| Embedding model | `HF_EMBED_MODEL` (default: `paraphrase-multilingual-MiniLM-L12-v2` — runs locally, no key needed) |

## GitHub Actions Scraper Process

The catalog enrichment scraper runs from GitHub-hosted runners (not from the VPS IP), which helps avoid source blocking.

### Workflows

- `Test Source Accessibility` (`.github/workflows/test_sources.yml`): manual workflow to check which domains are reachable from GitHub runner IP.
- `Aftermarket Scraper` (`.github/workflows/scraper.yml`): daily scheduled run (02:00 UTC) plus manual trigger.

### Active Scraper Sources

The GitHub Actions scraper currently targets these 6 sources only:

1. `motorstore.co.il`
2. `meyle.com`
3. `bilstein.com`
4. `mann-filter.com`
5. `gates.com`
6. `brembo.com`

### Required Secret

Add this repository secret before running scraper workflows:

- Name: `CATALOG_DB_URL`
- Value format: `postgresql://autospare:<PASSWORD>@<PUBLIC_IP>:5432/autospare`

### Manual Run Process

1. Validate source reachability:

```bash
gh workflow run test_sources.yml --repo khalilayoub23/autosparefinder --ref main
gh run list --repo khalilayoub23/autosparefinder --workflow test_sources.yml --limit 1
```

2. Trigger scraper:

```bash
gh workflow run scraper.yml --repo khalilayoub23/autosparefinder --ref main
gh run list --repo khalilayoub23/autosparefinder --workflow scraper.yml --limit 1
```

3. Verify catalog updates in DB (example checks):

```bash
docker compose -f docker-compose.yml exec -T postgres_catalog \
  psql -U autospare -d autospare -c "SELECT COUNT(*) FROM part_cross_reference WHERE ref_type='aftermarket';"

docker compose -f docker-compose.yml exec -T postgres_catalog \
  psql -U autospare -d autospare -c "SELECT COUNT(*) FROM supplier_parts WHERE last_checked_at > NOW() - INTERVAL '24 hours';"
```

## Testing

```bash
cd backend
pytest tests/ -q --ignore=tests/test_system.py
```

Expected baseline: pre-existing infrastructure tests fail when Postgres/Redis are not reachable. All route logic tests pass.

## Troubleshooting

- **Backend fails to start** — check `.env` values, verify Postgres/Redis are up: `docker compose logs backend`
- **Frontend can't reach API** — verify backend is on port 8000, check `CORS_ORIGINS` in `.env`
- **Alembic errors on startup** — run `docker exec autospare_backend alembic -c alembic.ini current` to inspect migration state