"""
Crassus 2.5 -- Risk sizing.

Computes position sizes for both stock and options trades.

Stock sizing modes (controlled by ``STOCK_SIZING_MODE`` env var):
  - ``"fixed"``:  Use ``DEFAULT_STOCK_QTY`` shares per trade (default).
  - ``"risk_pct"``:  Risk ``RISK_PCT_OF_EQUITY`` % of account equity per trade.
    Shares = (equity * pct / 100) / (entry_price * stop_loss_pct / 100).

Options sizing: fixed dollar risk from ``MAX_DOLLAR_RISK`` env var.

Buying-power validation: ``validate_buying_power()`` checks that the
account has sufficient buying power before submitting an order.
"""

import logging
import os
from typing import Optional

from utils import get_logger, log_structured

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def get_max_dollar_risk() -> float:
    """Return the maximum dollar risk per trade from environment.

    Default: $50 -- a conservative starting point for options.
    """
    return float(os.environ.get("MAX_DOLLAR_RISK", "50.0"))


def get_risk_pct_of_equity() -> Optional[float]:
    """Return the risk percentage of equity, if configured.

    Returns:
        The configured percentage, or ``None`` if not set.
    """
    val = os.environ.get("RISK_PCT_OF_EQUITY")
    if val is not None:
        return float(val)
    return None


def get_stock_sizing_mode() -> str:
    """Return the stock sizing mode: 'fixed' or 'risk_pct'."""
    return os.environ.get("STOCK_SIZING_MODE", "fixed").strip().lower()


def get_max_open_positions() -> int:
    """Return the maximum number of concurrent open positions allowed.

    Default: 10 -- prevents over-concentration.
    """
    return int(os.environ.get("MAX_OPEN_POSITIONS", "10"))


# ---------------------------------------------------------------------------
# Options sizing
# ---------------------------------------------------------------------------

def compute_options_qty(
    max_dollar_risk: float,
    stop_loss_pct: float,
    premium_price: float,
) -> int:
    """Compute the number of options contracts to trade.

    Formula::

        stop_distance = (stop_loss_pct / 100) * premium_price
        qty = max_dollar_risk / (stop_distance * 100)

    The x100 accounts for the options multiplier (each contract = 100 shares).
    """
    if premium_price <= 0:
        return 1
    if stop_loss_pct <= 0:
        return 1

    stop_distance = (stop_loss_pct / 100.0) * premium_price
    if stop_distance <= 0:
        return 1

    qty = max_dollar_risk / (stop_distance * 100.0)
    return max(1, int(qty))


# ---------------------------------------------------------------------------
# Stock sizing
# ---------------------------------------------------------------------------

def compute_stock_qty(
    entry_price: float = 0.0,
    stop_loss_pct: float = 0.0,
    account_equity: Optional[float] = None,
) -> int:
    """Compute stock quantity based on the configured sizing mode.

    Modes:
      - ``"fixed"``:  Returns ``DEFAULT_STOCK_QTY`` (default 1).
      - ``"risk_pct"``:  Computes shares from account equity, risk %,
        entry price, and stop-loss %.  Formula::

            risk_dollars = equity * (risk_pct / 100)
            dollar_risk_per_share = entry_price * (stop_loss_pct / 100)
            qty = risk_dollars / dollar_risk_per_share

    Falls back to ``"fixed"`` if required parameters are missing or zero.
    """
    mode = get_stock_sizing_mode()

    if mode == "risk_pct":
        risk_pct = get_risk_pct_of_equity()
        if (
            risk_pct is not None
            and risk_pct > 0
            and account_equity is not None
            and account_equity > 0
            and entry_price > 0
            and stop_loss_pct > 0
        ):
            risk_dollars = account_equity * (risk_pct / 100.0)
            dollar_risk_per_share = entry_price * (stop_loss_pct / 100.0)
            if dollar_risk_per_share > 0:
                qty = int(risk_dollars / dollar_risk_per_share)
                return max(1, qty)
        # Fall through to fixed if any param is missing
        logger.warning(
            "risk_pct sizing requested but missing params "
            "(equity=%s, risk_pct=%s, entry=%s, sl_pct=%s); "
            "falling back to fixed qty",
            account_equity, risk_pct, entry_price, stop_loss_pct,
        )

    return int(os.environ.get("DEFAULT_STOCK_QTY", "1"))


# ---------------------------------------------------------------------------
# Buying power validation
# ---------------------------------------------------------------------------

class InsufficientBuyingPowerError(Exception):
    """Raised when account buying power is insufficient for the order."""


def validate_buying_power(
    trading_client,
    required_dollars: float,
    correlation_id: str,
) -> float:
    """Check that the account has enough buying power for the trade.

    Args:
        trading_client: Authenticated Alpaca TradingClient.
        required_dollars: Dollar amount needed (qty * entry_price).
        correlation_id: For log tracing.

    Returns:
        Current buying power as a float.

    Raises:
        InsufficientBuyingPowerError: If buying power < required_dollars.
    """
    account = trading_client.get_account()
    buying_power = float(account.buying_power)

    log_structured(
        logger, logging.INFO,
        "Buying power check",
        correlation_id,
        buying_power=buying_power,
        required=required_dollars,
    )

    if buying_power < required_dollars:
        raise InsufficientBuyingPowerError(
            f"Insufficient buying power: ${buying_power:.2f} available, "
            f"${required_dollars:.2f} required"
        )

    return buying_power


def get_account_equity(trading_client) -> float:
    """Query the current account equity from Alpaca.

    Returns:
        Account equity as a float.
    """
    account = trading_client.get_account()
    return float(account.equity)


def get_open_position_count(trading_client) -> int:
    """Return the number of currently open positions."""
    positions = trading_client.get_all_positions()
    return len(positions)


class MaxPositionsExceededError(Exception):
    """Raised when the account already has too many open positions."""


def validate_position_limit(
    trading_client,
    correlation_id: str,
) -> int:
    """Check that we haven't exceeded the max open positions limit.

    Returns:
        Current open position count.

    Raises:
        MaxPositionsExceededError: If at or above the limit.
    """
    max_positions = get_max_open_positions()
    count = get_open_position_count(trading_client)

    log_structured(
        logger, logging.INFO,
        "Position limit check",
        correlation_id,
        open_positions=count,
        max_positions=max_positions,
    )

    if count >= max_positions:
        raise MaxPositionsExceededError(
            f"Max open positions reached: {count}/{max_positions}"
        )

    return count
