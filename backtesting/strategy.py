"""
Crassus 2.5 -- Self-contained strategy configuration for backtesting.

This is a standalone copy of the strategy configuration and bracket-price
computation logic.  It mirrors ``function_app/strategy.py`` but has **no
import dependency** on the live trading system, making the backtesting
package fully portable.

The strategy registry is loaded from environment variables (with the same
defaults) so that backtests use identical TP/SL parameters to production
unless explicitly overridden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StrategyConfig:
    """Immutable configuration for a single trading strategy."""

    name: str

    # Stock bracket percentages (applied to entry price)
    stock_tp_pct: float          # Take-profit % (e.g. 0.2 means 0.2 %)
    stock_sl_pct: float          # Stop-loss % (trigger price)
    stock_stop_limit_pct: float  # Stop-limit % (limit price for stop leg)

    # Options bracket percentages (applied to premium price)
    options_tp_pct: float        # Take-profit as % of premium
    options_sl_pct: float        # Stop-loss as % of premium


class UnknownStrategyError(Exception):
    """Raised when a signal references a strategy not in the registry."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_float(key: str, default: str) -> float:
    """Read a float from an environment variable with a fallback default."""
    return float(os.environ.get(key, default))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def load_strategy_registry() -> Dict[str, StrategyConfig]:
    """Build the strategy registry from environment variables.

    Each strategy uses a short prefix for its env vars::

        BMR_  ->  bollinger_mean_reversion
        LC_   ->  lorentzian_classification

    Returns:
        A dict keyed by strategy name.
    """
    return {
        "bollinger_mean_reversion": StrategyConfig(
            name="bollinger_mean_reversion",
            stock_tp_pct=_env_float("BMR_STOCK_TP_PCT", "0.2"),
            stock_sl_pct=_env_float("BMR_STOCK_SL_PCT", "0.1"),
            stock_stop_limit_pct=_env_float("BMR_STOCK_STOP_LIMIT_PCT", "0.15"),
            options_tp_pct=_env_float("BMR_OPTIONS_TP_PCT", "20.0"),
            options_sl_pct=_env_float("BMR_OPTIONS_SL_PCT", "10.0"),
        ),
        "lorentzian_classification": StrategyConfig(
            name="lorentzian_classification",
            stock_tp_pct=_env_float("LC_STOCK_TP_PCT", "1.0"),
            stock_sl_pct=_env_float("LC_STOCK_SL_PCT", "0.8"),
            stock_stop_limit_pct=_env_float("LC_STOCK_STOP_LIMIT_PCT", "0.9"),
            options_tp_pct=_env_float("LC_OPTIONS_TP_PCT", "50.0"),
            options_sl_pct=_env_float("LC_OPTIONS_SL_PCT", "40.0"),
        ),
    }


# Module-level registry -- loaded once at import time.
STRATEGY_REGISTRY: Dict[str, StrategyConfig] = load_strategy_registry()


def get_strategy(name: str) -> StrategyConfig:
    """Look up a strategy by name.

    Raises:
        UnknownStrategyError: If the strategy is not registered.
    """
    config = STRATEGY_REGISTRY.get(name)
    if config is None:
        known = ", ".join(sorted(STRATEGY_REGISTRY.keys()))
        raise UnknownStrategyError(
            f"Unknown strategy '{name}'. Registered strategies: {known}"
        )
    return config


# ---------------------------------------------------------------------------
# Bracket-price computation (pure functions)
# ---------------------------------------------------------------------------

def compute_stock_bracket_prices(
    entry_price: float,
    side: str,
    config: StrategyConfig,
) -> Tuple[float, float, float]:
    """Compute take-profit, stop-loss, and stop-limit prices for a stock order.

    Args:
        entry_price: The limit price for the parent order.
        side: ``"buy"`` or ``"sell"``.
        config: Strategy configuration with TP / SL / stop-limit percentages.

    Returns:
        ``(take_profit_price, stop_price, stop_limit_price)`` as raw floats.
    """
    tp_mult = config.stock_tp_pct / 100.0
    sl_mult = config.stock_sl_pct / 100.0
    sl_limit_mult = config.stock_stop_limit_pct / 100.0

    if side == "buy":
        take_profit = entry_price * (1 + tp_mult)
        stop_price  = entry_price * (1 - sl_mult)
        stop_limit  = entry_price * (1 - sl_limit_mult)
    else:
        take_profit = entry_price * (1 - tp_mult)
        stop_price  = entry_price * (1 + sl_mult)
        stop_limit  = entry_price * (1 + sl_limit_mult)

    return take_profit, stop_price, stop_limit


def compute_options_exit_prices(
    premium: float,
    side: str,
    config: StrategyConfig,
) -> Tuple[float, float]:
    """Compute take-profit and stop-loss target prices for an options position.

    Args:
        premium: The price paid per contract (entry premium).
        side: ``"buy"`` or ``"sell"`` -- underlying signal direction.
        config: Strategy configuration with options TP / SL percentages.

    Returns:
        ``(take_profit_price, stop_loss_price)`` as raw floats.
    """
    tp_mult = config.options_tp_pct / 100.0
    sl_mult = config.options_sl_pct / 100.0

    take_profit = premium * (1 + tp_mult)
    stop_loss   = premium * (1 - sl_mult)

    return take_profit, stop_loss


# ---------------------------------------------------------------------------
# Risk sizing (pure function)
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

    Args:
        max_dollar_risk: Maximum dollars to risk on this trade.
        stop_loss_pct: Stop-loss as percentage of premium (e.g. 10.0 = 10 %).
        premium_price: The options premium (entry price per share).

    Returns:
        Number of contracts (integer, minimum 1).
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
