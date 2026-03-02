#!/usr/bin/env python3
"""Turnkey backtesting script using Yahoo Finance data.

Usage:
    python run_backtest.py                          # defaults: AAPL, last 6 months
    python run_backtest.py --ticker MSFT
    python run_backtest.py --ticker TSLA --start 2024-01-01 --end 2024-12-31
    python run_backtest.py --ticker AAPL --interval 1h --capital 50000
    python run_backtest.py --ticker AAPL --strategy lorentzian_classification
    python run_backtest.py --ticker AAPL --mode options --risk 100
"""

import argparse
from datetime import datetime, timedelta

from backtesting import (
    Engine,
    Signal,
    fetch_bars,
    generate_report,
    compute_metrics,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a backtest with Yahoo Finance data")
    parser.add_argument("--ticker", default="AAPL", help="Stock symbol (default: AAPL)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (default: 6 months ago)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--interval", default="1d", help="Bar interval: 1m,5m,15m,1h,1d,1wk (default: 1d)")
    parser.add_argument("--capital", type=float, default=100_000, help="Starting capital (default: 100000)")
    parser.add_argument("--qty", type=int, default=10, help="Shares per stock trade (default: 10)")
    parser.add_argument("--risk", type=float, default=50, help="Max dollar risk per options trade (default: 50)")
    parser.add_argument("--slippage", type=float, default=0.0, help="Slippage %% (default: 0)")
    parser.add_argument("--commission", type=float, default=0.0, help="Commission per trade (default: 0)")
    parser.add_argument(
        "--strategy",
        default="bollinger_mean_reversion",
        choices=["bollinger_mean_reversion", "lorentzian_classification"],
        help="Strategy name (default: bollinger_mean_reversion)",
    )
    parser.add_argument("--mode", default="stock", choices=["stock", "options"], help="Trade mode (default: stock)")
    args = parser.parse_args()

    # Default date range: last 6 months
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")
    start_date = args.start or (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")

    print(f"Fetching {args.ticker} bars from {start_date} to {end_date} ({args.interval})...")
    bars = fetch_bars(args.ticker, start=start_date, end=end_date, interval=args.interval)
    print(f"  Got {len(bars)} bars\n")

    if not bars:
        print("No bars returned. Check ticker/dates and try again.")
        return

    # Generate simple signals: buy on the first bar, sell halfway through
    mid = len(bars) // 2
    signals = [
        Signal(
            timestamp=bars[0].timestamp,
            ticker=args.ticker,
            side="buy",
            price=bars[0].close,
            strategy=args.strategy,
            mode=args.mode,
        ),
    ]
    if mid > 0:
        signals.append(
            Signal(
                timestamp=bars[mid].timestamp,
                ticker=args.ticker,
                side="buy",
                price=bars[mid].close,
                strategy=args.strategy,
                mode=args.mode,
            ),
        )

    print(f"Generated {len(signals)} signals using '{args.strategy}' ({args.mode} mode)\n")

    # Run the backtest
    engine = Engine(
        initial_capital=args.capital,
        default_stock_qty=args.qty,
        max_dollar_risk=args.risk,
        slippage_pct=args.slippage,
        commission_per_trade=args.commission,
    )

    result = engine.run(bars, signals)
    metrics = compute_metrics(result)
    print(generate_report(result, metrics))

    # Quick summary at the bottom
    print("\n--- Quick Summary ---")
    print(f"  Ticker:         {args.ticker}")
    print(f"  Period:         {start_date} -> {end_date}")
    print(f"  Bars:           {result.bars_processed}")
    print(f"  Signals:        {result.signals_processed} processed, {result.signals_skipped} skipped")
    print(f"  Trades:         {metrics.total_trades}")
    print(f"  Win rate:       {metrics.win_rate:.1f}%")
    print(f"  Total P&L:     ${metrics.total_pnl:,.2f}")
    print(f"  Total return:   {metrics.total_return_pct:.2f}%")
    print(f"  Sharpe ratio:   {metrics.sharpe_ratio:.3f}")
    print(f"  Max drawdown:   {metrics.drawdown.max_drawdown_pct:.2f}%")
    print(f"  Open positions: {len(result.open_positions)}")


if __name__ == "__main__":
    main()
