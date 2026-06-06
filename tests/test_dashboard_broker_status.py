import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock


DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import config_manager


def _load_app_module(module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, DASHBOARD_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_broker_status_separates_tastytrade_alpaca_routing_and_safety(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=prod\n"
        "STOCK_BROKER=tastytrade\n"
        "OPTIONS_BROKER=tastytrade\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_PROD_STOCK_FUNCTION_BASE_URL=https://stock.example.test\n"
        "AZURE_PROD_OPTIONS_FUNCTION_BASE_URL=https://options.example.test\n"
        "DEPLOYED_GIT_BRANCH=main\n"
        "DEPLOYED_GIT_SHA=abc123\n"
        "TASTYTRADE_ACCOUNT_NUMBER=5WT12345\n"
        "TASTYTRADE_CLIENT_SECRET=super-secret\n"
        "TASTYTRADE_REFRESH_TOKEN=refresh-secret\n"
        "TASTYTRADE_IS_TEST=true\n"
        "TASTYTRADE_DRY_RUN=true\n"
        "ENABLE_TASTYTRADE_OPTIONS=true\n"
        "ALPACA_PAPER=true\n"
        "LIVE_TRADING_CONFIRMED=no\n"
        "TRADING_HALTED=false\n"
        "MAX_POSITIONS=3\n"
        "MAX_DOLLARS_PER_TRADE=500\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_broker_status_test")
    monkeypatch.setattr(module, "tt_has_credentials", MagicMock(return_value=True))
    monkeypatch.setattr(
        module,
        "tt_verify_credentials",
        MagicMock(return_value={"ok": True, "account_id": "5WT12345", "paper": True, "dry_run": True}),
    )
    monkeypatch.setattr(module, "has_credentials", MagicMock(return_value=False))
    monkeypatch.setattr(module, "verify_credentials", MagicMock())

    response = module.app.test_client().get("/api/broker/status")
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["routing"]["stock_broker"] == "tastytrade"
    assert body["routing"]["options_broker"] == "tastytrade"
    assert body["routing"]["stock_endpoint"] == "https://stock.example.test/api/trade-stock"
    assert body["routing"]["options_endpoint"] == "https://options.example.test/api/trade-options"
    assert body["routing"]["deployed_git_branch"] == "main"
    assert body["routing"]["deployed_git_sha"] == "abc123"
    assert body["tastytrade"]["configured"] is True
    assert body["tastytrade"]["status"] == "ok"
    assert body["tastytrade"]["account_number"] == "5WT12345"
    assert body["tastytrade"]["is_test"] is True
    assert body["tastytrade"]["dry_run"] is True
    assert body["tastytrade"]["options_enabled"] is True
    assert body["alpaca"]["configured"] is False
    assert body["alpaca"]["status"] == "missing"
    assert body["alpaca"]["role"] == "inactive"
    assert body["safety"]["live_confirmed"] is False
    assert body["safety"]["can_place_live_orders"] is False
    assert body["safety"]["max_positions"] == 3
    assert body["safety"]["max_dollars_per_trade"] == 500.0
    assert body["mode_labels"] == [
        "PROD",
        "TASTYTRADE SANDBOX",
        "DRY RUN",
        "LIVE BLOCKED",
        "OPTIONS ENABLED",
    ]
    assert "super-secret" not in json.dumps(body)
    assert "refresh-secret" not in json.dumps(body)
    module.verify_credentials.assert_not_called()
