from pathlib import Path
import sys


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
