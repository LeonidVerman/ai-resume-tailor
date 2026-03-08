"""Backward-compatibility shim — tailor.llm is an alias for tailor.core_generation.llm.

Replacing this module in sys.modules with the implementation module ensures that:
  - All existing 'from tailor.llm import X' statements work unchanged.
  - unittest.mock.patch('tailor.llm.X') patches the attribute used by the
    implementation code, so all existing tests continue to work.
"""

import sys as _sys
import tailor.core_generation.llm as _impl

# Make tailor.llm point to the real module object so patches work correctly.
_sys.modules[__name__] = _impl
