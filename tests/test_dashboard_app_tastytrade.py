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
