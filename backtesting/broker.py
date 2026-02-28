"""
Crassus 2.5 -- Simulated broker for backtesting.

Manages simulated order lifecycle, fill detection, position tracking,
and cash accounting.  Mirrors the behaviour of real bracket orders
submitted to Alpaca via the live trading system.

Fill logic per bar
------------------

For each pending order the broker checks whether the bar's OHLCV range
would have triggered a fill:

  - **Limit buy**: fills if ``bar.low <= limit_price``.
    Fill price = ``limit_price`` (or worse with slippage).
  - **Limit sell**: fills if ``bar.high >= limit_price``.
    Fill price = ``limit_price`` (or worse with slippage).
  - **Stop buy** (stop-loss on short): fills if ``bar.high >= stop_price``.
    Fill price = ``stop_price``.
  - **Stop sell** (stop-loss on long): fills if ``bar.low <= stop_price``.
    Fill price = ``stop_price``.
  - **Market**: fills immediately at ``bar.open``.

Bracket orders
--------------

A stock bracket order consists of three linked orders:

  1. **Parent** (limit entry)
  2. **Take-profit** (limit exit) -- activated when parent fills
  3. **Stop-loss** (stop exit) -- activated when parent fills

Only one exit leg can fill; the other is cancelled automatically.

For options, the system uses simple limit entry + monitored TP/SL exits
(matching the live exit_monitor.py behaviour).
"""

from __future__ import annotations

import logging
import uuid
from typing import List, Optional, Dict

from backtesting.models import (
    Bar,
    Order,
    OrderType,
    OrderStatus,
    Position,
    PositionStatus,
    Trade,
    Signal,
    BacktestConfig,
)

logger = logging.getLogger(__name__)


