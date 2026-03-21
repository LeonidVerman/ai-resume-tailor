"""Baseline pointer management for the PDF round-trip evaluator.

The baseline is stored as a JSON file at eval/baseline.json.
It contains a pointer to a promoted run_id and the summary from that run.
Promotion is explicit and separate from running.
"""
from __future__ import annotations

import json
import os
from typing import Any


_BASELINE_FILENAME = "baseline.json"


def baseline_path(eval_dir: str) -> str:
    return os.path.join(eval_dir, _BASELINE_FILENAME)


def load_baseline(eval_dir: str) -> dict | None:
    """Return the baseline dict, or None if no baseline has been promoted."""
    bp = baseline_path(eval_dir)
    if not os.path.exists(bp):
        return None
    with open(bp, encoding="utf-8") as f:
        return json.load(f)


def load_run_summary(runs_dir: str, run_id: str) -> dict | None:
    """Load run_summary.json for a given run_id, or None if not found."""
    path = os.path.join(runs_dir, run_id, "run_summary.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def promote(eval_dir: str, run_id: str, allow_regressions: bool = False) -> None:
    """Promote *run_id* as the new baseline.

    Raises
    ------
    FileNotFoundError
        If the run directory or run_summary.json does not exist.
    RuntimeError
        If the run has regressions vs the current baseline and
        *allow_regressions* is False.
    """
    runs_dir = os.path.join(eval_dir, "runs")
    summary = load_run_summary(runs_dir, run_id)
    if summary is None:
        raise FileNotFoundError(
            f"Run '{run_id}' not found in {runs_dir}. "
            "Run the evaluator first to generate a run summary."
        )

    # Check for regressions (only if a baseline already exists)
    current = load_baseline(eval_dir)
    if current and not allow_regressions:
        regressions = summary.get("regressions", [])
        if regressions:
            raise RuntimeError(
                f"Run '{run_id}' has {len(regressions)} regression(s) vs the current "
                f"baseline ('{current.get('run_id', '?')}'):\n"
                + "\n".join(f"  - {s}" for s in regressions)
                + "\n\nUse --allow-regressions to promote anyway."
            )

    baseline: dict[str, Any] = {
        "run_id": run_id,
        "promoted_from": summary.get("run_id", run_id),
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "total_samples": summary.get("total_samples"),
        "sample_results": summary.get("sample_results", []),
    }

    os.makedirs(eval_dir, exist_ok=True)
    with open(baseline_path(eval_dir), "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    print(f"Baseline promoted: '{run_id}' -> {baseline_path(eval_dir)}")
