from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent


def test_selected_template_drives_test_webhook_payload():
    js = (ROOT_DIR / "dashboard" / "static" / "js" / "dashboard.js").read_text()

    assert "let selectedTemplateKey = 'stockBuy';" in js
    assert "selectedTemplateKey = key;" in js
    assert "function sampleWebhookPayload(key)" in js
    assert "body: JSON.stringify({ payload: sampleWebhookPayload(selectedTemplateKey) })" in js
