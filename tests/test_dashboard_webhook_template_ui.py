from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_selected_template_drives_test_webhook_payload():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "let selectedTemplateKey = 'stockBuy';" in js
    assert "selectedTemplateKey = key;" in js
    assert "function sampleWebhookPayload(key)" in js
    assert "body: JSON.stringify({ payload: sampleWebhookPayload(selectedTemplateKey) })" in js


def test_broker_status_labels_paper_and_cert_modes_separately():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "data.broker === 'tastytrade' ? 'Cert/Sandbox' : 'Paper'" in js
    assert "data.broker === 'tastytrade' ? 'Production' : 'Live'" in js
    assert "Dry Run Mode" in js
