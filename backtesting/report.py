"""
Crassus 2.5 -- Backtesting report generation.

Produces a human-readable text report from backtest results and metrics.
Designed for terminal output; can also be written to a file.
"""

from __future__ import annotations

from backtesting.models import BacktestResult
from backtesting.metrics import compute_metrics, PerformanceMetrics


def generate_report(
    result: BacktestResult,
    metrics: PerformanceMetrics | None = None,
) -> str:
    """Generate a text summary report from backtest results.

    Args:
        result: Completed :class:`BacktestResult`.
        metrics: Pre-computed metrics.  If ``None``, they are computed
            automatically from *result*.

    Returns:
        A formatted multi-line string.
    """
    if metrics is None:
        metrics = compute_metrics(result)

    lines: list[str] = []
    w = 60  # column width for the divider

    lines.append("=" * w)
    lines.append("CRASSUS 2.5 -- BACKTEST REPORT")
    lines.append("=" * w)

    # Time range
    if result.start_time and result.end_time:
        lines.append(f"Period:           {result.start_time:%Y-%m-%d} to {result.end_time:%Y-%m-%d}")
    lines.append(f"Bars processed:   {result.bars_processed:,}")
    lines.append(f"Signals processed:{metrics.total_trades:>6}")
    lines.append(f"Signals skipped:  {result.signals_skipped:>6}")
    lines.append("")

    # Capital & returns
    lines.append("-" * w)
    lines.append("RETURNS")
    lines.append("-" * w)
    lines.append(f"Initial capital:  ${metrics.initial_capital:>14,.2f}")
    lines.append(f"Final equity:     ${metrics.final_equity:>14,.2f}")
    lines.append(f"Total P&L:        ${metrics.total_pnl:>14,.2f}")
    lines.append(f"Total return:     {metrics.total_return_pct:>14.2f}%")
    lines.append(f"Annualised return:{metrics.annualised_return_pct:>14.2f}%")
    lines.append("")

    # Risk metrics
    lines.append("-" * w)
    lines.append("RISK METRICS")
    lines.append("-" * w)
    lines.append(f"Sharpe ratio:     {metrics.sharpe_ratio:>14.3f}")
    lines.append(f"Sortino ratio:    {metrics.sortino_ratio:>14.3f}")
    lines.append(f"Calmar ratio:     {metrics.calmar_ratio:>14.3f}")
    lines.append(f"Max drawdown:     {metrics.drawdown.max_drawdown_pct:>13.2f}%")
    lines.append(f"Max drawdown $:   ${metrics.drawdown.max_drawdown_dollar:>14,.2f}")
    if metrics.drawdown.peak_timestamp:
        lines.append(f"  Peak:           {metrics.drawdown.peak_timestamp}")
        lines.append(f"  Trough:         {metrics.drawdown.trough_timestamp}")
    lines.append("")

    # Trade statistics
    lines.append("-" * w)
    lines.append("TRADE STATISTICS")
    lines.append("-" * w)
    lines.append(f"Total trades:     {metrics.total_trades:>14}")
    lines.append(f"Winning trades:   {metrics.winning_trades:>14}")
    lines.append(f"Losing trades:    {metrics.losing_trades:>14}")
    lines.append(f"Breakeven trades: {metrics.breakeven_trades:>14}")
    lines.append(f"Win rate:         {metrics.win_rate:>13.1f}%")
    pf_str = f"{metrics.profit_factor:.2f}" if metrics.profit_factor != float("inf") else "inf"
    lines.append(f"Profit factor:    {pf_str:>14}")
    lines.append(f"Expectancy:       ${metrics.expectancy:>14,.2f}")
    lines.append("")
    lines.append(f"Avg P&L/trade:    ${metrics.avg_pnl:>14,.2f}")
    lines.append(f"Avg win:          ${metrics.avg_win:>14,.2f}")
    lines.append(f"Avg loss:         ${metrics.avg_loss:>14,.2f}")
    lines.append(f"Largest win:      ${metrics.largest_win:>14,.2f}")
    lines.append(f"Largest loss:     ${metrics.largest_loss:>14,.2f}")
    lines.append("")

    # Exposure
    lines.append("-" * w)
    lines.append("EXPOSURE")
    lines.append("-" * w)
    lines.append(f"Total bars:       {metrics.total_bars:>14,}")
    lines.append(f"Bars in market:   {metrics.bars_in_market:>14,}")
    lines.append(f"Exposure:         {metrics.exposure_pct:>13.1f}%")
    lines.append("")

    # Open positions at end
    if result.open_positions:
        lines.append("-" * w)
        lines.append(f"OPEN POSITIONS AT END ({len(result.open_positions)})")
        lines.append("-" * w)
        for pos in result.open_positions:
            lines.append(
                f"  {pos.ticker:<8} {pos.side:<5} qty={pos.qty} "
                f"entry=${pos.entry_price:.2f} mode={pos.mode}"
            )
        lines.append("")

    # Per-strategy breakdown
    if metrics.by_strategy:
        lines.append("-" * w)
        lines.append("PER-STRATEGY BREAKDOWN")
        lines.append("-" * w)
        for strat_name, sm in sorted(metrics.by_strategy.items()):
            lines.append(f"  {strat_name}")
            lines.append(f"    Trades:       {sm.total_trades:>8}")
            lines.append(f"    Win rate:     {sm.win_rate:>7.1f}%")
            lines.append(f"    Total P&L:    ${sm.total_pnl:>10,.2f}")
            lines.append(f"    Avg P&L:      ${sm.avg_pnl:>10,.2f}")
            pf = f"{sm.profit_factor:.2f}" if sm.profit_factor != float("inf") else "inf"
            lines.append(f"    Profit factor:{pf:>10}")
            lines.append("")

    lines.append("=" * w)
    lines.append("END OF REPORT")
    lines.append("=" * w)

    return "\n".join(lines)
