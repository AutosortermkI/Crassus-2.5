"""
Crassus 2.5 -- Options exit monitoring.

Polls open options positions and submits exit orders when take-profit
or stop-loss targets are hit.

Target storage uses a JSON file on disk.  This works for local
development and single-instance Azure Functions.  For multi-instance
scaling, swap ``_load_targets`` / ``_save_targets`` for Azure Table
Storage or Cosmos DB.
"""

import os
import json
import logging
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

from utils import log_structured, round_options_price, get_logger, generate_correlation_id

logger = get_logger(__name__)

# Target storage file (sits alongside function_app.py)
_TARGETS_FILE = Path(__file__).resolve().parent / ".options_targets.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ExitTarget:
    """TP/SL targets for an open options position."""

    contract_symbol: str
    underlying: str
    qty: int
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    correlation_id: str


# ---------------------------------------------------------------------------
# Target persistence (JSON file)
# ---------------------------------------------------------------------------

def _load_targets() -> dict[str, dict]:
    """Load targets from disk.  Returns {contract_symbol: target_dict}."""
    if not _TARGETS_FILE.exists():
        return {}
    try:
        with open(_TARGETS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_targets(targets: dict[str, dict]) -> None:
    """Write targets to disk."""
    with open(_TARGETS_FILE, "w") as f:
        json.dump(targets, f, indent=2)


def register_exit_target(target: ExitTarget) -> None:
    """Register TP/SL targets for a newly opened options position."""
    targets = _load_targets()
    targets[target.contract_symbol] = asdict(target)
    _save_targets(targets)
    log_structured(
        logger, logging.INFO,
        "Registered exit target",
        target.correlation_id,
        contract=target.contract_symbol,
        tp=target.take_profit_price,
        sl=target.stop_loss_price,
    )


def remove_exit_target(contract_symbol: str) -> None:
    """Remove a target after exit order is placed or position is closed."""
    targets = _load_targets()
    targets.pop(contract_symbol, None)
    _save_targets(targets)


# ---------------------------------------------------------------------------
# Exit monitor logic
# ---------------------------------------------------------------------------

def check_options_exits(client: TradingClient) -> list[dict]:
    """Check all tracked options positions against their TP/SL targets.

    For each position:
      - If current price >= TP: submit limit sell at TP
      - If current price <= SL: submit market sell (fast exit)

    Returns a list of actions taken (for logging / response).
    """
    targets = _load_targets()
    if not targets:
        return []

    correlation_id = generate_correlation_id()
    log_structured(
        logger, logging.INFO,
        f"Checking {len(targets)} options exit targets",
        correlation_id,
    )

    # Get all open positions from Alpaca
    try:
        positions = client.get_all_positions()
    except Exception as e:
        log_structured(logger, logging.ERROR, f"Failed to fetch positions: {e}", correlation_id)
        return []

    # Build a lookup: symbol -> position
    position_map = {}
    for pos in positions:
        position_map[pos.symbol] = pos

    actions = []

    for symbol, target_data in list(targets.items()):
        pos = position_map.get(symbol)

        if pos is None:
            # Position no longer open (manually closed, expired, etc.)
            log_structured(
                logger, logging.INFO,
                f"Position closed externally, removing target",
                correlation_id,
                contract=symbol,
            )
            remove_exit_target(symbol)
            actions.append({"contract": symbol, "action": "target_removed", "reason": "position_closed"})
            continue

        current_price = float(pos.current_price)
        qty = abs(int(float(pos.qty)))
        tp = target_data["take_profit_price"]
        sl = target_data["stop_loss_price"]

        log_structured(
            logger, logging.DEBUG,
            "Evaluating exit",
            correlation_id,
            contract=symbol, current=current_price, tp=tp, sl=sl,
        )

        order_id = None

        if current_price >= tp:
            # Take profit hit — submit limit sell at TP
            log_structured(
                logger, logging.INFO,
                "TP target hit, submitting exit",
                correlation_id,
                contract=symbol, current=current_price, tp=tp,
            )
            try:
                order = client.submit_order(order_data=LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    limit_price=round_options_price(tp),
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                ))
                order_id = str(order.id)
                remove_exit_target(symbol)
                actions.append({
                    "contract": symbol, "action": "take_profit",
                    "price": current_price, "target": tp, "order_id": order_id,
                })
            except Exception as e:
                log_structured(logger, logging.ERROR, f"TP exit order failed: {e}", correlation_id, contract=symbol)
                actions.append({"contract": symbol, "action": "tp_error", "error": str(e)})

        elif current_price <= sl:
            # Stop loss hit — submit market sell (fast exit)
            log_structured(
                logger, logging.INFO,
                "SL target hit, submitting exit",
                correlation_id,
                contract=symbol, current=current_price, sl=sl,
            )
            try:
                order = client.submit_order(order_data=MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=OrderSide.SELL,
                    time_in_force=TimeInForce.DAY,
                ))
                order_id = str(order.id)
                remove_exit_target(symbol)
                actions.append({
                    "contract": symbol, "action": "stop_loss",
                    "price": current_price, "target": sl, "order_id": order_id,
                })
            except Exception as e:
                log_structured(logger, logging.ERROR, f"SL exit order failed: {e}", correlation_id, contract=symbol)
                actions.append({"contract": symbol, "action": "sl_error", "error": str(e)})

    return actions
