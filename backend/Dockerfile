# backend/Dockerfile
# Build context: repository root
#
# Build:  docker build -f backend/Dockerfile -t ai-resume-tailor-backend .
# Run:    docker run -p 8000:8000 --env-file backend/.env ai-resume-tailor-backend

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for psycopg and lxml
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifest first (layer cache friendly)
COPY backend/pyproject.toml ./backend/pyproject.toml

# Install Python dependencies
RUN pip install --no-cache-dir -e ./backend

# Copy full repository so backend.app imports resolve
COPY backend/ ./backend/

EXPOSE 8000

# DATABASE_URL and other secrets are injected at runtime via environment variables.
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
