import importlib
from datetime import datetime, timezone


class FakeTastytradeClient:
    def __init__(self, response):
        self.response = response
        self.paths = []

    def _get(self, path, params=None):
        self.paths.append((path, params))
        return self.response


def test_fetch_api_quote_token_uses_tastytrade_quote_token_endpoint():
    module = importlib.import_module("market_data")
    market_data = importlib.reload(module)
    now = datetime(2026, 6, 6, 16, 0, tzinfo=timezone.utc)
    client = FakeTastytradeClient({
        "token": "quote-token-123",
        "dxlink-url": "wss://tasty.dxlink.example",
    })

    token = market_data.fetch_api_quote_token(client, now=now)

    assert client.paths == [("/api-quote-tokens", None)]
    assert token.token == "quote-token-123"
    assert token.dxlink_url == "wss://tasty.dxlink.example"
    assert token.fetched_at == now
    assert token.expires_at.isoformat() == "2026-06-07T16:00:00+00:00"
    assert token.refresh_after.isoformat() == "2026-06-07T15:30:00+00:00"


def test_build_dxlink_subscription_messages_uses_auth_and_feed_subscription():
    module = importlib.import_module("market_data")
    market_data = importlib.reload(module)

    messages = market_data.build_dxlink_subscription_messages(
        token="quote-token-123",
        symbols=["AAPL", "SPY"],
        channel=7,
    )

    assert [message["type"] for message in messages] == [
        "SETUP",
        "AUTH",
        "CHANNEL_REQUEST",
        "FEED_SETUP",
        "FEED_SUBSCRIPTION",
    ]
    assert messages[1]["token"] == "quote-token-123"
    assert messages[2]["channel"] == 7
    assert messages[4]["add"] == [
        {"type": "Quote", "symbol": "AAPL"},
        {"type": "Trade", "symbol": "AAPL"},
        {"type": "Summary", "symbol": "AAPL"},
        {"type": "Quote", "symbol": "SPY"},
        {"type": "Trade", "symbol": "SPY"},
        {"type": "Summary", "symbol": "SPY"},
    ]


def test_normalize_dxlink_quote_and_trade_events():
    module = importlib.import_module("market_data")
    market_data = importlib.reload(module)

    quote = market_data.normalize_market_event({
        "eventType": "Quote",
        "eventSymbol": "AAPL",
        "bidPrice": 189.45,
        "askPrice": 189.5,
        "bidSize": 10,
        "askSize": 12,
        "time": "2026-06-06T16:01:00Z",
    })
    trade = market_data.normalize_market_event({
        "eventType": "Trade",
        "eventSymbol": "AAPL",
        "price": 189.48,
        "size": 100,
        "time": "2026-06-06T16:01:01Z",
    })

    assert quote == {
        "source": "tastytrade_dxlink",
        "event_type": "Quote",
        "symbol": "AAPL",
        "bid": 189.45,
        "ask": 189.5,
        "last": None,
        "bid_size": 10,
        "ask_size": 12,
        "trade_size": None,
        "timestamp": "2026-06-06T16:01:00Z",
        "raw": {
            "eventType": "Quote",
            "eventSymbol": "AAPL",
            "bidPrice": 189.45,
            "askPrice": 189.5,
            "bidSize": 10,
            "askSize": 12,
            "time": "2026-06-06T16:01:00Z",
        },
    }
    assert trade["event_type"] == "Trade"
    assert trade["last"] == 189.48
    assert trade["trade_size"] == 100


def test_quote_cache_summary_marks_quotes_stale(tmp_path, monkeypatch):
    module = importlib.import_module("market_data")
    market_data = importlib.reload(module)
    monkeypatch.setattr(market_data, "LOCAL_STORE", tmp_path / "market_data.json")
    monkeypatch.setenv("AzureWebJobsStorage", "UseDevelopmentStorage=true")
    monkeypatch.setenv("MARKET_DATA_STALE_SECONDS", "60")

    market_data.record_quote({
        "source": "tastytrade_dxlink",
        "event_type": "Quote",
        "symbol": "AAPL",
        "bid": 189.45,
        "ask": 189.5,
        "last": None,
        "timestamp": "2026-06-06T16:01:00+00:00",
    })

    fresh = market_data.get_market_data_summary(now=datetime(2026, 6, 6, 16, 1, 30, tzinfo=timezone.utc))
    stale = market_data.get_market_data_summary(now=datetime(2026, 6, 6, 16, 2, 30, tzinfo=timezone.utc))

    assert fresh["status"] == "ok"
    assert fresh["stale"] is False
    assert fresh["subscribed_symbols"] == ["AAPL"]
    assert stale["status"] == "stale"
    assert stale["stale"] is True
