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
  - Yahoo Finance historical OHLCV data fetching
  - Dashboard UI for turnkey backtesting

Reuses existing Crassus modules:
  - strategy.py: StrategyConfig, bracket-price computation
  - risk.py: Position sizing
  - greeks.py: Black-Scholes Greeks (for options premium estimation)

Quick start::

    from backtesting import Engine, load_bars_csv, load_signals_csv

    bars = load_bars_csv("AAPL_daily.csv")
    signals = load_signals_csv("signals.csv")
    result = Engine(initial_capital=100_000).run(bars, signals)
    print(generate_report(result))

Or fetch bars directly from Yahoo Finance::

    from backtesting import Engine
    from backtesting.yahoo_fetch import fetch_bars

    bars = fetch_bars("AAPL", start="2024-01-01", end="2024-06-30")
    result = Engine().run(bars, signals)
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
from backtesting.yahoo_fetch import fetch_bars
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
    "fetch_bars",
    "SimulatedBroker",
    "Engine",
    "compute_metrics",
    "PerformanceMetrics",
    "generate_report",
]
