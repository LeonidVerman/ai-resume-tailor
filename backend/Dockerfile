# backend/Dockerfile
# Build context: repository root
#
# Build:  docker build -f backend/Dockerfile -t ai-resume-tailor-backend .
# Run:    docker run -p 8000:8000 --env-file backend/.env ai-resume-tailor-backend

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed for psycopg, lxml, xhtml2pdf (pycairo),
# and LibreOffice (used for PDF→DOCX and DOCX→PDF conversion).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    libreoffice \
    # Metrically-compatible MS-font substitutes for LibreOffice headless rendering.
    # Without these, LibreOffice falls back to DejaVu (different glyph widths),
    # causing text reflow and page-count changes on Calibri/Cambria/Arial resumes.
    fonts-crosextra-carlito \
    fonts-crosextra-caladea \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency manifests first (layer cache friendly)
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY pyproject.toml ./pyproject.toml

# Install Python dependencies for the backend
RUN pip install --no-cache-dir -e ./backend

# Install the tailor generator package (no-deps: playwright is declared in
# backend/pyproject.toml and installed with its Chromium binary below)
COPY src/ ./src/
RUN pip install --no-cache-dir -e . --no-deps && \
    pip install --no-cache-dir openai requests curl_cffi pymupdf

# Install Playwright + Chromium for JS-heavy job-page scraping (HiBob, etc.)
# --with-deps installs the required system packages for the headless browser.
RUN playwright install --with-deps chromium

# Copy backend source and all runtime assets tailor.config resolves at runtime
# (BASE_DIR = /app, so prompts/templates/config/profile live at /app/*)
COPY backend/ ./backend/
COPY prompts/ ./prompts/
COPY templates/ ./templates/
COPY config/ ./config/
COPY profile/ ./profile/
COPY benchmark/ ./benchmark/
COPY legal/ ./legal/

EXPOSE 8000

# DATABASE_URL and other secrets are injected at runtime via environment variables.
# Render (and other PaaS) inject a PORT env var; fall back to 8000 for local runs.
# Run Alembic migrations before starting the server (idempotent — no-op if already current).
CMD ["sh", "-c", "python -m alembic -c backend/alembic.ini upgrade head && uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
