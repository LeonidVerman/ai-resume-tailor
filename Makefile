# Makefile — ai-resume-tailor
#
# Current targets:  CLI generator install, test, calibration
# Future targets:   backend dev server, frontend dev server, migrations

.PHONY: help install test smoke calibrate assess \
        dev-backend dev-frontend migrate seed \
        docker-lo clean

# ── Defaults ──────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "ai-resume-tailor — available targets"
	@echo ""
	@echo "  Current CLI generator:"
	@echo "    make install       Install Python package (editable)"
	@echo "    make test          Run full pytest suite"
	@echo "    make smoke         Run smoke tests only"
	@echo "    make calibrate     Run calibration script"
	@echo "    make assess        Run assessment script"
	@echo "    make docker-lo     Build LibreOffice Docker image"
	@echo ""
	@echo "  Future SaaS (not yet implemented):"
	@echo "    make dev-backend   Start FastAPI backend dev server"
	@echo "    make dev-frontend  Start Next.js frontend dev server"
	@echo "    make migrate       Run Alembic database migrations"
	@echo "    make seed          Seed dev database with sample data"
	@echo ""

# ── CLI Generator ─────────────────────────────────────────────────────────

install:
	pip install -e .

test:
	pytest tests/

smoke:
	pytest tests/test_smoke.py -v

calibrate:
	bash tests/run_calibrate.sh

assess:
	bash tests/run_assess.sh

docker-lo:
	docker build -f Dockerfile.libreoffice -t ai-resume-tailor-lo .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

# ── Future SaaS (scaffolded — not yet implemented) ────────────────────────

dev-backend:
	@echo "[future] Starting FastAPI backend..."
	@echo "Run: bash scripts/dev_backend.sh"

dev-frontend:
	@echo "[future] Starting Next.js frontend..."
	@echo "Run: bash scripts/dev_frontend.sh"

migrate:
	@echo "[future] Running Alembic migrations..."
	@echo "Run: bash scripts/run_migrations.sh"

seed:
	@echo "[future] Seeding dev database..."
	@echo "Run: python scripts/seed_dev_data.py"
