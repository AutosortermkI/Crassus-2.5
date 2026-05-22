from types import SimpleNamespace

import pytest

from tastytrade_orders import (
    TastytradeAPIError,
    TastytradeBracketParams,
    TastytradeClient,
    TastytradeConfig,
    build_tastytrade_equity_otoco_order,
    get_tastytrade_account_equity,
    resolve_tastytrade_option_symbol,
    validate_tastytrade_buying_power,
    validate_tastytrade_position_limit,
)
from risk import InsufficientBuyingPowerError, MaxPositionsExceededError


def test_build_buy_equity_otoco_payload_uses_tastytrade_bracket_shape():
    params = TastytradeBracketParams(
        symbol="aapl",
        side="buy",
        qty=2,
        entry_price=100.0,
        take_profit_price=101.25,
        stop_price=99.0,
        stop_limit_price=98.75,
    )

    payload = build_tastytrade_equity_otoco_order(params)

    assert payload["type"] == "OTOCO"
    assert payload["trigger-order"]["order-type"] == "Limit"
    assert payload["trigger-order"]["price"] == 100.0
    assert payload["trigger-order"]["price-effect"] == "Debit"
    assert payload["trigger-order"]["time-in-force"] == "Day"
    assert payload["trigger-order"]["legs"] == [
        {
            "instrument-type": "Equity",
            "symbol": "AAPL",
            "quantity": 2,
            "action": "Buy to Open",
        }
    ]
    assert payload["orders"][0]["order-type"] == "Limit"
    assert payload["orders"][0]["price"] == 101.25
    assert payload["orders"][0]["price-effect"] == "Credit"
    assert payload["orders"][0]["legs"][0]["action"] == "Sell to Close"
    assert payload["orders"][1]["order-type"] == "Stop Limit"
    assert payload["orders"][1]["stop-trigger"] == 99.0
    assert payload["orders"][1]["price"] == 98.75
    assert payload["orders"][1]["price-effect"] == "Credit"
    assert payload["orders"][1]["legs"][0]["action"] == "Sell to Close"


def test_build_sell_equity_otoco_payload_reverses_actions_and_price_effects():
    params = TastytradeBracketParams(
        symbol="MSFT",
        side="sell",
        qty=3,
        entry_price=250.0,
        take_profit_price=245.0,
        stop_price=252.0,
        stop_limit_price=252.5,
    )

    payload = build_tastytrade_equity_otoco_order(params)

    assert payload["trigger-order"]["price-effect"] == "Credit"
    assert payload["trigger-order"]["legs"][0]["action"] == "Sell to Open"
    assert payload["orders"][0]["price-effect"] == "Debit"
    assert payload["orders"][0]["legs"][0]["action"] == "Buy to Close"
    assert payload["orders"][1]["price-effect"] == "Debit"
    assert payload["orders"][1]["legs"][0]["action"] == "Buy to Close"


def test_resolve_tastytrade_option_symbol_builds_padded_occ_symbol():
    symbol = resolve_tastytrade_option_symbol(
        {
            "underlying": "aapl",
            "expiration": "2026-01-17",
            "option_type": "call",
            "strike": "190",
        }
    )

    assert symbol == "AAPL  260117C00190000"


def test_tastytrade_client_uses_complex_order_dry_run_path_and_bearer_token():
    session = FakeSession(
        responses=[
            FakeResponse(200, {"access_token": "access-123", "expires_in": 900}),
            FakeResponse(200, {"data": {"order": {"id": "dry-run-ok"}}}),
        ]
    )
    client = TastytradeClient(
        TastytradeConfig(
            account_number="5WT12345",
            client_secret="client-secret",
            refresh_token="refresh-token",
            is_test=True,
        ),
        session=session,
    )

    response = client.place_complex_order({"type": "OTOCO"}, dry_run=True)

    assert response == {"order": {"id": "dry-run-ok"}}
    assert session.posts[0].url == "https://api.cert.tastyworks.com/oauth/token"
    assert session.posts[0].json["grant_type"] == "refresh_token"
    assert session.posts[1].url == (
        "https://api.cert.tastyworks.com/accounts/5WT12345/complex-orders/dry-run"
    )
    assert session.posts[1].headers["Authorization"] == "Bearer access-123"


def test_tastytrade_client_raises_api_error_with_response_detail():
    session = FakeSession(
        responses=[
            FakeResponse(200, {"access_token": "access-123", "expires_in": 900}),
            FakeResponse(422, {"error": {"message": "preflight failed"}}),
        ]
    )
    client = TastytradeClient(
        TastytradeConfig(
            account_number="5WT12345",
            client_secret="client-secret",
            refresh_token="refresh-token",
            is_test=True,
        ),
        session=session,
    )

    with pytest.raises(TastytradeAPIError, match="preflight failed") as excinfo:
        client.place_complex_order({"type": "OTOCO"}, dry_run=False)

    assert excinfo.value.status_code == 422


def test_tastytrade_risk_helpers_use_balance_and_positions(monkeypatch):
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "3")
    client = SimpleNamespace(
        get_balance=lambda: {
            "net-liquidating-value": "10000.50",
            "equity-buying-power": "2500.25",
        },
        get_positions=lambda: [
            {"symbol": "AAPL", "quantity": "1"},
            {"symbol": "MSFT", "quantity": "2"},
        ],
    )

    assert get_tastytrade_account_equity(client) == 10000.50
    assert validate_tastytrade_buying_power(client, 2000.0, "corr") == 2500.25
    assert validate_tastytrade_position_limit(client, "corr") == 2


def test_tastytrade_risk_helpers_reject_insufficient_buying_power_and_position_limit(monkeypatch):
    monkeypatch.setenv("MAX_OPEN_POSITIONS", "2")
    client = SimpleNamespace(
        get_balance=lambda: {"equity-buying-power": "100.00"},
        get_positions=lambda: [{"symbol": "AAPL"}, {"symbol": "MSFT"}],
    )

    with pytest.raises(InsufficientBuyingPowerError):
        validate_tastytrade_buying_power(client, 101.0, "corr")
    with pytest.raises(MaxPositionsExceededError):
        validate_tastytrade_position_limit(client, "corr")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append(SimpleNamespace(url=url, **kwargs))
        return self.responses.pop(0)

    def get(self, url, **kwargs):
        self.gets.append(SimpleNamespace(url=url, **kwargs))
        return self.responses.pop(0)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload
