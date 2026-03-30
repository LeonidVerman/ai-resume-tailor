# Phase 11 — Deployment and Operations Notes

Phase 11 adds deployment infrastructure and operational tooling to make
the SaaS system runnable locally, in staging, and in production.

---

## What Was Added

### Docker

| File | Purpose |
|---|---|
| `backend/Dockerfile` | Backend container (Python 3.11-slim, uvicorn) |
| `frontend/Dockerfile` | Frontend container (multi-stage Node 20, Next.js standalone) |
| `frontend/next.config.mjs` | Added `output: "standalone"` for Docker build |

### docker-compose

`docker-compose.yml` updated with fully functional services:

| Service | Port | Profile |
|---|---|---|
| `postgres` | 5432 | default |
| `backend` | 8000 | default |
| `frontend` | 3000 | default |
| `minio` | 9000/9001 | `--profile storage` |
| `libreoffice` | — | `--profile libreoffice` (legacy) |

### Scripts

| Script | Description |
|---|---|
| `scripts/dev_backend.sh` | Start FastAPI with hot reload |
| `scripts/dev_frontend.sh` | Start Next.js dev server |
| `scripts/run_migrations.sh` | Run Alembic migrations |
| `scripts/run_tests.sh` | Run test suite (cli / backend / fast / all) |

### API Endpoints

| Endpoint | Added | Description |
|---|---|---|
| `GET /api/v1/health` | Existed | Liveness probe |
| `GET /api/v1/health/db` | **New** | Readiness probe (DB connectivity) |
| `GET /api/v1/metrics` | **New** | In-process operational counters |

### Environment Variables

`.env.example` updated to include all SaaS variables (previously commented out):
- `DATABASE_URL`, `POSTGRES_*`
- `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`
- `STORAGE_*` (S3-compatible)
- `MINIO_*` (local dev)
- `STRIPE_*`
- `NEXT_PUBLIC_*` (frontend)

### CI Pipeline

`.github/workflows/ci.yml` runs on every push to `main` / `develop`:

1. **CLI Generator Tests** — `pytest tests/`
2. **Backend Tests** — `pytest backend/tests/` with a postgres service container
3. **Frontend Lint + Build** — `npm run lint && npm run build`
4. **Docker Backend Build** — `docker build -f backend/Dockerfile`
5. **Docker Frontend Build** — `docker build -f frontend/Dockerfile`

### Documentation

| File | Description |
|---|---|
| `doc/DEPLOYMENT.md` | How to deploy to Railway, Render, or Docker |
| `doc/OPERATIONS.md` | Day-to-day operations: running, logs, migrations, troubleshooting |

### Infra Updates

- `infra/railway/railway.toml` — updated to point to `backend/Dockerfile`
- `infra/docker/backend.Dockerfile` — updated to redirect to canonical `backend/Dockerfile`

---

## Local Development Workflow

```bash
# 1. Configure environment
cp .env.example .env
# Edit .env with your OPENAI_API_KEY

# 2. Start full stack
docker-compose up

# OR start services individually:
docker-compose up postgres -d
bash scripts/run_migrations.sh
bash scripts/dev_backend.sh   # terminal 1
bash scripts/dev_frontend.sh  # terminal 2
```

URLs:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000/api/v1
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/api/v1/health
- DB Health: http://localhost:8000/api/v1/health/db
- Metrics: http://localhost:8000/api/v1/metrics

---

## Production Deployment Summary

1. Build and push Docker images (or let Railway/Render build from repo)
2. Set all environment variables on the platform
3. Run `alembic upgrade head` as a pre-deploy step
4. Start backend: `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
5. Start frontend: `npm start` (in `frontend/`)

See `doc/DEPLOYMENT.md` for provider-specific instructions.

---

## What Was NOT Changed

- CLI generator (`python -m tailor`) — unchanged and fully functional
- `tests/run_assess.sh`, `tests/run_calibrate.sh`, `tests/run_calibrate_samples.sh` — untouched
- All existing backend service/model/schema code — untouched
- All existing frontend pages/components — untouched
- All existing tests — continue to pass