class SimulatedBroker:
    """Event-driven simulated broker.

    Maintains order book, open positions, cash balance, and trade history.
    Call :meth:`on_bar` for each price bar to advance the simulation.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.cash: float = config.initial_capital
        self.pending_orders: List[Order] = []
        self.filled_orders: List[Order] = []
        self.cancelled_orders: List[Order] = []
        self.open_positions: List[Position] = []
        self.closed_positions: List[Position] = []
        self.trades: List[Trade] = []

        # Map parent order ID -> (tp_order, sl_order) for bracket management
        self._bracket_legs: Dict[str, Dict[str, Order]] = {}
        # Map parent order ID -> Signal for trade record-keeping
        self._order_signals: Dict[str, Signal] = {}
        # Map parent order ID -> metadata (strategy, mode, tp/sl prices)
        self._order_meta: Dict[str, dict] = {}
        # Map position key -> parent order ID (to link exits back to trades)
        self._position_parent: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_order(self, order: Order) -> str:
        """Add an order to the pending queue.

        Returns:
            The order ID.
        """
        order.status = OrderStatus.PENDING
        self.pending_orders.append(order)
        return order.id

    def submit_bracket_order(
        self,
        signal: Signal,
        entry_order: Order,
        tp_order: Order,
        sl_order: Order,
        strategy: str = "",
        mode: str = "stock",
        tp_price: float = 0.0,
        sl_price: float = 0.0,
    ) -> str:
        """Submit a bracket order (entry + TP leg + SL leg).

        The TP and SL legs remain dormant until the entry order fills.

        Returns:
            The parent (entry) order ID.
        """
        # Link legs to parent
        tp_order.parent_id = entry_order.id
        sl_order.parent_id = entry_order.id

        # Store bracket relationship
        self._bracket_legs[entry_order.id] = {
            "tp": tp_order,
            "sl": sl_order,
        }
        self._order_signals[entry_order.id] = signal
        self._order_meta[entry_order.id] = {
            "strategy": strategy,
            "mode": mode,
            "tp_price": tp_price,
            "sl_price": sl_price,
        }

        # Only the entry order goes into pending immediately
        self.submit_order(entry_order)
        return entry_order.id

    def submit_options_order(
        self,
        signal: Signal,
        entry_order: Order,
        tp_price: float,
        sl_price: float,
        strategy: str = "",
    ) -> str:
        """Submit an options entry order with TP/SL monitoring targets.

        Unlike stock brackets, options TP/SL legs are created as separate
        limit/stop orders that activate after the entry fills, matching
        the live exit_monitor.py behaviour.
        """
        exit_side = "sell" if signal.side == "buy" else "buy"

        tp_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=entry_order.timestamp,
            ticker=entry_order.ticker,
            side=exit_side,
            order_type=OrderType.LIMIT,
            qty=entry_order.qty,
            limit_price=tp_price,
            parent_id=entry_order.id,
            tag="take_profit",
        )
        sl_order = Order(
            id=uuid.uuid4().hex[:8],
            timestamp=entry_order.timestamp,
            ticker=entry_order.ticker,
            side=exit_side,
            order_type=OrderType.STOP,
            qty=entry_order.qty,
            stop_price=sl_price,
            parent_id=entry_order.id,
            tag="stop_loss",
        )

        self._bracket_legs[entry_order.id] = {"tp": tp_order, "sl": sl_order}
        self._order_signals[entry_order.id] = signal
        self._order_meta[entry_order.id] = {
            "strategy": strategy,
            "mode": "options",
            "tp_price": tp_price,
            "sl_price": sl_price,
        }

        self.submit_order(entry_order)
        return entry_order.id

    # ------------------------------------------------------------------
    # Bar processing
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> None:
        """Process a single price bar: check fills, update positions."""
        filled_this_bar: List[Order] = []

        for order in list(self.pending_orders):
            if order.ticker and order.ticker != bar.ticker:
                continue
            fill_price = self._check_fill(order, bar)
            if fill_price is not None:
                self._fill_order(order, fill_price, bar)
                filled_this_bar.append(order)

        # Remove filled orders from pending
        for order in filled_this_bar:
            if order in self.pending_orders:
                self.pending_orders.remove(order)

    def _check_fill(self, order: Order, bar: Bar) -> Optional[float]:
        """Determine whether an order would fill on this bar.

        Returns the raw fill price (before slippage), or ``None``.
        """
        if order.order_type == OrderType.MARKET:
            return bar.open

        if order.order_type == OrderType.LIMIT:
            if order.side == "buy" and bar.low <= order.limit_price:
                return order.limit_price
            if order.side == "sell" and bar.high >= order.limit_price:
                return order.limit_price

        if order.order_type == OrderType.STOP:
            if order.side == "sell" and bar.low <= order.stop_price:
                return order.stop_price
            if order.side == "buy" and bar.high >= order.stop_price:
                return order.stop_price

        if order.order_type == OrderType.STOP_LIMIT:
            if order.side == "sell" and bar.low <= order.stop_price:
                # Stop triggered — fill at limit (or stop if limit not reachable)
                return order.limit_price if order.limit_price else order.stop_price
            if order.side == "buy" and bar.high >= order.stop_price:
                return order.limit_price if order.limit_price else order.stop_price

        return None

    def _apply_slippage(self, price: float, side: str) -> float:
        """Apply slippage: buys fill slightly higher, sells slightly lower."""
        if self.config.slippage_pct <= 0:
            return price
        slip = price * (self.config.slippage_pct / 100.0)
        if side == "buy":
            return round(price + slip, 2)
        else:
            return round(price - slip, 2)

    def _fill_order(self, order: Order, raw_price: float, bar: Bar) -> None:
        """Execute an order fill: update status, cash, and positions."""
        fill_price = self._apply_slippage(raw_price, order.side)
        order.status = OrderStatus.FILLED
        order.fill_price = fill_price
        order.fill_timestamp = bar.timestamp
        self.filled_orders.append(order)

        commission = self.config.commission_per_trade
        mode = "stock"

        # Determine if this is an entry or exit order
        if order.tag in ("take_profit", "stop_loss"):
            self._handle_exit_fill(order, fill_price, bar, commission)
        elif order.parent_id is None or order.tag == "entry":
            # This is an entry order — check for bracket legs
            meta = self._order_meta.get(order.id, {})
            mode = meta.get("mode", "stock")
            multiplier = 100 if mode == "options" else 1
            cost = fill_price * order.qty * multiplier + commission
            self.cash -= cost

            # Open a new position
            pos = Position(
                ticker=order.ticker,
                side=order.side,
                qty=order.qty,
                entry_price=fill_price,
                entry_timestamp=bar.timestamp,
                mode=mode,
            )
            self.open_positions.append(pos)
            self._position_parent[self._pos_key(pos)] = order.id

            # Activate bracket legs
            if order.id in self._bracket_legs:
                legs = self._bracket_legs[order.id]
                tp_order = legs["tp"]
                sl_order = legs["sl"]
                tp_order.qty = order.qty
                sl_order.qty = order.qty
                self.pending_orders.append(tp_order)
                self.pending_orders.append(sl_order)

            logger.debug(
                "Entry filled: %s %s %d @ %.2f (cash=%.2f)",
                order.side, order.ticker, order.qty, fill_price, self.cash,
            )

    def _handle_exit_fill(
        self, order: Order, fill_price: float, bar: Bar, commission: float,
    ) -> None:
        """Close a position when a TP or SL leg fills."""
        parent_id = order.parent_id

        # Find the matching open position
        pos = self._find_open_position(order.ticker, parent_id)
        if pos is None:
            logger.warning("Exit fill but no matching position for %s", order.ticker)
            return

        meta = self._order_meta.get(parent_id, {})
        mode = meta.get("mode", "stock")
        multiplier = 100 if mode == "options" else 1

        # Credit cash
        proceeds = fill_price * order.qty * multiplier - commission
        self.cash += proceeds

        # Close the position
        pos.exit_price = fill_price
        pos.exit_timestamp = bar.timestamp
        pos.status = PositionStatus.CLOSED
        if pos in self.open_positions:
            self.open_positions.remove(pos)
        self.closed_positions.append(pos)

        # Cancel the other bracket leg
        if parent_id in self._bracket_legs:
            legs = self._bracket_legs[parent_id]
            other_tag = "sl" if order.tag == "take_profit" else "tp"
            other_order = legs.get(other_tag)
            if other_order and other_order.status == OrderStatus.PENDING:
                other_order.status = OrderStatus.CANCELLED
                if other_order in self.pending_orders:
                    self.pending_orders.remove(other_order)
                self.cancelled_orders.append(other_order)

        # Record the complete trade
        signal = self._order_signals.get(parent_id)
        if signal:
            trade = Trade(
                signal=signal,
                position=pos,
                entry_order_id=parent_id,
                exit_order_id=order.id,
                strategy=meta.get("strategy", ""),
                mode=mode,
                take_profit_price=meta.get("tp_price", 0.0),
                stop_loss_price=meta.get("sl_price", 0.0),
            )
            self.trades.append(trade)

        logger.debug(
            "Exit filled (%s): %s %d @ %.2f (pnl=%.2f, cash=%.2f)",
            order.tag, order.ticker, order.qty, fill_price,
            pos.pnl or 0.0, self.cash,
        )

    def _find_open_position(
        self, ticker: str, parent_id: Optional[str],
    ) -> Optional[Position]:
        """Find the open position that matches the given exit order."""
        for pos in self.open_positions:
            if pos.ticker == ticker or not pos.ticker:
                key = self._pos_key(pos)
                if parent_id and self._position_parent.get(key) == parent_id:
                    return pos
        # Fallback: match by ticker alone
        for pos in self.open_positions:
            if pos.ticker == ticker:
                return pos
        return None

    @staticmethod
    def _pos_key(pos: Position) -> str:
        """Generate a hashable key for position lookup."""
        return f"{pos.ticker}_{pos.side}_{id(pos)}"

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Current total equity (cash + unrealised value of open positions).

        Note: for accurate mark-to-market, call :meth:`mark_to_market`
        with the latest bar first.
        """
        return self.cash + self._open_positions_value

    @property
    def _open_positions_value(self) -> float:
        """Notional value of all open positions at their entry price.

        This is a rough estimate; for proper mark-to-market, positions
        should be valued at the latest bar close.
        """
        total = 0.0
        for pos in self.open_positions:
            multiplier = 100 if pos.mode == "options" else 1
            total += pos.entry_price * pos.qty * multiplier
        return total

    def mark_to_market(self, bar: Bar) -> float:
        """Return total equity with open positions valued at bar close."""
        mtm = self.cash
        for pos in self.open_positions:
            if pos.ticker and pos.ticker != bar.ticker:
                # Different ticker — use entry price as fallback
                multiplier = 100 if pos.mode == "options" else 1
                mtm += pos.entry_price * pos.qty * multiplier
                continue
            multiplier = 100 if pos.mode == "options" else 1
            mtm += bar.close * pos.qty * multiplier
        return mtm

    @property
    def open_position_count(self) -> int:
        """Number of currently open positions."""
        return len(self.open_positions)

    def cancel_all_pending(self) -> int:
        """Cancel all pending orders.  Returns the number cancelled."""
        count = 0
        for order in list(self.pending_orders):
            order.status = OrderStatus.CANCELLED
            self.cancelled_orders.append(order)
            count += 1
        self.pending_orders.clear()
        return count
