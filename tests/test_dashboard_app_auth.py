import importlib.util
import sys
from pathlib import Path


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


def test_hosted_login_uses_secure_session_cookie(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DASHBOARD_ACCESS_PASSWORD_HASH="
        f"{config_manager.generate_dashboard_password_hash('teammode')}\n"
    )

    monkeypatch.setattr(config_manager, "ENV_PATH", env_path)
    monkeypatch.setenv("WEBSITE_SITE_NAME", "crassus-dashboard-test")

    module = _load_app_module("dashboard_app_auth_test")
    client = module.app.test_client()

    response = client.post("/login", data={"password": "teammode"}, follow_redirects=False)

    assert response.status_code == 302
    cookie_header = response.headers["Set-Cookie"]
    assert "crassus_dashboard_session=" in cookie_header
    assert "Secure" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=Lax" in cookie_header
