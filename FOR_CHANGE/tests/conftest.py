# tests/conftest.py
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]      # E:\...\ai
APP_ROOT = ROOT / "app"                         # E:\...\ai\app

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP_ROOT))