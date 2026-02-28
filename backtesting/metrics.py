"""
Crassus 2.5 -- Backtesting performance metrics.

Computes standard quantitative trading metrics from a completed backtest:

  - **Total return** and annualised return
  - **Sharpe ratio** (annualised, assuming 252 trading days)
  - **Sortino ratio** (downside-deviation variant of Sharpe)
  - **Maximum drawdown** (peak-to-trough equity decline)
  - **Win rate** and profit factor
  - **Average win / loss** and expectancy
  - **Trade duration** statistics
  - **Per-strategy breakdowns**

All metrics operate on the equity curve and closed trade list from
:class:`BacktestResult`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from backtesting.models import Trade, BacktestResult


TRADING_DAYS_PER_YEAR = 252


@dataclass
class DrawdownInfo:
    """Maximum drawdown details."""
    max_drawdown_pct: float = 0.0
    max_drawdown_dollar: float = 0.0
    peak_equity: float = 0.0
    trough_equity: float = 0.0
    peak_timestamp: str = ""
    trough_timestamp: str = ""


@dataclass
class StrategyMetrics:
    """Per-strategy performance summary."""
    strategy: str = ""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0


@dataclass
class PerformanceMetrics:
    """Complete performance metrics for a backtest run."""
    # Capital
    initial_capital: float = 0.0
    final_equity: float = 0.0
    total_return_pct: float = 0.0
    annualised_return_pct: float = 0.0

    # Risk-adjusted
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Drawdown
    drawdown: DrawdownInfo = field(default_factory=DrawdownInfo)

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    breakeven_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0

    # P&L
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0

    # Exposure
    total_bars: int = 0
    bars_in_market: int = 0
    exposure_pct: float = 0.0

    # Per-strategy
    by_strategy: Dict[str, StrategyMetrics] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _daily_returns(equity_curve: List[Dict[str, float]]) -> List[float]:
    """Extract daily percentage returns from the equity curve."""
    if len(equity_curve) < 2:
        return []
    returns = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]["equity"]
        curr = equity_curve[i]["equity"]
        if prev > 0:
            returns.append((curr - prev) / prev)
        else:
            returns.append(0.0)
    return returns


def _compute_drawdown(equity_curve: List[Dict[str, float]]) -> DrawdownInfo:
    """Compute maximum drawdown from the equity curve."""
    if not equity_curve:
        return DrawdownInfo()

    peak = equity_curve[0]["equity"]
    peak_ts = equity_curve[0].get("timestamp", "")
    max_dd_pct = 0.0
    max_dd_dollar = 0.0
    trough = peak
    trough_ts = peak_ts
    best_peak = peak
    best_peak_ts = peak_ts

    for point in equity_curve:
        eq = point["equity"]
        ts = point.get("timestamp", "")

        if eq > peak:
            peak = eq
            peak_ts = ts

        dd_dollar = peak - eq
        dd_pct = dd_dollar / peak * 100 if peak > 0 else 0.0

        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd_dollar = dd_dollar
            trough = eq
            trough_ts = ts
            best_peak = peak
            best_peak_ts = peak_ts

    return DrawdownInfo(
        max_drawdown_pct=max_dd_pct,
        max_drawdown_dollar=max_dd_dollar,
        peak_equity=best_peak,
        trough_equity=trough,
        peak_timestamp=best_peak_ts,
        trough_timestamp=trough_ts,
    )


def _sharpe_ratio(returns: List[float], risk_free_daily: float = 0.0) -> float:
    """Annualised Sharpe ratio."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free_daily for r in returns]
    mean = sum(excess) / len(excess)
    variance = sum((r - mean) ** 2 for r in excess) / (len(excess) - 1)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _sortino_ratio(returns: List[float], risk_free_daily: float = 0.0) -> float:
    """Annualised Sortino ratio (uses downside deviation)."""
    if len(returns) < 2:
        return 0.0
    excess = [r - risk_free_daily for r in returns]
    mean = sum(excess) / len(excess)
    downside = [min(r, 0.0) ** 2 for r in excess]
    downside_var = sum(downside) / len(downside)
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
    if downside_std == 0:
        return 0.0
    return (mean / downside_std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _trade_pnl(trade: Trade) -> float:
    """Extract P&L from a trade's position."""
    return trade.position.pnl or 0.0


def _strategy_breakdown(trades: List[Trade]) -> Dict[str, StrategyMetrics]:
    """Compute per-strategy metrics."""
    buckets: Dict[str, List[Trade]] = {}
    for t in trades:
        buckets.setdefault(t.strategy, []).append(t)

    result: Dict[str, StrategyMetrics] = {}
    for strat, strat_trades in buckets.items():
        pnls = [_trade_pnl(t) for t in strat_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        result[strat] = StrategyMetrics(
            strategy=strat,
            total_trades=len(strat_trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=len(wins) / len(strat_trades) * 100 if strat_trades else 0.0,
            total_pnl=sum(pnls),
            avg_pnl=sum(pnls) / len(pnls) if pnls else 0.0,
            avg_win=gross_profit / len(wins) if wins else 0.0,
            avg_loss=-gross_loss / len(losses) if losses else 0.0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else float("inf"),
        )

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_metrics(result: BacktestResult) -> PerformanceMetrics:
    """Compute all performance metrics from a backtest result.

    Args:
        result: A completed :class:`BacktestResult`.

    Returns:
        A :class:`PerformanceMetrics` with all computed values.
    """
    equity_curve = result.equity_curve
    trades = result.trades
    initial = result.config.initial_capital

    # Final equity
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial

    # Returns
    total_return_pct = (final_equity - initial) / initial * 100 if initial > 0 else 0.0

    # Annualised return
    n_bars = len(equity_curve)
    years = n_bars / TRADING_DAYS_PER_YEAR if n_bars > 0 else 1.0
    if years > 0 and final_equity > 0 and initial > 0:
        annualised = ((final_equity / initial) ** (1.0 / years) - 1.0) * 100
    else:
        annualised = 0.0

    # Daily returns & ratios
    returns = _daily_returns(equity_curve)
    sharpe = _sharpe_ratio(returns)
    sortino = _sortino_ratio(returns)

    # Drawdown
    drawdown = _compute_drawdown(equity_curve)

    # Calmar ratio
    calmar = annualised / drawdown.max_drawdown_pct if drawdown.max_drawdown_pct > 0 else 0.0

    # Trade statistics
    pnls = [_trade_pnl(t) for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    breakevens = [p for p in pnls if p == 0]

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    total_trades = len(trades)
    win_rate = len(wins) / total_trades * 100 if total_trades > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_pnl = sum(pnls) / total_trades if total_trades > 0 else 0.0
    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = -gross_loss / len(losses) if losses else 0.0
    expectancy = avg_pnl  # Average P&L per trade

    # Exposure
    bars_in_market = sum(
        1 for pt in equity_curve if pt.get("open_positions", 0) > 0
    )
    exposure_pct = bars_in_market / n_bars * 100 if n_bars > 0 else 0.0

    return PerformanceMetrics(
        initial_capital=initial,
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        annualised_return_pct=annualised,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        drawdown=drawdown,
        total_trades=total_trades,
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=len(breakevens),
        win_rate=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        total_pnl=sum(pnls),
        avg_pnl=avg_pnl,
        avg_win=avg_win,
        avg_loss=avg_loss,
        largest_win=max(wins) if wins else 0.0,
        largest_loss=min(losses) if losses else 0.0,
        total_bars=n_bars,
        bars_in_market=bars_in_market,
        exposure_pct=exposure_pct,
        by_strategy=_strategy_breakdown(trades),
    )
