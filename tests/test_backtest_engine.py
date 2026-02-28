"""
Tests for the backtesting engine (end-to-end).
"""

import pytest
from datetime import datetime

from backtesting.models import Bar, Signal, BacktestConfig
from backtesting.engine import Engine
from backtesting.data import bars_from_dicts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(ticker="AAPL"):
    """Create a simple 5-bar price series that rises then falls."""
    return bars_from_dicts([
        {"timestamp": datetime(2024, 1, 2, 9, 30), "open": 100, "high": 101, "low": 99,  "close": 100.5},
        {"timestamp": datetime(2024, 1, 3, 9, 30), "open": 100.5, "high": 102, "low": 100, "close": 101.5},
        {"timestamp": datetime(2024, 1, 4, 9, 30), "open": 101.5, "high": 103, "low": 101, "close": 102.5},
        {"timestamp": datetime(2024, 1, 5, 9, 30), "open": 102.5, "high": 103, "low": 99,  "close": 99.5},
        {"timestamp": datetime(2024, 1, 8, 9, 30), "open": 99.5,  "high": 100, "low": 97,  "close": 98.0},
    ], ticker=ticker)


# ---------------------------------------------------------------------------
# Basic engine tests
# ---------------------------------------------------------------------------

class TestEngineBasic:
    def test_no_signals_no_trades(self):
        bars = _make_bars()
        engine = Engine(initial_capital=100_000)
        result = engine.run(bars, [])
        assert result.bars_processed == 5
        assert len(result.trades) == 0
        assert result.signals_processed == 0

    def test_equity_curve_length(self):
        bars = _make_bars()
        engine = Engine(initial_capital=100_000)
        result = engine.run(bars, [])
        assert len(result.equity_curve) == 5

    def test_initial_equity_preserved(self):
        bars = _make_bars()
        engine = Engine(initial_capital=50_000)
        result = engine.run(bars, [])
        assert result.equity_curve[0]["equity"] == pytest.approx(50_000)


# ---------------------------------------------------------------------------
# Stock bracket order execution
# ---------------------------------------------------------------------------

class TestStockBracketExecution:
    def test_buy_signal_creates_trade(self):
        """A buy signal on bar 0 should create a bracket order."""
        bars = _make_bars()
        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
        ]
        engine = Engine(initial_capital=100_000, default_stock_qty=10)
        result = engine.run(bars, signals)
        assert result.signals_processed == 1

    def test_buy_entry_fills(self):
        """Entry should fill when bar low touches the limit price."""
        bars = _make_bars()
        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
        ]
        engine = Engine(initial_capital=100_000, default_stock_qty=10)
        result = engine.run(bars, signals)
        # The signal at bar 0 has entry price=100, bar 0 low=99 → fills
        # BMR TP = 100*(1+0.002) = 100.20, SL = 100*(1-0.001) = 99.90
        # Bar 1 high=102 > TP=100.20 → TP should fill
        # This means we should have 1 completed trade
        assert len(result.trades) >= 1

    def test_sell_signal(self):
        """Sell signals should work with reversed bracket."""
        # Use a falling price series
        bars = bars_from_dicts([
            {"timestamp": datetime(2024, 1, 2, 9, 30), "open": 100, "high": 101, "low": 99, "close": 99.5},
            {"timestamp": datetime(2024, 1, 3, 9, 30), "open": 99.5, "high": 100, "low": 97, "close": 97.5},
            {"timestamp": datetime(2024, 1, 4, 9, 30), "open": 97.5, "high": 98, "low": 95, "close": 95.5},
        ], ticker="AAPL")

        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="sell", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
        ]
        engine = Engine(initial_capital=100_000, default_stock_qty=10)
        result = engine.run(bars, signals)
        assert result.signals_processed == 1


# ---------------------------------------------------------------------------
# Options order execution
# ---------------------------------------------------------------------------

class TestOptionsExecution:
    def test_options_signal(self):
        """Options signals should create orders with 100x multiplier."""
        bars = bars_from_dicts([
            {"timestamp": datetime(2024, 1, 2, 9, 30), "open": 5.0, "high": 5.5, "low": 4.5, "close": 5.2},
            {"timestamp": datetime(2024, 1, 3, 9, 30), "open": 5.2, "high": 7.0, "low": 5.0, "close": 6.5},
            {"timestamp": datetime(2024, 1, 4, 9, 30), "open": 6.5, "high": 7.5, "low": 6.0, "close": 7.0},
        ], ticker="AAPL")

        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=5.0,
                strategy="bollinger_mean_reversion", mode="options",
            ),
        ]
        engine = Engine(initial_capital=100_000, max_dollar_risk=50.0)
        result = engine.run(bars, signals)
        assert result.signals_processed == 1


# ---------------------------------------------------------------------------
# Position limits
# ---------------------------------------------------------------------------

class TestPositionLimits:
    def test_max_positions_enforced(self):
        bars = _make_bars()
        # Two signals at the same time
        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
        ]
        engine = Engine(initial_capital=100_000, max_open_positions=1)
        result = engine.run(bars, signals)
        # Second signal should be skipped
        assert result.signals_skipped >= 1


# ---------------------------------------------------------------------------
# Multiple signals across bars
# ---------------------------------------------------------------------------

class TestMultipleSignals:
    def test_multiple_strategies(self):
        bars = bars_from_dicts([
            {"timestamp": datetime(2024, 1, i, 9, 30),
             "open": 100+i, "high": 102+i, "low": 98+i, "close": 101+i}
            for i in range(2, 12)
        ], ticker="AAPL")

        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="bollinger_mean_reversion", mode="stock",
            ),
            Signal(
                timestamp=datetime(2024, 1, 5, 9, 30),
                ticker="AAPL", side="buy", price=103.0,
                strategy="lorentzian_classification", mode="stock",
            ),
        ]
        engine = Engine(initial_capital=100_000, default_stock_qty=5)
        result = engine.run(bars, signals)
        assert result.signals_processed == 2
        assert result.bars_processed == 10


# ---------------------------------------------------------------------------
# Config override
# ---------------------------------------------------------------------------

class TestConfigOverride:
    def test_config_object(self):
        cfg = BacktestConfig(
            initial_capital=50_000,
            commission_per_trade=1.0,
            slippage_pct=0.05,
            default_stock_qty=5,
        )
        engine = Engine(config=cfg)
        assert engine.config.initial_capital == 50_000
        assert engine.config.commission_per_trade == 1.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_bars(self):
        engine = Engine()
        result = engine.run([], [])
        assert result.bars_processed == 0
        assert result.start_time is None

    def test_unknown_strategy_skipped(self):
        bars = _make_bars()
        signals = [
            Signal(
                timestamp=datetime(2024, 1, 2, 9, 30),
                ticker="AAPL", side="buy", price=100.0,
                strategy="nonexistent_strategy", mode="stock",
            ),
        ]
        engine = Engine()
        result = engine.run(bars, signals)
        assert result.signals_skipped == 1
        assert result.signals_processed == 0
