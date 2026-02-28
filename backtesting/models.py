"""
Crassus 2.5 -- Backtesting data models.

All immutable or semi-mutable dataclasses used by the backtesting engine.
Kept separate from the live-trading models to avoid coupling the backtest
to Azure Functions or Alpaca types.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class OrderType(Enum):
    """Supported simulated order types."""
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderStatus(Enum):
    """Lifecycle states for a simulated order."""
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


class PositionStatus(Enum):
    """Whether a position is still open or has been closed."""
    OPEN = "open"
    CLOSED = "closed"


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    """A single OHLCV price bar.

    Attributes:
        timestamp: Bar open time.
        open: Opening price.
        high: High price.
        low: Low price.
        close: Closing price.
        volume: Bar volume.
        ticker: Instrument symbol (e.g. ``"AAPL"``).
    """
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    ticker: str = ""


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signal:
    """A trade signal to be replayed during backtesting.

    Maps closely to :class:`function_app.parser.ParsedSignal` but uses
    :class:`datetime` for the timestamp instead of an optional string.

    Attributes:
        timestamp: When the signal fired.
        ticker: Instrument symbol.
        side: ``"buy"`` or ``"sell"``.
        price: Signal price (used as limit-order entry price).
        strategy: Strategy name (must exist in the strategy registry).
        mode: ``"stock"`` or ``"options"``.
    """
    timestamp: datetime
    ticker: str
    side: str
    price: float
    strategy: str
    mode: str = "stock"


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """A simulated order managed by the broker.

    Attributes:
        id: Unique order identifier.
        timestamp: When the order was created.
        ticker: Instrument symbol.
        side: ``"buy"`` or ``"sell"``.
        order_type: Market, limit, stop, or stop-limit.
        qty: Number of shares / contracts.
        limit_price: Limit price (for limit and stop-limit orders).
        stop_price: Stop trigger price (for stop and stop-limit orders).
        status: Current order status.
        fill_price: Actual fill price (set when filled).
        fill_timestamp: When the order was filled.
        parent_id: Links bracket legs to their parent order.
        tag: Free-form label (e.g. ``"entry"``, ``"take_profit"``,
             ``"stop_loss"``).
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: datetime = field(default_factory=datetime.now)
    ticker: str = ""
    side: str = "buy"
    order_type: OrderType = OrderType.LIMIT
    qty: int = 1
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: Optional[float] = None
    fill_timestamp: Optional[datetime] = None
    parent_id: Optional[str] = None
    tag: str = ""


# ---------------------------------------------------------------------------
# Positions & trades
# ---------------------------------------------------------------------------

@dataclass
class Position:
    """An open or closed position.

    Attributes:
        ticker: Instrument symbol.
        side: ``"buy"`` (long) or ``"sell"`` (short).
        qty: Position size.
        entry_price: Average fill price at entry.
        entry_timestamp: When the position was opened.
        exit_price: Average fill price at exit (None while open).
        exit_timestamp: When the position was closed (None while open).
        status: Open or closed.
        mode: ``"stock"`` or ``"options"``.
    """
    ticker: str = ""
    side: str = "buy"
    qty: int = 1
    entry_price: float = 0.0
    entry_timestamp: datetime = field(default_factory=datetime.now)
    exit_price: Optional[float] = None
    exit_timestamp: Optional[datetime] = None
    status: PositionStatus = PositionStatus.OPEN
    mode: str = "stock"

    @property
    def pnl(self) -> Optional[float]:
        """Compute realised P&L for closed positions.

        For stock mode: ``(exit - entry) * qty`` for longs, reversed for shorts.
        For options mode: ``(exit - entry) * qty * 100`` (options multiplier).

        Returns ``None`` if the position is still open.
        """
        if self.exit_price is None:
            return None
        multiplier = 100 if self.mode == "options" else 1
        if self.side == "buy":
            return (self.exit_price - self.entry_price) * self.qty * multiplier
        else:
            return (self.entry_price - self.exit_price) * self.qty * multiplier

    @property
    def pnl_pct(self) -> Optional[float]:
        """Return percentage P&L relative to entry cost.

        Returns ``None`` if the position is still open.
        """
        if self.exit_price is None or self.entry_price == 0:
            return None
        if self.side == "buy":
            return (self.exit_price - self.entry_price) / self.entry_price * 100
        else:
            return (self.entry_price - self.exit_price) / self.entry_price * 100


@dataclass
class Trade:
    """A complete round-trip trade (entry + exit).

    Groups the entry order, exit order, resulting position, and metadata
    for analysis and reporting.
    """
    signal: Signal
    position: Position
    entry_order_id: str = ""
    exit_order_id: str = ""
    strategy: str = ""
    mode: str = "stock"
    take_profit_price: float = 0.0
    stop_loss_price: float = 0.0


# ---------------------------------------------------------------------------
# Configuration & results
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """Settings for a backtest run.

    Attributes:
        initial_capital: Starting cash balance in dollars.
        commission_per_trade: Flat commission charged per order fill.
        slippage_pct: Simulated slippage as a percentage of fill price.
            Applied adversely (buys fill slightly higher, sells slightly lower).
        default_stock_qty: Shares per stock signal (mirrors ``DEFAULT_STOCK_QTY``).
        max_dollar_risk: Max dollar risk per options trade (mirrors
            ``MAX_DOLLAR_RISK``).
        max_open_positions: Maximum concurrent open positions (0 = unlimited).
    """
    initial_capital: float = 100_000.0
    commission_per_trade: float = 0.0
    slippage_pct: float = 0.0
    default_stock_qty: int = 1
    max_dollar_risk: float = 50.0
    max_open_positions: int = 0


@dataclass
class BacktestResult:
    """Output of a completed backtest run.

    Contains all trades, the equity curve, and computed metrics.
    """
    config: BacktestConfig
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Dict[str, float]] = field(default_factory=list)
    open_positions: List[Position] = field(default_factory=list)
    signals_processed: int = 0
    signals_skipped: int = 0
    bars_processed: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
