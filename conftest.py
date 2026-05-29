"""Root conftest — ensures workspace src/ takes priority over any editable install."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
