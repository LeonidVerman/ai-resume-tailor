# Makefile — ai-resume-tailor

.PHONY: help install test smoke calibrate assess \
        dev-backend dev-frontend migrate seed \
        docker-lo docker-backend docker-frontend up clean

# ── Defaults ──────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "ai-resume-tailor — available targets"
	@echo ""
	@echo "  CLI generator:"
	@echo "    make install         Install Python package (editable)"
	@echo "    make test            Run full pytest suite"
	@echo "    make smoke           Run smoke tests only"
	@echo "    make calibrate       Run calibration script"
	@echo "    make assess          Run assessment script"
	@echo ""
	@echo "  SaaS development:"
	@echo "    make up              Start full stack (docker-compose up)"
	@echo "    make dev-backend     Start FastAPI backend dev server"
	@echo "    make dev-frontend    Start Next.js frontend dev server"
	@echo "    make migrate         Run Alembic database migrations"
	@echo "    make seed            Seed dev database with sample data"
	@echo ""
	@echo "  Docker:"
	@echo "    make docker-backend  Build backend Docker image"
	@echo "    make docker-frontend Build frontend Docker image"
	@echo "    make docker-lo       Build LibreOffice Docker image (legacy)"
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

# ── SaaS Development ──────────────────────────────────────────────────────

up:
	docker-compose up

dev-backend:
	bash scripts/dev_backend.sh

dev-frontend:
	bash scripts/dev_frontend.sh

migrate:
	bash scripts/run_migrations.sh

seed:
	python scripts/seed_dev_data.py

# ── Docker ────────────────────────────────────────────────────────────────

docker-backend:
	docker build -f backend/Dockerfile -t ai-resume-tailor-backend .

docker-frontend:
	docker build -f frontend/Dockerfile -t ai-resume-tailor-frontend ./frontend

docker-lo:
	docker build -f Dockerfile.libreoffice -t ai-resume-tailor-lo .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
