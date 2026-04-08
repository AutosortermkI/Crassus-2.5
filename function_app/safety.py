"""
Crassus 2.5 -- Live trading safety gate.

Provides explicit verification that live trading is intentional, not
accidental. When ``ALPACA_PAPER=false`` (live mode), the system requires
``LIVE_TRADING_CONFIRMED=yes`` to be set, preventing accidental live trades
from a misconfigured environment. Additional operator safety controls can
temporarily halt trading or block new entries after a configured daily loss.
"""

import os
import logging
from utils import get_logger, log_structured

logger = get_logger(__name__)


class LiveTradingNotConfirmedError(Exception):
    """Raised when live trading is active but not explicitly confirmed."""


class TradingHaltedError(Exception):
    """Raised when operators have explicitly halted new trades."""


class DailyLossLimitExceededError(Exception):
    """Raised when a configured daily drawdown threshold has been breached."""


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def check_live_trading_gate(correlation_id: str = "") -> bool:
    """Verify that live trading configuration is intentional.

    Rules:
      - If ``ALPACA_PAPER=true`` (default): always passes (paper mode is safe).
      - If ``ALPACA_PAPER=false`` (live): requires ``LIVE_TRADING_CONFIRMED=yes``.

    Returns:
        True if paper mode, or live mode with confirmation.

    Raises:
        LiveTradingNotConfirmedError: If live mode without confirmation.
    """
    is_paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

    if is_paper:
        return True

    confirmed = os.environ.get("LIVE_TRADING_CONFIRMED", "").strip().lower()
    if confirmed != "yes":
        log_structured(
            logger, logging.CRITICAL,
            "LIVE TRADING BLOCKED: ALPACA_PAPER=false but "
            "LIVE_TRADING_CONFIRMED is not set to 'yes'",
            correlation_id,
        )
        raise LiveTradingNotConfirmedError(
            "Live trading is enabled (ALPACA_PAPER=false) but not confirmed. "
            "Set LIVE_TRADING_CONFIRMED=yes to acknowledge live trading."
        )

    log_structured(
        logger, logging.WARNING,
        "LIVE TRADING MODE ACTIVE",
        correlation_id,
    )
    return True


def check_operator_halt(correlation_id: str = "") -> bool:
    """Block new orders when operators have set the global trading halt flag."""
    if not _env_flag("TRADING_HALTED"):
        return True

    reason = os.environ.get("TRADING_HALTED_REASON", "").strip()
    if reason:
        message = f"Trading is halted by operator control: {reason}"
    else:
        message = "Trading is halted by operator control."

    log_structured(
        logger, logging.CRITICAL,
        "TRADING HALTED",
        correlation_id,
        reason=reason or None,
    )
    raise TradingHaltedError(message)


def check_daily_loss_limit(trading_client, correlation_id: str = "") -> bool:
    """Block new entries after a configured daily loss threshold is breached."""
    max_loss_dollars = _env_float("MAX_DAILY_LOSS_DOLLARS", 0.0)
    max_loss_pct = _env_float("MAX_DAILY_LOSS_PCT", 0.0)
    if max_loss_dollars <= 0 and max_loss_pct <= 0:
        return True

    account = trading_client.get_account()

    try:
        equity = float(account.equity)
        last_equity = float(account.last_equity)
    except (AttributeError, TypeError, ValueError):
        log_structured(
            logger, logging.WARNING,
            "Daily loss limits configured, but account equity fields were unavailable",
            correlation_id,
        )
        return True

    if last_equity <= 0:
        return True

    daily_loss = max(0.0, last_equity - equity)
    daily_loss_pct = (daily_loss / last_equity) * 100.0

    log_structured(
        logger, logging.INFO,
        "Daily loss check",
        correlation_id,
        equity=equity,
        last_equity=last_equity,
        daily_loss=daily_loss,
        daily_loss_pct=round(daily_loss_pct, 4),
        max_loss_dollars=max_loss_dollars if max_loss_dollars > 0 else None,
        max_loss_pct=max_loss_pct if max_loss_pct > 0 else None,
    )

    if max_loss_dollars > 0 and daily_loss >= max_loss_dollars:
        raise DailyLossLimitExceededError(
            f"Daily loss limit reached: ${daily_loss:.2f} loss exceeds "
            f"${max_loss_dollars:.2f} limit"
        )

    if max_loss_pct > 0 and daily_loss_pct >= max_loss_pct:
        raise DailyLossLimitExceededError(
            f"Daily loss limit reached: {daily_loss_pct:.2f}% loss exceeds "
            f"{max_loss_pct:.2f}% limit"
        )

    return True


def check_trading_safety(trading_client, correlation_id: str = "") -> bool:
    """Run all pre-trade safety gates that can block new order entry."""
    check_operator_halt(correlation_id)
    check_live_trading_gate(correlation_id)
    check_daily_loss_limit(trading_client, correlation_id)
    return True


def is_paper_mode() -> bool:
    """Return True if running in paper trading mode."""
    return os.environ.get("ALPACA_PAPER", "true").lower() == "true"
