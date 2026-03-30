"""
backend/app/api/metrics.py

Simple in-process metrics endpoint.

Exposes lightweight counters for operational observability without
requiring a full Prometheus/Grafana stack.

Usage:
  GET /metrics  — returns JSON counters

Counters are incremented by other parts of the system via:
  from backend.app.api.metrics import increment
  increment("generation_runs_total")
"""

import threading
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

# Thread-safe in-process counters
_lock = threading.Lock()
_counters: dict[str, int] = {
    "generation_runs_total": 0,
    "generation_runs_success": 0,
    "generation_runs_failed": 0,
    "http_requests_total": 0,
}


def increment(counter: str, amount: int = 1) -> None:
    """Increment a named counter.  Creates the counter if it does not exist."""
    with _lock:
        _counters[counter] = _counters.get(counter, 0) + amount


def get_counters() -> dict[str, int]:
    with _lock:
        return dict(_counters)


class MetricsResponse(BaseModel):
    counters: dict[str, int]


@router.get("/metrics", response_model=MetricsResponse)
async def metrics() -> MetricsResponse:
    """Return in-process operational counters."""
    return MetricsResponse(counters=get_counters())
