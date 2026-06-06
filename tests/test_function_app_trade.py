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
    monkeypatch.setenv("STOCK_WEBHOOK_AUTH_TOKEN", "stock-token")
    monkeypatch.setenv("OPTIONS_WEBHOOK_AUTH_TOKEN", "options-token")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")

    module = importlib.import_module("function_app")
    return importlib.reload(module)


def _make_request(content: str, token: str = "token", route: str = "trade") -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url=f"http://localhost/api/{route}",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": token,
        },
        params={},
        body=json.dumps({"content": content}).encode(),
    )


def _make_json_request(payload: dict, token: str = "token", route: str = "trade") -> func.HttpRequest:
    return func.HttpRequest(
        method="POST",
        url=f"http://localhost/api/{route}",
        headers={
            "Content-Type": "application/json",
            "X-Webhook-Token": token,
        },
        params={},
        body=json.dumps(payload).encode(),
    )


def test_function_app_imports_without_alpaca_credentials(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER", raising=False)
    monkeypatch.setenv("WEBHOOK_AUTH_TOKEN", "token")
    monkeypatch.setenv("STOCK_WEBHOOK_AUTH_TOKEN", "stock-token")
    monkeypatch.setenv("OPTIONS_WEBHOOK_AUTH_TOKEN", "options-token")

    module = importlib.import_module("function_app")
    reloaded = importlib.reload(module)

    assert reloaded.trade_stock
    assert reloaded.trade_options
    assert reloaded.get_stock_broker() == "alpaca"
    assert reloaded.get_options_broker() == "tastytrade"


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
    monkeypatch.setenv("OPTIONS_BROKER", "alpaca")
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
    monkeypatch.setenv("OPTIONS_BROKER", "alpaca")
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


def test_tastytrade_stock_dry_run_skips_live_buying_power_gate(monkeypatch):
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    monkeypatch.setenv("TASTYTRADE_DRY_RUN", "true")
    monkeypatch.setenv("DEFAULT_STOCK_QTY", "2")
    function_module = _reload_function_module(monkeypatch)

    fake_client = SimpleNamespace()
    submit_tastytrade = MagicMock(return_value="tt-dry-run-123")
    buying_power_check = MagicMock(
        side_effect=function_module.InsufficientBuyingPowerError("Insufficient Tastytrade buying power")
    )

    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=fake_client))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "get_tastytrade_account_equity", MagicMock(return_value=50000.0))
    monkeypatch.setattr(function_module, "validate_tastytrade_buying_power", buying_power_check)
    monkeypatch.setattr(function_module, "submit_tastytrade_stock_order", submit_tastytrade)
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
    assert body["order_id"] == "tt-dry-run-123"
    buying_power_check.assert_not_called()
    submit_tastytrade.assert_called_once()


def test_tastytrade_stock_dry_run_records_paper_ledger_lifecycle(monkeypatch):
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    monkeypatch.setenv("TASTYTRADE_DRY_RUN", "true")
    monkeypatch.setenv("DEFAULT_STOCK_QTY", "2")
    function_module = _reload_function_module(monkeypatch)

    fake_client = SimpleNamespace()
    ledger_calls = []
    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=fake_client))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "get_tastytrade_account_equity", MagicMock(return_value=50000.0))
    monkeypatch.setattr(function_module, "validate_tastytrade_buying_power", MagicMock(return_value=25000.0))
    monkeypatch.setattr(function_module, "submit_tastytrade_stock_order", MagicMock(return_value="tt-dry-run-ledger"))
    monkeypatch.setattr(function_module, "check_tastytrade_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)
    monkeypatch.setattr(
        function_module,
        "record_trade_lifecycle",
        lambda **kwargs: ledger_calls.append(kwargs) or [],
    )

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 100.00"
    )

    resp = function_module.trade(req)

    assert resp.status_code == 200
    assert len(ledger_calls) == 1
    call = ledger_calls[0]
    assert call["correlation_id"]
    assert call["parsed"] == {
        "ticker": "AAPL",
        "side": "buy",
        "strategy": "bollinger_mean_reversion",
        "price": 100.0,
        "mode": "stock",
        "volume": None,
        "time": None,
    }
    assert call["execution"]["ok"] is True
    assert call["execution"]["status_code"] == 200
    assert call["execution"]["body"]["broker"] == "tastytrade"
    assert call["execution"]["body"]["dry_run"] is True


