from pathlib import Path


def test_save_config_toast_surfaces_azure_sync_error_detail():
    script = Path("dashboard/static/js/dashboard.js").read_text(encoding="utf-8")

    assert "showToast(data.message || 'Saved locally. Azure sync needs attention.', 'error')" in script


def test_logs_tab_is_present_with_split_route_controls():
    template = Path("dashboard/templates/index.html").read_text(encoding="utf-8")

    assert 'data-tab="logs"' in template
    assert 'id="panel-logs"' in template
    assert "runDiagnosticsTest('stockBuy')" in template
    assert "runDiagnosticsTest('optionsBuy')" in template
    assert 'id="diagnosticsErrors"' in template


def test_logs_tab_fetches_existing_low_cost_diagnostics_sources():
    script = Path("dashboard/static/js/dashboard.js").read_text(encoding="utf-8")

    assert "function loadDiagnostics()" in script
    assert "['webhookInfo', '/api/webhook/info']" in script
    assert "['brokerStatus', '/api/broker/status']" in script
    assert "['combined', '/api/dashboard/combined']" in script
    assert "['activity', '/api/webhook/activity']" in script
    assert "function runDiagnosticsTest(templateKey)" in script
