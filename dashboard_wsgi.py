"""WSGI entrypoint for the hosted Azure dashboard."""

from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = ROOT_DIR / "dashboard"

if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

from app import app  # noqa: E402
