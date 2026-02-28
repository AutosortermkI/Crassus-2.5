"""
Crassus 2.5 -- Backtesting engine.

Orchestrates the bar-by-bar replay of historical data through the
simulated broker, reusing the same strategy logic as the live system.

Usage::

    from backtesting import Engine, load_bars_csv, load_signals_csv

    bars = load_bars_csv("AAPL_daily.csv", ticker="AAPL")
    signals = load_signals_csv("signals.csv")
    result = Engine(initial_capital=100_000).run(bars, signals)

The engine:
  1. Merges bars and signals into a single time-ordered event stream.
  2. For each bar, checks the broker for order fills.
  3. For each signal on this bar's timestamp, computes bracket prices
     using the existing strategy module and submits simulated orders.
  4. Records the equity curve at each bar close.
  5. After all bars, computes performance metrics and returns a
     :class:`BacktestResult`.
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Dict

from backtesting.models import (
    Bar,
    Signal,
    Order,
    OrderType,
    BacktestConfig,
    BacktestResult,
)
from backtesting.broker import SimulatedBroker
from backtesting.metrics import compute_metrics

# Import existing Crassus strategy logic
from strategy import (
    get_strategy,
    compute_stock_bracket_prices,
    compute_options_exit_prices,
    StrategyConfig,
    UnknownStrategyError,
)
from risk import compute_options_qty

logger = logging.getLogger(__name__)


class Engine:
    """Main backtesting engine.

    Args:
        initial_capital: Starting cash (default $100,000).
        commission_per_trade: Flat fee per fill.
        slippage_pct: Simulated slippage percentage.
        default_stock_qty: Shares per stock signal.
        max_dollar_risk: Max $ risk per options trade.
        max_open_positions: Concurrent position cap (0 = unlimited).
        config: Provide a :class:`BacktestConfig` directly (overrides
            the individual keyword arguments above).
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        commission_per_trade: float = 0.0,
        slippage_pct: float = 0.0,
        default_stock_qty: int = 1,
        max_dollar_risk: float = 50.0,
        max_open_positions: int = 0,
        config: Optional[BacktestConfig] = None,
    ) -> None:
        if config is not None:
            self.config = config
        else:
            self.config = BacktestConfig(
                initial_capital=initial_capital,
                commission_per_trade=commission_per_trade,
                slippage_pct=slippage_pct,
                default_stock_qty=default_stock_qty,
                max_dollar_risk=max_dollar_risk,
                max_open_positions=max_open_positions,
            )

    def run(
        self,
        bars: List[Bar],
        signals: List[Signal],
    ) -> BacktestResult:
        """Execute the backtest.

        Args:
            bars: Historical OHLCV bars sorted by timestamp.
            signals: Trade signals sorted by timestamp.

        Returns:
            A :class:`BacktestResult` with trades, equity curve, and metrics.
        """
        broker = SimulatedBroker(self.config)

        # Index signals by timestamp for O(1) lookup
        signal_index: Dict[str, List[Signal]] = {}
        for sig in signals:
            key = sig.timestamp.isoformat()
            signal_index.setdefault(key, []).append(sig)

        equity_curve: List[Dict[str, float]] = []
        signals_processed = 0
        signals_skipped = 0

        for bar in bars:
            # 1. Process fills on this bar
            broker.on_bar(bar)

            # 2. Check for signals at this bar's timestamp
            key = bar.timestamp.isoformat()
            bar_signals = signal_index.get(key, [])

            for sig in bar_signals:
                # Filter by ticker if the bar has one
                if bar.ticker and sig.ticker != bar.ticker:
                    continue

                # Check position limits (count open positions + pending entries)
                pending_entries = sum(
                    1 for o in broker.pending_orders if o.tag == "entry"
                )
                total_exposure = broker.open_position_count + pending_entries
                if (self.config.max_open_positions > 0 and
                        total_exposure >= self.config.max_open_positions):
                    signals_skipped += 1
                    logger.debug("Skipping signal (max positions): %s", sig)
                    continue

                try:
                    self._process_signal(broker, sig, bar)
                    signals_processed += 1
                except UnknownStrategyError as e:
                    logger.warning("Skipping signal (unknown strategy): %s", e)
                    signals_skipped += 1

            # 3. Record equity at bar close
            equity = broker.mark_to_market(bar)
            equity_curve.append({
                "timestamp": bar.timestamp.isoformat(),
                "equity": equity,
                "cash": broker.cash,
                "open_positions": broker.open_position_count,
            })

        # Cancel any remaining pending orders
        broker.cancel_all_pending()

        result = BacktestResult(
            config=self.config,
            trades=broker.trades,
            equity_curve=equity_curve,
            open_positions=list(broker.open_positions),
            signals_processed=signals_processed,
            signals_skipped=signals_skipped,
            bars_processed=len(bars),
            start_time=bars[0].timestamp if bars else None,
            end_time=bars[-1].timestamp if bars else None,
        )

        return result

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _process_signal(
        self, broker: SimulatedBroker, signal: Signal, bar: Bar,
    ) -> None:
        """Convert a signal into simulated orders via the strategy module."""
        strategy_config = get_strategy(signal.strategy)

        if signal.mode == "stock":
            self._submit_stock_bracket(broker, signal, strategy_config, bar)
        elif signal.mode == "options":
            self._submit_options_order(broker, signal, strategy_config, bar)
        else:
            logger.warning("Unsupported mode '%s' in signal", signal.mode)

    def _submit_stock_bracket(
        self,
        broker: SimulatedBroker,
        signal: Signal,
        config: StrategyConfig,
        bar: Bar,
    ) -> None:
        """Build and submit a stock bracket order (entry + TP + SL)."""
        tp_price, stop_price, stop_limit_price = compute_stock_bracket_prices(
            entry_price=signal.price,
            side=signal.side,
            config=config,
        )

        qty = self.config.default_stock_qty
        exit_side = "sell" if signal.side == "buy" else "buy"

        entry_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=bar.timestamp,
            ticker=signal.ticker,
            side=signal.side,
            order_type=OrderType.LIMIT,
            qty=qty,
            limit_price=signal.price,
            tag="entry",
        )

        tp_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=bar.timestamp,
            ticker=signal.ticker,
            side=exit_side,
            order_type=OrderType.LIMIT,
            qty=qty,
            limit_price=tp_price,
            tag="take_profit",
        )

        sl_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=bar.timestamp,
            ticker=signal.ticker,
            side=exit_side,
            order_type=OrderType.STOP,
            qty=qty,
            stop_price=stop_price,
            tag="stop_loss",
        )

        broker.submit_bracket_order(
            signal=signal,
            entry_order=entry_order,
            tp_order=tp_order,
            sl_order=sl_order,
            strategy=config.name,
            mode="stock",
            tp_price=tp_price,
            sl_price=stop_price,
        )

        logger.debug(
            "Stock bracket submitted: %s %s @ %.2f (TP=%.2f, SL=%.2f)",
            signal.side, signal.ticker, signal.price, tp_price, stop_price,
        )

    def _submit_options_order(
        self,
        broker: SimulatedBroker,
        signal: Signal,
        config: StrategyConfig,
        bar: Bar,
    ) -> None:
        """Build and submit an options entry order with TP/SL targets.

        In backtesting, we simulate options using the signal price as
        the premium.  The TP/SL targets are computed as percentages of
        the premium, matching the live system behaviour.
        """
        premium = signal.price
        tp_price, sl_price = compute_options_exit_prices(
            premium=premium,
            side=signal.side,
            config=config,
        )

        qty = compute_options_qty(
            max_dollar_risk=self.config.max_dollar_risk,
            stop_loss_pct=config.options_sl_pct,
            premium_price=premium,
        )

        entry_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=bar.timestamp,
            ticker=signal.ticker,
            side="buy",  # Always buying options (calls for buy signal, puts for sell)
            order_type=OrderType.LIMIT,
            qty=qty,
            limit_price=premium,
            tag="entry",
        )

        broker.submit_options_order(
            signal=signal,
            entry_order=entry_order,
            tp_price=tp_price,
            sl_price=sl_price,
            strategy=config.name,
        )

        logger.debug(
            "Options order submitted: %s %s premium=%.2f qty=%d (TP=%.2f, SL=%.2f)",
            signal.side, signal.ticker, premium, qty, tp_price, sl_price,
        )
