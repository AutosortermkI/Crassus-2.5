import importlib
import os
import sys
from pathlib import Path


DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))


def test_import_does_not_load_repo_env_into_process_environment(monkeypatch):
    """Importing the dashboard broker client must not leak .env into os.environ."""
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_KEY_VAULT_NAME", raising=False)
    sys.modules.pop("alpaca_client", None)

    importlib.import_module("alpaca_client")

    assert os.environ.get("ALPACA_API_KEY") is None
    assert os.environ.get("AZURE_KEY_VAULT_NAME") is None
