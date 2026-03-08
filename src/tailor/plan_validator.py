"""Backward-compatibility shim.

Implementation moved to tailor.core_generation.plan_validator.
All imports from this path continue to work unchanged.
"""

from tailor.core_generation.plan_validator import validate_plan_extended

__all__ = ["validate_plan_extended"]
