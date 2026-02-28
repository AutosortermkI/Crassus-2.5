"""
Crassus 2.5 -- Backtesting Engine.

Event-driven backtesting framework that replays historical price data
through the same strategy logic used in live trading.

Supports:
  - Stock bracket orders (limit entry + TP + SL legs)
  - Options trades (premium-based TP/SL simulation)
  - Bar-by-bar OHLCV replay with realistic fill simulation
  - Comprehensive performance metrics (Sharpe, drawdown, win rate, etc.)
  - CSV data ingestion for price bars and trade signals

Reuses existing Crassus modules:
  - strategy.py: StrategyConfig, bracket-price computation
  - risk.py: Position sizing
  - greeks.py: Black-Scholes Greeks (for options premium estimation)

Quick start::

    from backtesting import Engine, load_bars_csv, load_signals_csv

    bars = load_bars_csv("AAPL_daily.csv")
    signals = load_signals_csv("signals.csv")
    result = Engine(initial_capital=100_000).run(bars, signals)
    print(result.report())
"""

from backtesting.models import (
    Bar,
    Signal,
    Order,
    OrderType,
    OrderStatus,
    Position,
    Trade,
    BacktestConfig,
    BacktestResult,
)
from backtesting.data import load_bars_csv, load_signals_csv, bars_from_dicts
from backtesting.broker import SimulatedBroker
from backtesting.engine import Engine
from backtesting.metrics import compute_metrics, PerformanceMetrics
from backtesting.report import generate_report

__all__ = [
    "Bar",
    "Signal",
    "Order",
    "OrderType",
    "OrderStatus",
    "Position",
    "Trade",
    "BacktestConfig",
    "BacktestResult",
    "load_bars_csv",
    "load_signals_csv",
    "bars_from_dicts",
    "SimulatedBroker",
    "Engine",
    "compute_metrics",
    "PerformanceMetrics",
    "generate_report",
]
