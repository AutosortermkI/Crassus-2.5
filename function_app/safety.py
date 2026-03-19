"""
Crassus 2.5 -- Live trading safety gate.

Provides explicit verification that live trading is intentional, not
accidental. When ``ALPACA_PAPER=false`` (live mode), the system requires
``LIVE_TRADING_CONFIRMED=yes`` to be set, preventing accidental live trades
from a misconfigured environment.
"""

import os
import logging
from utils import get_logger, log_structured

logger = get_logger(__name__)


class LiveTradingNotConfirmedError(Exception):
    """Raised when live trading is active but not explicitly confirmed."""


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


def is_paper_mode() -> bool:
    """Return True if running in paper trading mode."""
    return os.environ.get("ALPACA_PAPER", "true").lower() == "true"
