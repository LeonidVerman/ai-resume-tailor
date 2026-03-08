# Operations Guide

This guide explains how to operate ai-resume-tailor SaaS day-to-day.

---

## Run Locally (Development)

### Prerequisites

- Python 3.11+
- Node.js 20+
- Docker + Docker Compose (for PostgreSQL / full stack)

### 1. Clone and configure

```bash
git clone <repo>
cd ai-resume-tailor
cp .env.example .env
# Edit .env and fill in OPENAI_API_KEY, etc.
```

### 2. Start PostgreSQL

```bash
docker-compose up postgres -d
```

### 3. Install backend

```bash
pip install -e backend/
```

### 4. Run migrations

```bash
bash scripts/run_migrations.sh
```

### 5. Start backend

```bash
bash scripts/dev_backend.sh
# API available at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 6. Start frontend

```bash
bash scripts/dev_frontend.sh
# App available at http://localhost:3000
```

### Full stack with Docker Compose

```bash
docker-compose up
```

This starts postgres, backend, and frontend together.

---

## Run Migrations

```bash
# Upgrade to latest
bash scripts/run_migrations.sh

# Show current revision
bash scripts/run_migrations.sh current

# Downgrade one step
bash scripts/run_migrations.sh downgrade -1

# Create a new migration
cd backend && alembic revision --autogenerate -m "describe change"
```

---

## Run Tests

```bash
# All CLI generator tests
python -m pytest tests/ -v

# Backend tests only (requires DB)
bash scripts/run_tests.sh backend

# Fast tests (skip slow)
bash scripts/run_tests.sh fast
```

---

## Deploy

See `doc/DEPLOYMENT.md` for full deployment instructions.

Quick summary:
1. Set all environment variables on your host
2. Run `alembic upgrade head`
3. Start backend with `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
4. Start frontend with `npm start` inside `frontend/`

---

## Inspect Logs

### Local dev

Logs stream to stdout when running `bash scripts/dev_backend.sh`.

Format: `YYYY-MM-DDTHH:MM:SS [LEVEL] module.name: message`

### Docker

```bash
docker logs -f backend
docker logs -f frontend
docker-compose logs -f backend
```

### Railway / Render

Use the platform's log viewer in the dashboard.

---

## Restart Services

### Docker Compose

```bash
docker-compose restart backend
docker-compose restart frontend
docker-compose restart postgres
```

### Docker (standalone)

```bash
docker restart backend
docker restart frontend
```

### Railway / Render

Use the "Restart" button in the platform dashboard, or redeploy.

---

## Troubleshoot Generation Failures

### 1. Check generation run in DB

Query the `generation_runs` table for the failing run:
```sql
SELECT id, status, error_message, started_at, completed_at
FROM generation_runs
WHERE status = 'failed'
ORDER BY started_at DESC
LIMIT 10;
```

### 2. Check backend logs

Look for ERROR level entries around the time of the failure:
```bash
docker logs backend 2>&1 | grep ERROR
```

### 3. Common causes

| Symptom | Likely cause | Fix |
|---|---|---|
| `OPENAI_API_KEY` errors | Key missing/expired | Update env var |
| `DATABASE_URL` errors | DB not running or bad URL | Check postgres and DATABASE_URL |
| `502 Bad Gateway` | Backend not running | Restart backend service |
| Generation stuck in `running` | Worker crashed | Restart backend; incomplete runs auto-fail on startup |
| `validation_error` in logs | LLM output failed validation | Check prompt version; may need repair attempt |

### 4. Manually retry a generation

The API endpoint `POST /api/v1/generations` accepts a `run_id` to retry. Or trigger a new generation from the frontend.

### 5. Test the CLI generator directly

The legacy CLI generator can be used to test the LLM pipeline without the SaaS layer:
```bash
python -m tailor -f --simple -pu <job-url>
```

---

## Health Checks

```bash
# Liveness
curl http://localhost:8000/api/v1/health

# DB readiness
curl http://localhost:8000/api/v1/health/db

# Metrics (in-process counters)
curl http://localhost:8000/api/v1/metrics
```

---

## Useful Make Targets

```bash
make install       # Install CLI generator
make test          # Run CLI tests
make dev-backend   # Start backend dev server
make dev-frontend  # Start frontend dev server
make migrate       # Run DB migrations
```
