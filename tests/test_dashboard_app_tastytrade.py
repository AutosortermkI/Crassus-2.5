import importlib.util
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


def test_credentials_check_uses_tastytrade_when_selected(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORDER_BROKER=tastytrade\n")
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_tastytrade_check_test")
    monkeypatch.setattr(module, "tt_has_credentials", MagicMock(return_value=True))
    monkeypatch.setattr(
        module,
        "tt_verify_credentials",
        MagicMock(return_value={"ok": True, "account_id": "5WT12345", "paper": True, "dry_run": True}),
    )

    response = module.app.test_client().get("/api/credentials/check")
    body = response.get_json()

    assert response.status_code == 200
    assert body["broker"] == "tastytrade"
    assert body["status"] == "ok"
    assert body["account_id"] == "5WT12345"
    assert body["dry_run"] is True


def test_credentials_save_accepts_tastytrade_payload_and_syncs(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORDER_BROKER=tastytrade\n")
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_tastytrade_save_test")
    save_creds = MagicMock()
    sync = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(module, "save_tastytrade_credentials", save_creds)
    monkeypatch.setattr(
        module,
        "tt_verify_credentials_with_values",
        MagicMock(return_value={"ok": True, "account_id": "5WT12345", "paper": True, "dry_run": True}),
    )
    monkeypatch.setattr(module, "sync_settings_to_azure", sync)

    response = module.app.test_client().post(
        "/api/credentials/save",
        json={
            "broker": "tastytrade",
            "account_number": "5WT12345",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "is_test": True,
            "dry_run": True,
        },
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["broker"] == "tastytrade"
    save_creds.assert_called_once_with(
        "5WT12345",
        "client-secret",
        "refresh-token",
        is_test=True,
        dry_run=True,
    )
    sync.assert_called_once()
    assert sync.call_args.args[0]["TASTYTRADE_CLIENT_SECRET"] == "client-secret"


def test_credentials_save_verifies_submitted_tastytrade_values_before_azure_sync(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORDER_BROKER=tastytrade\n")
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_tastytrade_first_setup_test")
    submitted_verify = MagicMock(
        return_value={"ok": True, "account_id": "5WT12345", "paper": True, "dry_run": True}
    )
    legacy_verify = MagicMock(side_effect=AssertionError("should verify submitted credentials directly"))
    monkeypatch.setattr(module, "tt_verify_credentials_with_values", submitted_verify)
    monkeypatch.setattr(module, "tt_verify_credentials", legacy_verify)
    monkeypatch.setattr(module, "save_tastytrade_credentials", MagicMock())
    monkeypatch.setattr(module, "sync_settings_to_azure", MagicMock(return_value={"ok": True}))

    response = module.app.test_client().post(
        "/api/credentials/save",
        json={
            "broker": "tastytrade",
            "account_number": "5WT12345",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "is_test": True,
            "dry_run": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
    submitted_verify.assert_called_once_with(
        account_number="5WT12345",
        client_secret="client-secret",
        refresh_token="refresh-token",
        is_test=True,
    )
    legacy_verify.assert_not_called()


def test_credentials_save_failure_names_tastytrade_api_mode(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORDER_BROKER=tastytrade\n")
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_tastytrade_failure_message_test")
    monkeypatch.setattr(
        module,
        "tt_verify_credentials_with_values",
        MagicMock(return_value={"ok": False, "error": "invalid_grant"}),
    )
    monkeypatch.setattr(module, "save_tastytrade_credentials", MagicMock())
    monkeypatch.setattr(module, "sync_settings_to_azure", MagicMock())

    response = module.app.test_client().post(
        "/api/credentials/save",
        json={
            "broker": "tastytrade",
            "account_number": "5WT12345",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
            "is_test": False,
            "dry_run": True,
        },
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "invalid"
    assert "production" in body["message"].lower()
    assert "invalid_grant" in body["message"]
    assert "saved" not in body["message"].lower()
    module.save_tastytrade_credentials.assert_not_called()
    module.sync_settings_to_azure.assert_not_called()


def test_config_brokers_saves_valid_values_and_syncs_without_live_flags(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "STOCK_BROKER=alpaca\n"
        "OPTIONS_BROKER=tastytrade\n"
        "ALPACA_PAPER=true\n"
        "TASTYTRADE_DRY_RUN=true\n"
        "ENABLE_TASTYTRADE_OPTIONS=false\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_broker_config_save_test")
    sync = MagicMock(return_value={
        "stock_function": "ok",
        "options_function": "ok",
        "dashboard": "ok",
    })
    monkeypatch.setattr(module, "sync_broker_settings_to_azure", sync)

    response = module.app.test_client().post(
        "/api/config/brokers",
        json={"stock_broker": "tastytrade", "options_broker": "alpaca"},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body == {
        "status": "ok",
        "stock_broker": "tastytrade",
        "options_broker": "alpaca",
        "azure_sync": {
            "stock_function": "ok",
            "options_function": "ok",
            "dashboard": "ok",
        },
    }
    saved = env_path.read_text()
    assert "STOCK_BROKER=tastytrade" in saved
    assert "OPTIONS_BROKER=alpaca" in saved
    assert "ALPACA_PAPER=true" in saved
    assert "TASTYTRADE_DRY_RUN=true" in saved
    assert "ENABLE_TASTYTRADE_OPTIONS=false" in saved
    sync.assert_called_once_with({"STOCK_BROKER": "tastytrade", "OPTIONS_BROKER": "alpaca"})


def test_config_brokers_rejects_invalid_values(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ENVIRONMENT_NAME=dev\n")
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_broker_config_invalid_test")
    monkeypatch.setattr(module, "sync_broker_settings_to_azure", MagicMock())

    response = module.app.test_client().post(
        "/api/config/brokers",
        json={"stock_broker": "alpaca", "options_broker": "paperclip"},
    )
    body = response.get_json()

    assert response.status_code == 400
    assert body["status"] == "error"
    assert "options_broker" in body["message"]
    module.sync_broker_settings_to_azure.assert_not_called()


def test_webhook_info_returns_split_stock_and_options_urls(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "WEBHOOK_AUTH_TOKEN=token-123\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=https://dev-stock.azurewebsites.net\n"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=https://dev-options.azurewebsites.net\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_split_webhook_info_test")

    response = module.app.test_client().get("/api/webhook/info")
    body = response.get_json()

    assert response.status_code == 200
    assert body["stock_url"] == "https://dev-stock.azurewebsites.net/api/trade-stock"
    assert body["options_url"] == "https://dev-options.azurewebsites.net/api/trade-options"
    assert body["stock_full_url"] == "https://dev-stock.azurewebsites.net/api/trade-stock?token=token-123"
    assert body["options_full_url"] == "https://dev-options.azurewebsites.net/api/trade-options?token=token-123"
    assert body["forward_urls"] == {
        "stock": "https://dev-stock.azurewebsites.net/api/trade-stock",
        "options": "https://dev-options.azurewebsites.net/api/trade-options",
    }
    assert body["activity_urls"] == {
        "stock": "https://dev-stock.azurewebsites.net/api/webhook-activity",
        "options": "https://dev-options.azurewebsites.net/api/webhook-activity",
    }
    assert body["local_url"] == body["stock_url"]
    assert body["full_url"] == body["stock_full_url"]


def test_dashboard_forward_uses_options_endpoint_for_options_mode(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=https://dev-stock.azurewebsites.net\n"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=https://dev-options.azurewebsites.net\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_split_forward_test")
    response = MagicMock()
    response.status_code = 200
    response.text = "ok"
    response.headers = {"content-type": "text/plain"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.http_requests, "post", post)

    result = module._forward_webhook({"content": "payload"}, "token-123", {"mode": "options"})

    assert result["ok"] is True
    assert result["url"] == "https://dev-options.azurewebsites.net/api/trade-options"
    post.assert_called_once()
    assert post.call_args.args[0] == "https://dev-options.azurewebsites.net/api/trade-options"


def test_dashboard_test_webhook_uses_stock_split_endpoint(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "WEBHOOK_AUTH_TOKEN=token-123\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=https://dev-stock.azurewebsites.net\n"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=https://dev-options.azurewebsites.net\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_split_test_webhook_test")
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"status": "ok", "route": "trade-stock"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.http_requests, "post", post)

    api_response = module.app.test_client().post("/api/webhook/test", json={})
    body = api_response.get_json()

    assert api_response.status_code == 200
    assert body["trade_url"] == "https://dev-stock.azurewebsites.net/api/trade-stock"
    post.assert_called_once()
    assert post.call_args.args[0] == "https://dev-stock.azurewebsites.net/api/trade-stock"


def test_dashboard_test_webhook_uses_options_split_endpoint_for_options_payload(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "WEBHOOK_AUTH_TOKEN=token-123\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=https://dev-stock.azurewebsites.net\n"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=https://dev-options.azurewebsites.net\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_split_test_webhook_options_test")
    response = MagicMock()
    response.status_code = 200
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"status": "ok", "route": "trade-options"}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.http_requests, "post", post)

    api_response = module.app.test_client().post(
        "/api/webhook/test",
        json={
            "payload": {
                "ticker": "AAPL",
                "side": "buy",
                "strategy": "lorentzian_classification",
                "mode": "options",
                "price": "189.50",
            }
        },
    )
    body = api_response.get_json()

    assert api_response.status_code == 200
    assert body["trade_url"] == "https://dev-options.azurewebsites.net/api/trade-options"
    post.assert_called_once()
    assert post.call_args.args[0] == "https://dev-options.azurewebsites.net/api/trade-options"
    assert post.call_args.kwargs["json"]["mode"] == "options"


def test_webhook_activity_merges_split_function_results(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "WEBHOOK_AUTH_TOKEN=token-123\n"
        "WEBHOOK_FORWARD_TARGET=azure\n"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=https://dev-stock.azurewebsites.net\n"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=https://dev-options.azurewebsites.net\n"
    )
    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    module = _load_app_module("dashboard_app_split_activity_test")

    def fake_get(url, **kwargs):
        mode = "options" if "dev-options" in url else "stock"
        body = {
            "latest_event": {
                "id": f"{mode}-latest",
                "received_at": f"2026-05-30T04:0{1 if mode == 'stock' else 2}:00+00:00",
                "parsed": {"ticker": "AAPL", "side": "buy", "strategy": "demo", "mode": mode},
            },
            "recent_events": [
                {
                    "id": f"{mode}-1",
                    "received_at": f"2026-05-30T04:0{1 if mode == 'stock' else 2}:00+00:00",
                    "signature": f"AAPL:buy:demo:{mode}",
                    "parsed": {"ticker": "AAPL", "side": "buy", "strategy": "demo", "mode": mode},
                }
            ],
            "active_webhooks": [
                {
                    "signature": f"AAPL:buy:demo:{mode}",
                    "ticker": "AAPL",
                    "side": "buy",
                    "strategy": "demo",
                    "mode": mode,
                    "last_seen": f"2026-05-30T04:0{1 if mode == 'stock' else 2}:00+00:00",
                    "count": 1,
                }
            ],
            "counts": {"recent_events": 1, "active_webhooks": 1},
        }
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.json.return_value = body
        return response

    monkeypatch.setattr(module.http_requests, "get", fake_get)

    response = module.app.test_client().get("/api/webhook/activity")
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert [event["parsed"]["mode"] for event in body["recent_events"]] == ["options", "stock"]
    assert body["latest_event"]["id"] == "options-1"
    assert body["counts"] == {"recent_events": 2, "active_webhooks": 2}
