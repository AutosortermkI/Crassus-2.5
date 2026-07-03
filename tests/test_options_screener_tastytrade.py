from datetime import date, timedelta

from options_screener import (
    ScreeningCriteria,
    screen_option_contracts,
)
from tastytrade_market_data import TastytradeOptionChain, TastytradeOptionContract


def test_screen_option_contracts_uses_tastytrade_market_data_without_alpaca(monkeypatch):
    expiration = date.today() + timedelta(days=30)
    chain = TastytradeOptionChain(
        underlying="AAPL",
        contracts=[
            TastytradeOptionContract(
                contract_symbol="AAPL  260117C00190000",
                streamer_symbol=".AAPL260117C190",
                option_type="call",
                strike=190.0,
                expiration=expiration,
                last_price=5.20,
                bid=5.10,
                ask=5.30,
                volume=1500,
                open_interest=5000,
                implied_volatility=0.25,
            ),
            TastytradeOptionContract(
                contract_symbol="AAPL  260117C00220000",
                streamer_symbol=".AAPL260117C220",
                option_type="call",
                strike=220.0,
                expiration=expiration,
                last_price=0.15,
                bid=0.10,
                ask=0.20,
                volume=50,
                open_interest=500,
                implied_volatility=0.80,
            ),
        ],
    )
    tastytrade_client = object()

    monkeypatch.setenv("OPTIONS_DATA_SOURCE", "tastytrade")
    monkeypatch.setattr(
        "options_screener.get_tastytrade_client",
        lambda: tastytrade_client,
    )
    monkeypatch.setattr(
        "options_screener.get_tastytrade_option_chain",
        lambda client, underlying, correlation_id="": chain,
    )

    class AlpacaClientShouldNotBeUsed:
        def get_option_contracts(self, _request):
            raise AssertionError("Alpaca option data source was used")

    selected = screen_option_contracts(
        client=AlpacaClientShouldNotBeUsed(),
        underlying="AAPL",
        side="buy",
        entry_price=190.0,
        criteria=ScreeningCriteria(
            dte_min=14,
            dte_max=45,
            delta_min=0.30,
            delta_max=0.70,
            min_open_interest=100,
            min_volume=10,
            max_spread_pct=5.0,
            min_price=0.50,
            max_price=50.0,
        ),
        correlation_id="corr",
    )

    assert selected.symbol == "AAPL  260117C00190000"
    assert selected.premium == 5.20
    assert selected.open_interest == 5000
