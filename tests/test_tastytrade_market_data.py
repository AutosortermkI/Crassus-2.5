from datetime import date

from tastytrade_market_data import (
    get_option_chain,
    parse_nested_option_chain,
)


def test_parse_nested_option_chain_keeps_order_symbols_and_streamer_symbols():
    chain = parse_nested_option_chain(_nested_chain_payload(), "AAPL")

    assert chain.underlying == "AAPL"
    assert len(chain.contracts) == 2
    call = chain.contracts[0]
    assert call.contract_symbol == "AAPL  260117C00190000"
    assert call.streamer_symbol == ".AAPL260117C190"
    assert call.option_type == "call"
    assert call.strike == 190.0
    assert call.expiration == date(2026, 1, 17)


def test_get_option_chain_merges_tastytrade_realtime_market_data():
    client = FakeTastytradeMarketDataClient(
        chain_payload=_nested_chain_payload(),
        market_data_payload=[
            {
                "symbol": "AAPL  260117C00190000",
                "bid": 5.10,
                "ask": 5.30,
                "last": 5.20,
                "volume": 1500,
                "open-interest": 5000,
                "implied-volatility": 0.25,
            },
            {
                "symbol": "AAPL  260117P00180000",
                "bid": 4.00,
                "ask": 4.20,
                "last": 4.10,
                "volume": 900,
                "open-interest": 4000,
                "implied-volatility": 0.30,
            },
        ],
    )

    chain = get_option_chain(client, "AAPL")

    assert client.chain_requests == ["AAPL"]
    assert client.market_data_requests == [["AAPL  260117C00190000", "AAPL  260117P00180000"]]
    call = [c for c in chain.contracts if c.option_type == "call"][0]
    assert call.bid == 5.10
    assert call.ask == 5.30
    assert call.last_price == 5.20
    assert call.volume == 1500
    assert call.open_interest == 5000
    assert call.implied_volatility == 0.25


class FakeTastytradeMarketDataClient:
    def __init__(self, chain_payload, market_data_payload):
        self.chain_payload = chain_payload
        self.market_data_payload = market_data_payload
        self.chain_requests = []
        self.market_data_requests = []

    def get_nested_option_chain(self, underlying):
        self.chain_requests.append(underlying)
        return self.chain_payload

    def get_market_data_by_type(self, equity_options=None):
        self.market_data_requests.append(list(equity_options or []))
        return self.market_data_payload


def _nested_chain_payload():
    return {
        "items": [
            {
                "underlying-symbol": "AAPL",
                "root-symbol": "AAPL",
                "option-chain-type": "Standard",
                "shares-per-contract": 100,
                "expirations": [
                    {
                        "expiration-date": "2026-01-17",
                        "days-to-expiration": 225,
                        "strikes": [
                            {
                                "strike-price": "190.0",
                                "call": "AAPL  260117C00190000",
                                "call-streamer-symbol": ".AAPL260117C190",
                                "put": "AAPL  260117P00180000",
                                "put-streamer-symbol": ".AAPL260117P180",
                            }
                        ],
                    }
                ],
            }
        ]
    }
