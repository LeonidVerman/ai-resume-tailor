# infra/docker/backend.Dockerfile
# Status: PLACEHOLDER — not yet implemented (Phase 11).
# Build context: repository root

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy backend project
COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir -e .

COPY backend/ .

# Expose port
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
