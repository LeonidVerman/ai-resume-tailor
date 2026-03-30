# Deployment Guide

This guide explains how to deploy ai-resume-tailor SaaS to various hosting providers.

---

## Required Environment Variables

Set these in your hosting provider's dashboard or `.env` file.

| Variable | Description | Required |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `OPENAI_API_KEY` | OpenAI API key | Yes |
| `SECRET_KEY` | JWT signing secret (generate with `openssl rand -hex 32`) | Yes |
| `APP_ENV` | `development` / `staging` / `production` | Yes |
| `ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins | Yes |
| `SUPABASE_URL` | Supabase project URL (for auth) | Yes |
| `SUPABASE_ANON_KEY` | Supabase anon key | Yes |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key | Yes |
| `STORAGE_ENDPOINT` | S3-compatible endpoint (Cloudflare R2, etc.) | Yes |
| `STORAGE_ACCESS_KEY_ID` | Storage access key | Yes |
| `STORAGE_SECRET_ACCESS_KEY` | Storage secret key | Yes |
| `STORAGE_BUCKET` | Storage bucket name | Yes |
| `STRIPE_SECRET_KEY` | Stripe secret key | Yes |
| `STRIPE_WEBHOOK_SECRET` | Stripe webhook signing secret | Yes |
| `STRIPE_PRICE_ID_STARTER` | Stripe price ID for Starter plan | Yes |
| `STRIPE_PRICE_ID_PRO` | Stripe price ID for Pro plan | Yes |

---

## Deploy on Railway

Railway can run both backend and frontend from the same repo.

### Backend

1. Create a new Railway project and connect your GitHub repository.
2. Add a **PostgreSQL** plugin — Railway will inject `DATABASE_URL` automatically.
3. Set all required environment variables in the Railway dashboard.
4. Railway uses `infra/railway/railway.toml` for build configuration automatically.
5. The service starts with:
   ```
   uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT
   ```

### Frontend

1. Add a second service in the same Railway project.
2. Set `NEXT_PUBLIC_API_URL` to your backend Railway URL (e.g. `https://api.yourapp.railway.app/api/v1`).
3. Set build command: `cd frontend && npm ci && npm run build`
4. Set start command: `cd frontend && npm start`

---

## Deploy on Render

### Backend

1. Create a new **Web Service** on Render.
2. Connect your GitHub repository.
3. Set build command: `pip install -e backend/`
4. Set start command: `uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT`
5. Or use Docker: set **Dockerfile Path** to `backend/Dockerfile`.
6. Add environment variables in the Render dashboard.

### Frontend

1. Create a **Static Site** or **Web Service** on Render.
2. Set build command: `cd frontend && npm ci && npm run build`
3. Set publish directory: `frontend/.next` (for static) or start command `cd frontend && npm start`.

### Database

Use Render's managed PostgreSQL or an external Supabase instance.

---

## Deploy on a Generic Docker Host

### Build images

```bash
# Backend (from repo root)
docker build -f backend/Dockerfile -t ai-resume-tailor-backend .

# Frontend
docker build -f frontend/Dockerfile -t ai-resume-tailor-frontend ./frontend
```

### Run containers

```bash
# Postgres (if not using managed DB)
docker run -d \
  --name postgres \
  -e POSTGRES_USER=tailor \
  -e POSTGRES_PASSWORD=yourpassword \
  -e POSTGRES_DB=tailor \
  -p 5432:5432 \
  postgres:16-alpine

# Backend
docker run -d \
  --name backend \
  -p 8000:8000 \
  --env-file .env \
  ai-resume-tailor-backend

# Frontend
docker run -d \
  --name frontend \
  -p 3000:3000 \
  -e NEXT_PUBLIC_API_URL=https://your-backend-domain/api/v1 \
  ai-resume-tailor-frontend
```

---

## Run Database Migrations

Always run migrations before starting the backend for the first time or after a deployment that includes schema changes.

```bash
# Local
bash scripts/run_migrations.sh

# In Docker
docker exec backend python -m alembic -c backend/alembic.ini upgrade head

# Railway / Render: add as a pre-deploy command
python -m alembic -c backend/alembic.ini upgrade head
```

---

## Health Checks

| Endpoint | Description |
|---|---|
| `GET /api/v1/health` | Liveness — process is running |
| `GET /api/v1/health/db` | Readiness — DB is connected |

Configure your load balancer or platform health check to use `/api/v1/health`.

---

## Startup Sequence

1. Start PostgreSQL
2. Run migrations: `alembic upgrade head`
3. Start backend: `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000`
4. Start frontend: `npm start` (in `frontend/`)
