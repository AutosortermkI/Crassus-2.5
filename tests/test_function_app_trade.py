import importlib
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import azure.functions as func


def _reload_function_module(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "secret")
    monkeypatch.setenv("ALPACA_PAPER", "true")
    monkeypatch.setenv("WEBHOOK_AUTH_TOKEN", "token")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")

    module = importlib.import_module("function_app")
    return importlib.reload(module)


def _make_request(content: str, token: str = "token") -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url="http://localhost/api/trade",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": token,
        },
        params={},
        body=json.dumps({"content": content}).encode(),
    )


def test_trade_unknown_strategy_returns_400_and_records_activity(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    recorded_events = []
    monkeypatch.setattr(function_module, "record_webhook_event", recorded_events.append)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: does_not_exist\n"
        "Mode: stock\n"
        "Price: 189.50"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 400
    assert "Unknown strategy 'does_not_exist'" in body["error"]
    assert recorded_events
    assert recorded_events[0]["execution"]["status_code"] == 400
    assert recorded_events[0]["parsed"]["strategy"] == "does_not_exist"


def test_trade_halted_returns_503_and_records_activity(monkeypatch):
    monkeypatch.setenv("TRADING_HALTED", "true")
    monkeypatch.setenv("TRADING_HALTED_REASON", "maintenance")
    function_module = _reload_function_module(monkeypatch)
    recorded_events = []
    monkeypatch.setattr(function_module, "record_webhook_event", recorded_events.append)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 189.50"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 503
    assert "maintenance" in body["error"]
    assert recorded_events
    assert recorded_events[0]["execution"]["status_code"] == 503


def test_options_trade_insufficient_buying_power_returns_422(monkeypatch):
    function_module = _reload_function_module(monkeypatch)

    contract = SimpleNamespace(
        symbol="AAPL260417C00190000",
        premium=5.0,
        strike=190.0,
        expiration=date(2026, 4, 17),
        dte=10,
        contract_type="call",
    )

    position_check = MagicMock(return_value=0)
    buying_power_check = MagicMock(
        side_effect=function_module.InsufficientBuyingPowerError("Insufficient buying power")
    )
    submit_retry = MagicMock()

    monkeypatch.setattr(function_module, "screen_option_contracts", MagicMock(return_value=contract))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "validate_position_limit", position_check)
    monkeypatch.setattr(function_module, "compute_options_qty", MagicMock(return_value=2))
    monkeypatch.setattr(function_module, "validate_buying_power", buying_power_check)
    monkeypatch.setattr(function_module, "submit_with_retry", submit_retry)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: options\n"
        "Price: 189.50"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 422
    assert "Insufficient buying power" in body["error"]
    position_check.assert_called_once()
    buying_power_check.assert_called_once()
    assert buying_power_check.call_args.args[1] == 1000.0
    submit_retry.assert_not_called()


def test_options_trade_uses_retry_wrapper(monkeypatch):
    function_module = _reload_function_module(monkeypatch)

    contract = SimpleNamespace(
        symbol="AAPL260417C00190000",
        premium=4.25,
        strike=190.0,
        expiration=date(2026, 4, 17),
        dte=10,
        contract_type="call",
    )

    submit_entry = MagicMock(return_value="order-123")
    register_target = MagicMock()
    retry_calls = []

    def _retry(fn, correlation_id):
        retry_calls.append(correlation_id)
        return fn()

    monkeypatch.setattr(function_module, "screen_option_contracts", MagicMock(return_value=contract))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "validate_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "compute_options_qty", MagicMock(return_value=1))
    monkeypatch.setattr(function_module, "validate_buying_power", MagicMock(return_value=5000.0))
    monkeypatch.setattr(function_module, "submit_options_entry_order", submit_entry)
    monkeypatch.setattr(function_module, "submit_with_retry", _retry)
    monkeypatch.setattr(function_module, "register_exit_target", register_target)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: options\n"
        "Price: 189.50"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 200
    assert body["order_id"] == "order-123"
    assert retry_calls
    submit_entry.assert_called_once()
    register_target.assert_called_once()


def test_stock_trade_uses_tastytrade_broker_without_alpaca_account_checks(monkeypatch):
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    monkeypatch.setenv("TASTYTRADE_DRY_RUN", "true")
    monkeypatch.setenv("DEFAULT_STOCK_QTY", "2")
    function_module = _reload_function_module(monkeypatch)

    fake_client = SimpleNamespace()
    submit_tastytrade = MagicMock(return_value="tt-order-123")
    alpaca_position_check = MagicMock(side_effect=AssertionError("Alpaca position check used"))
    alpaca_buying_power_check = MagicMock(side_effect=AssertionError("Alpaca buying power check used"))
    alpaca_safety = MagicMock(side_effect=AssertionError("Alpaca safety check used"))

    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=fake_client))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "get_tastytrade_account_equity", MagicMock(return_value=50000.0))
    monkeypatch.setattr(function_module, "validate_tastytrade_buying_power", MagicMock(return_value=25000.0))
    monkeypatch.setattr(function_module, "submit_tastytrade_stock_order", submit_tastytrade)
    monkeypatch.setattr(function_module, "validate_position_limit", alpaca_position_check)
    monkeypatch.setattr(function_module, "validate_buying_power", alpaca_buying_power_check)
    monkeypatch.setattr(function_module, "check_trading_safety", alpaca_safety)
    monkeypatch.setattr(function_module, "check_tastytrade_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 100.00"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 200
    assert body["broker"] == "tastytrade"
    assert body["dry_run"] is True
    assert body["order_id"] == "tt-order-123"
    submit_tastytrade.assert_called_once()
    params = submit_tastytrade.call_args.args[1]
    assert params.symbol == "AAPL"
    assert params.side == "buy"
    assert params.qty == 2
    assert params.entry_price == 100.0


def test_tastytrade_options_mode_returns_controlled_not_implemented(monkeypatch):
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    function_module = _reload_function_module(monkeypatch)

    monkeypatch.setattr(function_module, "check_tastytrade_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "check_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "screen_option_contracts", MagicMock(side_effect=AssertionError("Alpaca options screen used")))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: options\n"
        "Price: 189.50"
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 501
    assert body["broker"] == "tastytrade"
    assert "Tastytrade options routing" in body["error"]
