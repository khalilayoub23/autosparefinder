# Auto Spare Finder

Production-focused auto parts platform with a FastAPI backend, React frontend, PostgreSQL, Redis, and AI-assisted workflows.

## What Is In This Repo

- Backend API: FastAPI app and business logic in `backend/`
- Frontend app: React + Vite app in `frontend/`
- Database bootstrap SQL: `database/init.sql`
- Container orchestration: `docker-compose.yml`
- Django scaffold files still exist at repo root (`manage.py`, `autosparefinder/`) but are not the main runtime path for the current stack.

## Current Project Layout

```text
autosparefinder/
├── backend/
│   ├── BACKEND_API_ROUTES.py
│   ├── BACKEND_AI_AGENTS.py
│   ├── BACKEND_AUTH_SECURITY.py
│   ├── BACKEND_DATABASE_MODELS.py
│   ├── requirements.txt
│   ├── .env.example
│   ├── alembic.ini
│   ├── alembic/
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── src/
│   │   └── pages/
│   └── Dockerfile
├── database/
│   └── init.sql
└── docker-compose.yml
```

## Prerequisites

- Docker + Docker Compose (recommended)
- Or local tooling:
  - Python 3.11+
  - Node.js 20+
  - PostgreSQL 16
  - Redis 7

## Quick Start (Recommended: Docker)

1. Create a root `.env` file for compose variables (for example `DB_PASSWORD`, `REDIS_PASSWORD`, `JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY`, `ENCRYPTION_KEY`, `MEILI_MASTER_KEY`, and integration keys as needed).
2. Start the stack:

```bash
docker-compose up -d --build
```

3. Check service health:

```bash
docker-compose ps
docker-compose logs -f backend
```

## Local Development

### 1) Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set required values in `.env` before running.

Run API:

```bash
uvicorn BACKEND_API_ROUTES:app --host 0.0.0.0 --port 8000 --reload
```

Optional migrations:

```bash
alembic -c alembic.ini upgrade head
```

### 2) Frontend

```bash
cd frontend
npm ci
npm run dev
```

The Vite dev server runs on `http://localhost:5173`.

## URLs

- Backend API docs: `http://localhost:8000/docs`
- Backend health: `http://localhost:8000/api/v1/system/health`
- Frontend (local Vite): `http://localhost:5173`
- Frontend (compose/nginx): `http://localhost`

## Environment Variables

Canonical backend variables are documented in:

- `backend/.env.example`

Key groups:

- Database: `DATABASE_URL`, `DATABASE_PII_URL`
- Redis: `REDIS_URL`
- Security: `JWT_SECRET_KEY`, `JWT_REFRESH_SECRET_KEY`, `ENCRYPTION_KEY`
- AI: `OLLAMA_URL`, `AGENTS_DEFAULT_MODEL`
- Search: `MEILI_URL`, `MEILI_MASTER_KEY`
- Integrations: Twilio, Stripe, SendGrid, Telegram

## Testing

Backend tests:

```bash
cd backend
pytest
```

## Troubleshooting

- Backend fails to start:
  - Check env values in `backend/.env`
  - Verify PostgreSQL/Redis reachability
  - Inspect logs: `docker-compose logs backend`

- Frontend cannot call API:
  - Verify backend is up on port 8000
  - Check CORS in backend env (`CORS_ORIGINS`)
  - If using Docker frontend, requests under `/api/` are proxied to backend

## Notes

- This README was trimmed to remove stale architecture counts, removed claims, and outdated endpoint/page statistics.
- Keep this file focused on verified setup/run behavior to prevent documentation drift.