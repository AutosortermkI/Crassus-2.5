"""
Tests for backtesting report generation.
"""

import pytest
from datetime import datetime

from backtesting.models import (
    Position,
    PositionStatus,
    Signal,
    Trade,
    BacktestConfig,
    BacktestResult,
)
from backtesting.report import generate_report
from backtesting.metrics import compute_metrics


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


class TestGenerateReport:
    def test_report_contains_header(self):
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[
                {"timestamp": "2024-01-02", "equity": 100_000, "cash": 100_000, "open_positions": 0},
            ],
            start_time=datetime(2024, 1, 2),
            end_time=datetime(2024, 1, 31),
            bars_processed=20,
        )
        report = generate_report(result)
        assert "CRASSUS 2.5 -- BACKTEST REPORT" in report
        assert "END OF REPORT" in report

    def test_report_contains_returns(self):
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[
                {"timestamp": "2024-01-02", "equity": 100_000, "cash": 100_000, "open_positions": 0},
                {"timestamp": "2024-01-31", "equity": 105_000, "cash": 105_000, "open_positions": 0},
            ],
            trades=[_make_trade(100, 110, qty=50)],
            start_time=datetime(2024, 1, 2),
            end_time=datetime(2024, 1, 31),
            bars_processed=20,
        )
        report = generate_report(result)
        assert "RETURNS" in report
        assert "Total return:" in report
        assert "Initial capital:" in report

    def test_report_contains_trade_stats(self):
        trades = [
            _make_trade(100, 110, strategy="bollinger_mean_reversion"),
            _make_trade(100, 95, strategy="lorentzian_classification"),
        ]
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[
                {"timestamp": "2024-01-02", "equity": 100_000, "cash": 100_000, "open_positions": 0},
            ],
            trades=trades,
            start_time=datetime(2024, 1, 2),
            end_time=datetime(2024, 1, 31),
            bars_processed=20,
        )
        report = generate_report(result)
        assert "TRADE STATISTICS" in report
        assert "Win rate:" in report
        assert "PER-STRATEGY BREAKDOWN" in report

    def test_report_with_open_positions(self):
        open_pos = Position(
            ticker="AAPL", side="buy", qty=10,
            entry_price=100.0, mode="stock",
        )
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[
                {"timestamp": "2024-01-02", "equity": 100_000, "cash": 99_000, "open_positions": 1},
            ],
            open_positions=[open_pos],
            bars_processed=1,
        )
        report = generate_report(result)
        assert "OPEN POSITIONS AT END" in report
        assert "AAPL" in report

    def test_empty_result(self):
        result = BacktestResult(
            config=BacktestConfig(initial_capital=100_000),
            equity_curve=[],
        )
        report = generate_report(result)
        assert "CRASSUS 2.5 -- BACKTEST REPORT" in report