def test_paper_ledger_routes_return_events_and_account(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "get_ledger_events", MagicMock(return_value=[
        {"event_id": "event-1", "event_type": "signal_received"},
    ]))
    monkeypatch.setattr(function_module, "get_paper_account", MagicMock(return_value={
        "source": "crassus_paper_ledger",
        "cash": 25000.0,
        "open_positions": [],
    }))

    events_req = func.HttpRequest(
        method="GET",
        url="http://localhost/api/paper-ledger/events",
        headers={"X-Webhook-Token": "token"},
        params={"limit": "5"},
        body=b"",
    )
    account_req = func.HttpRequest(
        method="GET",
        url="http://localhost/api/paper-ledger/account",
        headers={"X-Webhook-Token": "token"},
        params={},
        body=b"",
    )

    events_resp = function_module.paper_ledger_events(events_req)
    account_resp = function_module.paper_ledger_account(account_req)

    assert events_resp.status_code == 200
    assert json.loads(events_resp.get_body())["events"][0]["event_id"] == "event-1"
    assert account_resp.status_code == 200
    assert json.loads(account_resp.get_body())["account"]["source"] == "crassus_paper_ledger"
    function_module.get_ledger_events.assert_called_once_with(limit=5)


def test_tastytrade_stock_live_mode_enforces_buying_power_gate(monkeypatch):
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    monkeypatch.setenv("TASTYTRADE_DRY_RUN", "false")
    monkeypatch.setenv("DEFAULT_STOCK_QTY", "2")
    function_module = _reload_function_module(monkeypatch)

    fake_client = SimpleNamespace()
    submit_tastytrade = MagicMock(return_value="tt-live-123")
    buying_power_check = MagicMock(
        side_effect=function_module.InsufficientBuyingPowerError("Insufficient Tastytrade buying power")
    )

    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=fake_client))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "get_tastytrade_account_equity", MagicMock(return_value=50000.0))
    monkeypatch.setattr(function_module, "validate_tastytrade_buying_power", buying_power_check)
    monkeypatch.setattr(function_module, "submit_tastytrade_stock_order", submit_tastytrade)
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

    assert resp.status_code == 422
    assert body["broker"] == "tastytrade"
    assert "Insufficient Tastytrade buying power" in body["error"]
    buying_power_check.assert_called_once()
    submit_tastytrade.assert_not_called()


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


