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

## Embedding model — paraphrase-multilingual-MiniLM-L12-v2

The backend uses a local `sentence-transformers` model for multilingual vector search (Hebrew + English parts queries).

**How it works:**
- The model is downloaded **at Docker build time** (see `Dockerfile`) — no runtime download required.
- Weights are cached at `/root/.cache/huggingface/hub/` inside the container (~90 MB, CPU-only).
- On container startup a background warmup task (`_warmup_embed_model`) loads the model into memory in a thread so the event loop stays free. The model is ready within ~20 seconds of startup.
- If the model is not cached (e.g. a fresh host or stripped image), `hf_embed()` returns `[]` and search falls back to Meilisearch-only — the API never crashes.

**To pre-download on a new host (outside Docker):**
```bash
python backend/generate_embeddings.py   # also populates pgvector embeddings for all parts
```

**To change the model:**
```bash
docker build --build-arg EMBED_MODEL=your/model .
# and set HF_EMBED_MODEL=your/model in .env
```

**No HF_TOKEN required** — the model is public and runs fully offline once cached.

For full step-by-step instructions see the repository README and the `deploy/` helpers.
