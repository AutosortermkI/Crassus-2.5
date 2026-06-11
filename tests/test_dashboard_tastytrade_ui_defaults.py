from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_credentials_card_defaults_to_production_oauth_and_dry_run():
    html = (ROOT_DIR / "dashboard" / "templates" / "index.html").read_text()

    assert '<span id="setupTestLabel">OFF</span>' in html
    assert 'id="setupTestToggle" class="toggle"' in html
    assert 'aria-checked="false" aria-label="Cert/Sandbox API"' in html
    assert '<span id="setupDryRunLabel">ON</span>' in html
    assert 'id="setupDryRunToggle" class="toggle active"' in html


def test_dashboard_js_applies_tastytrade_mode_from_config():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "applyTastytradeSetupDefaults(configData)" in js
    assert "TASTYTRADE_IS_TEST" in js
    assert "TASTYTRADE_DRY_RUN" in js


def test_dashboard_js_syncs_landing_account_field_from_config():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "setupAccountNumber" in js
    assert "TASTYTRADE_ACCOUNT_NUMBER" in js
    assert "setSetupInputValue('setupAccountNumber'" in js


def test_dashboard_js_refreshes_config_after_tastytrade_credential_save():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()
    success_block = js[js.index("function submitCredentials()"):js.index("function loadPortfolio()")]

    assert "loadConfig()" in success_block


def test_dashboard_js_reconciles_landing_card_after_settings_save():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()
    success_block = js[js.index("function saveConfig()"):js.index("function init()")]

    assert "mergeConfigUpdates(updates)" in success_block
    assert "applyTastytradeSetupDefaults(configData)" in success_block


def test_dashboard_has_broker_control_center_cards():
    html = (ROOT_DIR / "dashboard" / "templates" / "index.html").read_text()

    assert 'id="brokerControlCenter"' in html
    assert 'id="brokerModeLabels"' in html
    assert 'id="brokerRoutingCard"' in html
    assert 'id="brokerTastytradeCard"' in html
    assert 'id="brokerAlpacaCard"' in html
    assert 'id="brokerSafetyCard"' in html
    assert "Broker Control Center" in html
    assert "Tastytrade Credentials" in html


def test_dashboard_has_dedicated_alpaca_credential_card():
    html = (ROOT_DIR / "dashboard" / "templates" / "index.html").read_text()

    assert "Alpaca Credentials" in html
    assert 'id="alpacaCredentialStatus"' in html
    assert 'id="alpacaApiKey"' in html
    assert 'id="alpacaSecretKey"' in html
    assert 'id="alpacaPaperToggle"' in html
    assert 'id="alpacaConnectBtn"' in html
    assert "submitAlpacaCredentials()" in html


def test_dashboard_js_saves_alpaca_credentials_through_broker_payload():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "function submitAlpacaCredentials()" in js
    assert "broker: 'alpaca'" in js
    assert "api_key: apiKey" in js
    assert "secret_key: secretKey" in js
    assert "ALPACA_PAPER" in js
    assert "applyAlpacaSetupDefaults(configData)" in js
    assert "window.submitAlpacaCredentials = submitAlpacaCredentials" in js


def test_dashboard_js_loads_combined_broker_status():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "fetch('/api/broker/status')" in js
    assert "renderBrokerStatus(data)" in js
    assert "brokerModeLabels" in js


def test_portfolio_tab_has_combined_dashboard_sections():
    html = (ROOT_DIR / "dashboard" / "templates" / "index.html").read_text()

    assert "Crassus Paper Account" in html
    assert "Broker Snapshots" in html
    assert "Market Data" in html
    assert 'id="paperAccountGrid"' in html
    assert 'id="brokerSnapshotsGrid"' in html
    assert 'id="marketDataGrid"' in html
    assert 'id="paperLedgerEvents"' in html


def test_dashboard_js_loads_combined_dashboard_api():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "fetch('/api/dashboard/combined')" in js
    assert "renderCombinedDashboard(data)" in js
    assert "paperAccountGrid" in js
