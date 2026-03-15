import importlib
import json

import azure.functions as func


def _reload_function_module(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("WEBHOOK_AUTH_TOKEN", "token")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")

    module = importlib.import_module("function_app")
    return importlib.reload(module)


def test_trade_unknown_strategy_returns_400_and_records_activity(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    recorded_events = []
    monkeypatch.setattr(function_module, "record_webhook_event", recorded_events.append)

    req = func.HttpRequest(
        method="POST",
        url="http://localhost/api/trade",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": "token",
        },
        params={},
        body=json.dumps({
            "content": (
                "**New Buy Signal:**\n"
                "AAPL 5 Min Candle\n"
                "Strategy: does_not_exist\n"
                "Mode: stock\n"
                "Price: 189.50"
            )
        }).encode(),
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 400
    assert "Unknown strategy 'does_not_exist'" in body["error"]
    assert recorded_events
    assert recorded_events[0]["execution"]["status_code"] == 400
    assert recorded_events[0]["parsed"]["strategy"] == "does_not_exist"
