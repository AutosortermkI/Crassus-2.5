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


def test_unix_deploy_supports_dev_prod_profiles_and_branch_guards():
    script = (ROOT_DIR / "deploy_azure.sh").read_text()

    assert "--env dev" in script
    assert "--env prod" in script
    assert "DEPLOY_ENV" in script
    assert "CURRENT_GIT_BRANCH" in script
    assert "DEPLOYED_GIT_BRANCH" in script
    assert "DEPLOYED_GIT_SHA" in script
    assert "DEPLOYED_AT_UTC" in script
    assert "Shared DEV warning: this deployment replaces whatever branch was previously running in dev." in script
    assert 'Type DEPLOY PROD to continue' in script
    assert 'if [ "$DEPLOY_ENV" = "prod" ] && [ "$CURRENT_GIT_BRANCH" != "main" ]' in script


def test_unix_deploy_resolves_split_app_names_and_routes():
    script = (ROOT_DIR / "deploy_azure.sh").read_text()

    assert "AZURE_DEV_STOCK_FUNCTION_APP_NAME" in script
    assert "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME" in script
    assert "AZURE_DEV_DASHBOARD_APP_NAME" in script
    assert "AZURE_PROD_STOCK_FUNCTION_APP_NAME" in script
    assert "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME" in script
    assert "AZURE_PROD_DASHBOARD_APP_NAME" in script
    assert 'env_default "$(load_env_var "AZURE_PROD_STOCK_FUNCTION_APP_NAME")" "crassus-25"' in script
    assert 'env_default "$(load_env_var "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME")" "crassus-25"' in script
    assert 'env_default "$(load_env_var "AZURE_PROD_DASHBOARD_APP_NAME")" "crassus-25-dashboard"' in script
    assert 'if [ "$STOCK_FUNCTION_APP_NAME" = "$OPTIONS_FUNCTION_APP_NAME" ]' in script
    assert "ACTIVE_TRADE_ENDPOINT=both" in script
    assert "ENABLE_STOCK_TRADING=true" in script
    assert "ENABLE_OPTIONS_TRADING=true" in script
    assert "AzureWebJobsFeatureFlags=EnableWorkerIndexing" in script
    assert "/api/trade-stock?token=" in script
    assert "/api/trade-options?token=" in script


def test_unix_deploy_supports_existing_dashboard_plan_and_quota_preflight():
    script = (ROOT_DIR / "deploy_azure.sh").read_text()

    assert "AZURE_DEV_DASHBOARD_RESOURCE_GROUP" in script
    assert "AZURE_DASHBOARD_PLAN_RESOURCE_GROUP" in script
    assert "dashboard_plan_id" in script
    assert "dashboard_plan_arg" in script
    assert "MSYS_NO_PATHCONV=1 az webapp create" in script
    assert "Dashboard Web App is in QuotaExceeded state" in script


def test_windows_deploy_fails_clearly_for_split_profile_flags_until_parity_exists():
    script = (ROOT_DIR / "deploy_azure.bat").read_text()

    assert "Split dev/prod deployment is currently supported by deploy_azure.sh" in script
    assert "deploy_azure.bat needs parity updates before Windows deployment" in script
