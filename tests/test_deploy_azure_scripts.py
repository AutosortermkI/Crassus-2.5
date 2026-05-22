from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_unix_deploy_allows_dashboard_first_tastytrade_setup():
    script = (ROOT_DIR / "deploy_azure.sh").read_text()

    assert 'touch "$ENV_FILE"' in script
    assert "Tastytrade credentials are not set locally" in script
    assert 'upsert_env_var "TASTYTRADE_IS_TEST" "$TASTYTRADE_IS_TEST"' in script
    assert 'upsert_env_var "TASTYTRADE_DRY_RUN" "$TASTYTRADE_DRY_RUN"' in script
    assert (
        "TASTYTRADE_ACCOUNT_NUMBER, TASTYTRADE_CLIENT_SECRET, "
        "and TASTYTRADE_REFRESH_TOKEN must be set in .env"
    ) not in script


def test_windows_deploy_allows_dashboard_first_tastytrade_setup():
    script = (ROOT_DIR / "deploy_azure.bat").read_text()

    assert 'type nul > "%ENV_FILE%"' in script
    assert "Tastytrade credentials are not set locally" in script
    assert "call :upsert_env_var TASTYTRADE_IS_TEST !TASTYTRADE_IS_TEST!" in script
    assert "call :upsert_env_var TASTYTRADE_DRY_RUN !TASTYTRADE_DRY_RUN!" in script
    assert (
        "TASTYTRADE_ACCOUNT_NUMBER, TASTYTRADE_CLIENT_SECRET, "
        "and TASTYTRADE_REFRESH_TOKEN must be set in .env"
    ) not in script
