# Deployment — AutoSpareFinder

Quick start (production-ready checklist):

- Provide a populated `.env` (see `.env.example`) and **never** commit secrets.
- Use Docker Compose for local / staging parity: `docker compose up --build`.
- Run DB migrations with Alembic: `alembic upgrade head`.
- Build frontend (`/frontend`) and serve via Nginx.
- Use a process manager (systemd) or container orchestrator in production.

Supported targets:
- Railway — quick, managed (see steps in repository root).
- VPS (Ubuntu) — full control; use the `deploy/deploy.sh` helper.
- Replit — for demos only.

Security & reliability notes:
- Rotate `JWT_SECRET_KEY` and `ENCRYPTION_KEY` regularly.
- Add Sentry and monitoring; do not expose DB credentials.
- Add CI that runs `pytest`, `mypy`, `flake8` and `docker compose config`.

For full step-by-step instructions see the repository README and the `deploy/` helpers.
