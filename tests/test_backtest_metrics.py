"""
Tests for backtesting performance metrics.
"""

import pytest
from datetime import datetime

from backtesting.models import (
    Bar,
    Signal,
    Position,
    PositionStatus,
    Trade,
    BacktestConfig,
    BacktestResult,
)
from backtesting.metrics import (
    compute_metrics,
    _daily_returns,
    _compute_drawdown,
    _sharpe_ratio,
    _sortino_ratio,
    _strategy_breakdown,
    PerformanceMetrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(price=100.0, strategy="bollinger_mean_reversion"):
    return Signal(
        timestamp=datetime(2024, 1, 2),
        ticker="AAPL", side="buy", price=price,
        strategy=strategy, mode="stock",
    )


def _make_trade(entry=100.0, exit_price=110.0, qty=10, strategy="bollinger_mean_reversion"):
    pos = Position(
        ticker="AAPL", side="buy", qty=qty,
        entry_price=entry, exit_price=exit_price,
        entry_timestamp=datetime(2024, 1, 2),
        exit_timestamp=datetime(2024, 1, 5),
        status=PositionStatus.CLOSED,
        mode="stock",
    )
    return Trade(
        signal=_make_signal(entry, strategy),
        position=pos,
        strategy=strategy,
        mode="stock",
    )


def _make_equity_curve(equities):
    """Create an equity curve from a list of equity values."""
    return [
        {"timestamp": f"2024-01-{i+2:02d}", "equity": eq, "cash": eq, "open_positions": 0}
        for i, eq in enumerate(equities)
    ]


# ---------------------------------------------------------------------------
# Daily returns
# ---------------------------------------------------------------------------

class TestDailyReturns:
    def test_basic(self):
        curve = _make_equity_curve([100, 110, 105])
        returns = _daily_returns(curve)
        assert len(returns) == 2
        assert returns[0] == pytest.approx(0.10)  # 110/100 - 1
        assert returns[1] == pytest.approx(-0.04545, abs=1e-4)

    def test_empty(self):
        assert _daily_returns([]) == []

    def test_single_point(self):
        assert _daily_returns([{"equity": 100}]) == []


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

class TestDrawdown:
    def test_no_drawdown(self):
        curve = _make_equity_curve([100, 110, 120, 130])
        dd = _compute_drawdown(curve)
        assert dd.max_drawdown_pct == pytest.approx(0.0)

    def test_simple_drawdown(self):
        curve = _make_equity_curve([100, 120, 90, 110])
        dd = _compute_drawdown(curve)
        # Peak=120, trough=90 → 30/120 = 25%
        assert dd.max_drawdown_pct == pytest.approx(25.0)
        assert dd.max_drawdown_dollar == pytest.approx(30.0)
        assert dd.peak_equity == pytest.approx(120.0)
        assert dd.trough_equity == pytest.approx(90.0)

    def test_empty(self):
        dd = _compute_drawdown([])
        assert dd.max_drawdown_pct == 0.0


# ---------------------------------------------------------------------------
# Sharpe ratio
# ---------------------------------------------------------------------------

class TestSharpeRatio:
    def test_positive_returns(self):
        returns = [0.01, 0.02, 0.01, 0.015, 0.005]
        sharpe = _sharpe_ratio(returns)
        assert sharpe > 0

    def test_zero_std(self):
        returns = [0.01, 0.01, 0.01]
        sharpe = _sharpe_ratio(returns)
        assert sharpe == 0.0

    def test_empty(self):
        assert _sharpe_ratio([]) == 0.0


# ---------------------------------------------------------------------------
# Sortino ratio
# ---------------------------------------------------------------------------

class TestSortinoRatio:
    def test_positive_returns_no_downside(self):
        returns = [0.01, 0.02, 0.015]
        sortino = _sortino_ratio(returns)
        # No negative returns → downside dev = 0 → return 0
        assert sortino == 0.0

    def test_mixed_returns(self):
        returns = [0.01, -0.02, 0.015, -0.005, 0.01]
        sortino = _sortino_ratio(returns)
        assert sortino != 0.0

    def test_empty(self):
        assert _sortino_ratio([]) == 0.0


# ---------------------------------------------------------------------------
# Strategy breakdown
# ---------------------------------------------------------------------------

class TestStrategyBreakdown:
    def test_single_strategy(self):
        trades = [
            _make_trade(100, 110, strategy="bmr"),
            _make_trade(100, 95, strategy="bmr"),
        ]
        breakdown = _strategy_breakdown(trades)
        assert "bmr" in breakdown
        assert breakdown["bmr"].total_trades == 2
        assert breakdown["bmr"].winning_trades == 1
        assert breakdown["bmr"].losing_trades == 1

    def test_multiple_strategies(self):
        trades = [
            _make_trade(100, 110, strategy="bmr"),
            _make_trade(100, 105, strategy="lc"),
        ]
        breakdown = _strategy_breakdown(trades)
        assert len(breakdown) == 2
        assert breakdown["bmr"].total_trades == 1
        assert breakdown["lc"].total_trades == 1


# ---------------------------------------------------------------------------
# Full compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_no_trades(self):
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=_make_equity_curve([100_000, 100_000, 100_000]),
        )
        metrics = compute_metrics(result)
        assert metrics.total_trades == 0
        assert metrics.total_return_pct == pytest.approx(0.0)
        assert metrics.final_equity == pytest.approx(100_000)

    def test_with_winning_trades(self):
        trades = [
            _make_trade(100, 110, qty=10),  # P&L = +100
            _make_trade(100, 105, qty=10),  # P&L = +50
        ]
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            trades=trades,
            equity_curve=_make_equity_curve([100_000, 100_100, 100_150]),
        )
        metrics = compute_metrics(result)
        assert metrics.total_trades == 2
        assert metrics.winning_trades == 2
        assert metrics.losing_trades == 0
        assert metrics.win_rate == pytest.approx(100.0)
        assert metrics.total_pnl == pytest.approx(150.0)

    def test_with_mixed_trades(self):
        trades = [
            _make_trade(100, 110, qty=10),  # P&L = +100
            _make_trade(100, 90, qty=10),   # P&L = -100
        ]
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            trades=trades,
            equity_curve=_make_equity_curve([100_000, 100_100, 100_000]),
        )
        metrics = compute_metrics(result)
        assert metrics.total_trades == 2
        assert metrics.winning_trades == 1
        assert metrics.losing_trades == 1
        assert metrics.win_rate == pytest.approx(50.0)
        assert metrics.profit_factor == pytest.approx(1.0)

    def test_exposure(self):
        curve = [
            {"timestamp": "2024-01-02", "equity": 100_000, "cash": 100_000, "open_positions": 0},
            {"timestamp": "2024-01-03", "equity": 100_100, "cash": 99_000, "open_positions": 1},
            {"timestamp": "2024-01-04", "equity": 100_050, "cash": 99_000, "open_positions": 1},
            {"timestamp": "2024-01-05", "equity": 100_000, "cash": 100_000, "open_positions": 0},
        ]
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=curve,
        )
        metrics = compute_metrics(result)
        assert metrics.exposure_pct == pytest.approx(50.0)  # 2/4 bars in market

    def test_empty_equity_curve(self):
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[],
        )
        metrics = compute_metrics(result)
        assert metrics.final_equity == pytest.approx(100_000)
