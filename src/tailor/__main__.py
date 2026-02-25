import os
import sys

# When invoked as `python -m tailor` from inside the src/tailor/ directory,
# Python inserts that directory as sys.path[0].  This causes `from docx import
# Document` inside tailor/docx/ to resolve to the local subpackage instead of
# the third-party python-docx library.  Remove any sys.path entry that points
# at this package directory before importing anything else.
_this_dir = os.path.normcase(os.path.abspath(os.path.dirname(__file__)))
sys.path = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _this_dir]

from tailor.cli import main

main()
