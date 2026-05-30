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


def test_prepare_azure_app_settings_treats_tastytrade_tokens_as_key_vault_secrets():
    settings = {
        "use_key_vault": True,
        "key_vault_name": "crassusvault",
        "key_vault_uri": "https://crassusvault.vault.azure.net",
        "key_vault_secret_prefix": "crassus-prod",
    }

    app_updates, secret_updates = config_manager.prepare_azure_app_settings(
        settings,
        {
            "TASTYTRADE_CLIENT_SECRET": "client-secret",
            "TASTYTRADE_REFRESH_TOKEN": "refresh-token",
            "TASTYTRADE_ACCOUNT_NUMBER": "5WT12345",
        },
    )

    assert secret_updates == {
        "TASTYTRADE_CLIENT_SECRET": "client-secret",
        "TASTYTRADE_REFRESH_TOKEN": "refresh-token",
    }
    assert app_updates["TASTYTRADE_ACCOUNT_NUMBER"] == "5WT12345"
    assert app_updates["TASTYTRADE_CLIENT_SECRET"] == (
        "@Microsoft.KeyVault("
        "SecretUri=https://crassusvault.vault.azure.net/secrets/crassus-prod-tastytrade-client-secret"
        ")"
    )
    assert app_updates["TASTYTRADE_REFRESH_TOKEN"] == (
        "@Microsoft.KeyVault("
        "SecretUri=https://crassusvault.vault.azure.net/secrets/crassus-prod-tastytrade-refresh-token"
        ")"
    )


def test_save_tastytrade_credentials_writes_secret_and_nonsecret_fields(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("ORDER_BROKER=alpaca\n")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)

    config_manager.save_tastytrade_credentials(
        account_number="5WT12345",
        client_secret="client-secret",
        refresh_token="refresh-token",
        is_test=True,
        dry_run=True,
    )

    saved = env_path.read_text()
    assert "ORDER_BROKER=tastytrade" in saved
    assert "TASTYTRADE_ACCOUNT_NUMBER=5WT12345" in saved
    assert "TASTYTRADE_CLIENT_SECRET=client-secret" in saved
    assert "TASTYTRADE_REFRESH_TOKEN=refresh-token" in saved
    assert "TASTYTRADE_IS_TEST=true" in saved
    assert "TASTYTRADE_DRY_RUN=true" in saved


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


def test_get_config_exposes_split_broker_routing_defaults(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.delenv("WEBSITE_SITE_NAME", raising=False)

    config = config_manager.get_config()

    assert config["ENVIRONMENT_NAME"]["value"] == "dev"
    assert config["STOCK_BROKER"]["value"] == "alpaca"
    assert config["OPTIONS_BROKER"]["value"] == "tastytrade"
    assert config["ENABLE_TASTYTRADE_OPTIONS"]["value"] == "false"


def test_resolve_broker_sync_targets_uses_dev_apps_for_dev_dashboard(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "AZURE_RESOURCE_GROUP=CRG\n"
        "AZURE_DEV_STOCK_FUNCTION_APP_NAME=dev-stock\n"
        "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME=dev-options\n"
        "AZURE_DEV_DASHBOARD_APP_NAME=dev-dashboard\n"
        "AZURE_PROD_STOCK_FUNCTION_APP_NAME=prod-stock\n"
        "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME=prod-options\n"
        "AZURE_PROD_DASHBOARD_APP_NAME=prod-dashboard\n"
    )

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    targets = config_manager.resolve_broker_sync_targets()

    assert targets["environment"] == "dev"
    assert targets["stock_function"] == "dev-stock"
    assert targets["options_function"] == "dev-options"
    assert targets["dashboard"] == "dev-dashboard"
    assert "prod" not in " ".join(targets.values())


def test_resolve_broker_sync_targets_allows_dashboard_resource_group_override(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=dev\n"
        "AZURE_RESOURCE_GROUP=CRG\n"
        "AZURE_DEV_DASHBOARD_RESOURCE_GROUP=CRG-staging\n"
        "AZURE_DEV_STOCK_FUNCTION_APP_NAME=dev-stock\n"
        "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME=dev-options\n"
        "AZURE_DEV_DASHBOARD_APP_NAME=dev-dashboard\n"
    )

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    targets = config_manager.resolve_broker_sync_targets()

    assert targets["resource_group"] == "CRG"
    assert targets["dashboard_resource_group"] == "CRG-staging"


def test_read_env_includes_deployed_metadata_from_host_env(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("")

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "hosted-dashboard")
    monkeypatch.setenv("DEPLOYED_GIT_BRANCH", "jeremy/split-stock-options-routing")
    monkeypatch.setenv("DEPLOYED_GIT_SHA", "abc123")
    monkeypatch.setenv("DEPLOYED_AT_UTC", "2026-05-30T02:00:00+00:00")

    values = config_manager.read_env()

    assert values["DEPLOYED_GIT_BRANCH"] == "jeremy/split-stock-options-routing"
    assert values["DEPLOYED_GIT_SHA"] == "abc123"
    assert values["DEPLOYED_AT_UTC"] == "2026-05-30T02:00:00+00:00"


def test_resolve_broker_sync_targets_uses_prod_apps_for_prod_dashboard(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENVIRONMENT_NAME=prod\n"
        "AZURE_RESOURCE_GROUP=CRG\n"
        "AZURE_DEV_STOCK_FUNCTION_APP_NAME=dev-stock\n"
        "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME=dev-options\n"
        "AZURE_DEV_DASHBOARD_APP_NAME=dev-dashboard\n"
        "AZURE_PROD_STOCK_FUNCTION_APP_NAME=prod-stock\n"
        "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME=prod-options\n"
        "AZURE_PROD_DASHBOARD_APP_NAME=prod-dashboard\n"
    )

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    targets = config_manager.resolve_broker_sync_targets()

    assert targets["environment"] == "prod"
    assert targets["stock_function"] == "prod-stock"
    assert targets["options_function"] == "prod-options"
    assert targets["dashboard"] == "prod-dashboard"
    assert "dev" not in " ".join(targets.values())
