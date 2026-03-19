from pathlib import Path
import sys

from werkzeug.security import check_password_hash

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import config_manager


def test_read_env_prefers_local_file_outside_azure(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("AZURE_FUNCTION_APP_NAME=file-app\n")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)
    monkeypatch.setenv("AZURE_FUNCTION_APP_NAME", "env-app")

    values = config_manager.read_env()

    assert values["AZURE_FUNCTION_APP_NAME"] == "file-app"


def test_read_env_prefers_process_env_on_azure(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("AZURE_FUNCTION_APP_NAME=file-app\n")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "hosted-dashboard")
    monkeypatch.setenv("AZURE_FUNCTION_APP_NAME", "env-app")

    values = config_manager.read_env()

    assert values["AZURE_FUNCTION_APP_NAME"] == "env-app"


def test_get_azure_settings_exposes_dashboard_hosting_fields(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "AZURE_FUNCTION_APP_NAME=crassus-test\n"
        "AZURE_DASHBOARD_APP_NAME=crassus-test-dashboard\n"
        "AZURE_DASHBOARD_PLAN_NAME=crassus-test-dashboard-plan\n"
        "AZURE_DASHBOARD_SKU=P1V3\n"
    )

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    settings = config_manager.get_azure_settings()

    assert settings["function_base_url"] == "https://crassus-test.azurewebsites.net"
    assert settings["dashboard_app_name"] == "crassus-test-dashboard"
    assert settings["dashboard_plan_name"] == "crassus-test-dashboard-plan"
    assert settings["dashboard_sku"] == "P1V3"


def test_get_azure_settings_defaults_key_vault_name_from_storage_account(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("AZURE_STORAGE_ACCOUNT=crassusstg03121938\n")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    settings = config_manager.get_azure_settings()

    assert settings["use_key_vault"] is True
    assert settings["key_vault_name"] == "crassusstg03121938kv"
    assert settings["key_vault_uri"] == "https://crassusstg03121938kv.vault.azure.net"


def test_prepare_azure_app_settings_uses_key_vault_references_for_secrets():
    settings = {
        "use_key_vault": True,
        "key_vault_name": "crassusvault",
        "key_vault_uri": "https://crassusvault.vault.azure.net",
        "key_vault_secret_prefix": "crassus-prod",
    }

    app_updates, secret_updates = config_manager.prepare_azure_app_settings(
        settings,
        {
            "ALPACA_API_KEY": "api-key",
            "WEBHOOK_FORWARD_TARGET": "azure",
        },
    )

    assert secret_updates == {"ALPACA_API_KEY": "api-key"}
    assert app_updates["WEBHOOK_FORWARD_TARGET"] == "azure"
    assert app_updates["ALPACA_API_KEY"] == (
        "@Microsoft.KeyVault("
        "SecretUri=https://crassusvault.vault.azure.net/secrets/crassus-prod-alpaca-api-key"
        ")"
    )


def test_prepare_azure_app_settings_hashes_plain_dashboard_password():
    settings = {
        "use_key_vault": False,
        "key_vault_name": "",
        "key_vault_uri": "",
        "key_vault_secret_prefix": "crassus",
    }

    app_updates, secret_updates = config_manager.prepare_azure_app_settings(
        settings,
        {"DASHBOARD_ACCESS_PASSWORD": "letmein"},
    )

    assert secret_updates == {}
    assert app_updates["DASHBOARD_ACCESS_PASSWORD"] == ""
    assert "DASHBOARD_ACCESS_PASSWORD_HASH" in app_updates
    assert check_password_hash(app_updates["DASHBOARD_ACCESS_PASSWORD_HASH"], "letmein")