def test_tastytrade_options_route_submits_explicit_contract_dry_run(monkeypatch):
    monkeypatch.setenv("OPTIONS_BROKER", "tastytrade")
    monkeypatch.setenv("ENABLE_TASTYTRADE_OPTIONS", "true")
    monkeypatch.setenv("TASTYTRADE_DRY_RUN", "true")
    function_module = _reload_function_module(monkeypatch)

    submit_tastytrade = MagicMock(return_value="tt-option-dry-run")
    monkeypatch.setattr(function_module, "check_tastytrade_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "check_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=object()))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "validate_tastytrade_buying_power", MagicMock())
    monkeypatch.setattr(function_module, "submit_tastytrade_option_order", submit_tastytrade)
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_json_request(
        {
            "ticker": "AAPL",
            "side": "buy",
            "strategy": "lorentzian_classification",
            "mode": "options",
            "price": "189.50",
            "option_symbol": "AAPL  260117C00190000",
            "option_price": "1.00",
        },
        token="options-token",
        route="trade-options",
    )

    resp = function_module.trade_options(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 200
    assert body["status"] == "ok"
    assert body["broker"] == "tastytrade"
    assert body["dry_run"] is True
    assert body["contract"] == "AAPL  260117C00190000"
    assert body["premium"] == 1.0
    assert body["take_profit"] == 1.5
    assert body["stop_loss"] == 0.6
    assert submit_tastytrade.call_args.args[1].option_symbol == "AAPL  260117C00190000"
    assert submit_tastytrade.call_args.args[1].qty == 1


def test_tastytrade_options_route_requires_explicit_contract(monkeypatch):
    monkeypatch.setenv("OPTIONS_BROKER", "tastytrade")
    monkeypatch.setenv("ENABLE_TASTYTRADE_OPTIONS", "true")
    function_module = _reload_function_module(monkeypatch)

    monkeypatch.setattr(function_module, "check_tastytrade_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "check_trading_safety", MagicMock(return_value=True))
    monkeypatch.setattr(function_module, "get_tastytrade_client", MagicMock(return_value=object()))
    monkeypatch.setattr(function_module, "validate_tastytrade_position_limit", MagicMock(return_value=0))
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_json_request(
        {
            "ticker": "AAPL",
            "side": "buy",
            "strategy": "lorentzian_classification",
            "mode": "options",
            "price": "189.50",
        },
        token="options-token",
        route="trade-options",
    )

    resp = function_module.trade_options(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 422
    assert body["broker"] == "tastytrade"
    assert "Option symbol requires" in body["error"]


def test_broker_helpers_use_split_vars_and_legacy_fallback(monkeypatch):
    monkeypatch.delenv("STOCK_BROKER", raising=False)
    monkeypatch.delenv("OPTIONS_BROKER", raising=False)
    monkeypatch.setenv("ORDER_BROKER", "tastytrade")
    function_module = _reload_function_module(monkeypatch)

    assert function_module.get_stock_broker() == "tastytrade"
    assert function_module.get_options_broker() == "tastytrade"

    monkeypatch.setenv("STOCK_BROKER", "alpaca")
    monkeypatch.setenv("OPTIONS_BROKER", "tastytrade")
    assert function_module.get_stock_broker() == "alpaca"
    assert function_module.get_options_broker() == "tastytrade"

    monkeypatch.setenv("STOCK_BROKER", "not-a-broker")
    try:
        function_module.get_stock_broker()
    except ValueError as exc:
        assert "STOCK_BROKER" in str(exc)
    else:
        raise AssertionError("invalid stock broker should fail closed")


def test_trade_stock_route_accepts_stock_signal_with_stock_token(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "_route_stock_order", MagicMock(return_value=(
        {"status": "ok", "route": "trade-stock", "broker": "alpaca", "symbol": "AAPL", "side": "buy", "qty": 1},
        200,
    )))
    monkeypatch.setattr(function_module, "_run_common_preflight", MagicMock())
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 189.50",
        token="stock-token",
        route="trade-stock",
    )

    resp = function_module.trade_stock(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 200
    assert body["route"] == "trade-stock"
    assert body["broker"] == "alpaca"
    function_module._route_stock_order.assert_called_once()


def test_trade_stock_route_rejects_explicit_options_signal(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: options\n"
        "Price: 189.50",
        token="stock-token",
        route="trade-stock",
    )

    resp = function_module.trade_stock(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 400
    assert "options" in body["error"].lower()


def test_trade_stock_tastytrade_api_error_includes_broker_detail(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "_run_common_preflight", MagicMock())
    monkeypatch.setattr(function_module, "_route_stock_order", MagicMock(side_effect=function_module.TastytradeAPIError(
        "One or more preflight checks failed",
        status_code=422,
        response_body={
            "error": {
                "message": "One or more preflight checks failed",
                "errors": [
                    {
                        "code": "preflight_check_failed",
                        "message": "Buying power is insufficient for this order",
                    }
                ],
            }
        },
    )))
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 189.50",
        token="stock-token",
        route="trade-stock",
    )

    resp = function_module.trade_stock(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 502
    assert body["broker"] == "tastytrade"
    assert body["broker_status_code"] == 422
    assert "Buying power is insufficient for this order" in body["broker_error_details"]


def test_trade_options_route_rejects_explicit_stock_signal(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 189.50",
        token="options-token",
        route="trade-options",
    )

    resp = function_module.trade_options(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 400
    assert "stock" in body["error"].lower()


def test_trade_options_tastytrade_disabled_fails_safe(monkeypatch):
    monkeypatch.setenv("OPTIONS_BROKER", "tastytrade")
    monkeypatch.setenv("ENABLE_TASTYTRADE_OPTIONS", "false")
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "_run_common_preflight", MagicMock())
    monkeypatch.setattr(function_module, "screen_option_contracts", MagicMock(side_effect=AssertionError("Alpaca options path used")))
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: options\n"
        "Price: 189.50",
        token="options-token",
        route="trade-options",
    )

    resp = function_module.trade_options(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 501
    assert body["route"] == "trade-options"
    assert body["broker"] == "tastytrade"
    assert body["enabled"] is False
    assert "disabled until contract-symbol routing is verified" in body["error"]


def test_legacy_trade_routes_by_signal_mode_and_warns(monkeypatch):
    function_module = _reload_function_module(monkeypatch)
    monkeypatch.setattr(function_module, "is_duplicate_signal", lambda **kwargs: False)
    monkeypatch.setattr(function_module, "_run_common_preflight", MagicMock())
    monkeypatch.setattr(function_module, "_route_stock_order", MagicMock(return_value=(
        {"status": "ok", "route": "trade-stock", "broker": "alpaca", "symbol": "AAPL", "side": "buy", "qty": 1},
        200,
    )))
    monkeypatch.setattr(function_module, "record_webhook_event", lambda event: None)

    req = _make_request(
        "**New Buy Signal:**\n"
        "AAPL 5 Min Candle\n"
        "Strategy: bollinger_mean_reversion\n"
        "Mode: stock\n"
        "Price: 189.50",
        token="token",
        route="trade",
    )

    resp = function_module.trade(req)
    body = json.loads(resp.get_body())

    assert resp.status_code == 200
    assert body["route"] == "trade-stock"
    assert "legacy_warning" in body
    assert "/api/trade-stock" in body["legacy_warning"]


def test_broker_selection_is_read_per_request(monkeypatch):
    monkeypatch.setenv("STOCK_BROKER", "alpaca")
    function_module = _reload_function_module(monkeypatch)
    assert function_module.get_stock_broker() == "alpaca"

    monkeypatch.setenv("STOCK_BROKER", "tastytrade")
    assert function_module.get_stock_broker() == "tastytrade"
