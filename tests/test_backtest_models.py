"""
Tests for backtesting data models.
"""

import pytest
from datetime import datetime

from backtesting.models import (
    Bar,
    Signal,
    Order,
    OrderType,
    OrderStatus,
    Position,
    PositionStatus,
    Trade,
    BacktestConfig,
    BacktestResult,
)


# ---------------------------------------------------------------------------
# Bar
# ---------------------------------------------------------------------------

class TestBar:
    def test_creation(self):
        bar = Bar(
            timestamp=datetime(2024, 1, 2, 9, 30),
            open=150.0, high=151.0, low=149.0, close=150.5,
            volume=1_000_000, ticker="AAPL",
        )
        assert bar.open == 150.0
        assert bar.ticker == "AAPL"

    def test_frozen(self):
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=100.0, high=101.0, low=99.0, close=100.5, volume=500,
        )
        with pytest.raises(AttributeError):
            bar.open = 200.0

    def test_default_ticker(self):
        bar = Bar(
            timestamp=datetime(2024, 1, 2),
            open=100.0, high=101.0, low=99.0, close=100.5, volume=500,
        )
        assert bar.ticker == ""


# ---------------------------------------------------------------------------
# Signal
# ---------------------------------------------------------------------------

class TestSignal:
    def test_creation(self):
        sig = Signal(
            timestamp=datetime(2024, 1, 5, 10, 0),
            ticker="AAPL", side="buy", price=150.25,
            strategy="bollinger_mean_reversion", mode="stock",
        )
        assert sig.side == "buy"
        assert sig.mode == "stock"

    def test_default_mode(self):
        sig = Signal(
            timestamp=datetime(2024, 1, 5),
            ticker="MSFT", side="sell", price=300.0,
            strategy="lorentzian_classification",
        )
        assert sig.mode == "stock"


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

class TestOrder:
    def test_defaults(self):
        order = Order()
        assert order.status == OrderStatus.PENDING
        assert order.order_type == OrderType.LIMIT
        assert len(order.id) == 8
        assert order.fill_price is None

    def test_unique_ids(self):
        o1 = Order()
        o2 = Order()
        assert o1.id != o2.id


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

class TestPosition:
    def test_long_pnl(self):
        pos = Position(
            ticker="AAPL", side="buy", qty=10,
            entry_price=100.0, exit_price=110.0, mode="stock",
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl == 100.0  # (110-100)*10

    def test_short_pnl(self):
        pos = Position(
            ticker="AAPL", side="sell", qty=10,
            entry_price=100.0, exit_price=90.0, mode="stock",
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl == 100.0  # (100-90)*10

    def test_long_loss(self):
        pos = Position(
            ticker="AAPL", side="buy", qty=5,
            entry_price=100.0, exit_price=95.0, mode="stock",
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl == -25.0  # (95-100)*5

    def test_options_multiplier(self):
        pos = Position(
            ticker="AAPL240215C00150000", side="buy", qty=2,
            entry_price=5.0, exit_price=6.0, mode="options",
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl == 200.0  # (6-5)*2*100

    def test_open_pnl_is_none(self):
        pos = Position(ticker="AAPL", side="buy", qty=1, entry_price=100.0)
        assert pos.pnl is None

    def test_pnl_pct_long(self):
        pos = Position(
            ticker="AAPL", side="buy", qty=1,
            entry_price=100.0, exit_price=110.0,
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl_pct == pytest.approx(10.0)

    def test_pnl_pct_short(self):
        pos = Position(
            ticker="AAPL", side="sell", qty=1,
            entry_price=100.0, exit_price=90.0,
            status=PositionStatus.CLOSED,
        )
        assert pos.pnl_pct == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_defaults(self):
        cfg = BacktestConfig()
        assert cfg.initial_capital == 100_000.0
        assert cfg.commission_per_trade == 0.0
        assert cfg.slippage_pct == 0.0
        assert cfg.default_stock_qty == 1
        assert cfg.max_dollar_risk == 50.0
        assert cfg.max_open_positions == 0

    def test_custom(self):
        cfg = BacktestConfig(initial_capital=50_000, slippage_pct=0.1)
        assert cfg.initial_capital == 50_000
        assert cfg.slippage_pct == 0.1
