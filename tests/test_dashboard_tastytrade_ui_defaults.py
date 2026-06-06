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


def test_dashboard_js_loads_combined_broker_status():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "fetch('/api/broker/status')" in js
    assert "renderBrokerStatus(data)" in js
    assert "brokerModeLabels" in js
