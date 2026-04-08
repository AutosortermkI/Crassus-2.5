"""
Crassus 2.5 -- Options exit monitoring.

Polls open options positions and submits exit orders when take-profit
or stop-loss targets are hit.

Target storage uses Azure Blob Storage when the Function App is configured
with a real ``AzureWebJobsStorage`` connection string, so tracked exits can
survive instance restarts and scale-out. Local development falls back to a
JSON file on disk.
"""

import os
import fcntl
import json
import logging
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - exercised in environments without Azure SDK
    BlobServiceClient = None

from utils import log_structured, round_options_price, get_logger, generate_correlation_id

logger = get_logger(__name__)

# Target storage file (sits alongside function_app.py)
_TARGETS_FILE = Path(__file__).resolve().parent / ".options_targets.json"
CONTAINER_NAME = os.environ.get("OPTIONS_TARGETS_CONTAINER", "options-exit-targets")


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
# Target persistence
# ---------------------------------------------------------------------------

_LOCK_FILE = _TARGETS_FILE.with_suffix(".lock")


def _connection_string() -> str:
    value = os.environ.get("AzureWebJobsStorage", "").strip()
    if not value or value == "UseDevelopmentStorage=true":
        return ""
    return value


def _use_blob_store() -> bool:
    return BlobServiceClient is not None and bool(_connection_string())


def _container_client():
    service = BlobServiceClient.from_connection_string(_connection_string())
    client = service.get_container_client(CONTAINER_NAME)
    try:
        client.create_container()
    except Exception:
        pass
    return client


def _record_blob_target(target: ExitTarget) -> None:
    client = _container_client()
    client.upload_blob(
        f"{target.contract_symbol}.json",
        json.dumps(asdict(target), indent=2),
        overwrite=True,
    )


def _load_blob_targets() -> dict[str, dict]:
    client = _container_client()
    targets: dict[str, dict] = {}
    for blob in client.list_blobs():
        try:
            raw = client.download_blob(blob.name).readall()
            target = json.loads(raw)
        except Exception:
            continue
        symbol = target.get("contract_symbol")
        if symbol:
            targets[symbol] = target
    return targets


def _remove_blob_target(contract_symbol: str) -> None:
    try:
        _container_client().delete_blob(f"{contract_symbol}.json")
    except Exception:
        pass


@contextmanager
def _locked_targets():
    """Context manager that yields (current_targets, save_fn) under an exclusive file lock.

    Usage::

        with _locked_targets() as (targets, save):
            targets["SYM"] = {...}
            save(targets)
    """
    _LOCK_FILE.touch(exist_ok=True)
    with open(_LOCK_FILE, "r") as lock_fh:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            # Load
            if _TARGETS_FILE.exists():
                try:
                    with open(_TARGETS_FILE, "r") as f:
                        data = json.load(f)
                except (json.JSONDecodeError, OSError):
                    data = {}
            else:
                data = {}

            def _save(targets: dict[str, dict]) -> None:
                with open(_TARGETS_FILE, "w") as f:
                    json.dump(targets, f, indent=2)

            yield data, _save
        finally:
            fcntl.flock(lock_fh, fcntl.LOCK_UN)


def _load_targets() -> dict[str, dict]:
    """Load targets from shared storage or disk."""
    if _use_blob_store():
        return _load_blob_targets()
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
    if _use_blob_store():
        _record_blob_target(target)
        log_structured(
            logger, logging.INFO,
            "Registered exit target",
            target.correlation_id,
            contract=target.contract_symbol,
            tp=target.take_profit_price,
            sl=target.stop_loss_price,
            storage="blob",
        )
        return

    with _locked_targets() as (targets, save):
        targets[target.contract_symbol] = asdict(target)
        save(targets)
    log_structured(
        logger, logging.INFO,
        "Registered exit target",
        target.correlation_id,
        contract=target.contract_symbol,
        tp=target.take_profit_price,
        sl=target.stop_loss_price,
        storage="file",
    )


def remove_exit_target(contract_symbol: str) -> None:
    """Remove a target after exit order is placed or position is closed."""
    if _use_blob_store():
        _remove_blob_target(contract_symbol)
        return

    with _locked_targets() as (targets, save):
        targets.pop(contract_symbol, None)
        save(targets)


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
    symbols_to_remove: list[str] = []

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
            symbols_to_remove.append(symbol)
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
                symbols_to_remove.append(symbol)
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
                symbols_to_remove.append(symbol)
                actions.append({
                    "contract": symbol, "action": "stop_loss",
                    "price": current_price, "target": sl, "order_id": order_id,
                })
            except Exception as e:
                log_structured(logger, logging.ERROR, f"SL exit order failed: {e}", correlation_id, contract=symbol)
                actions.append({"contract": symbol, "action": "sl_error", "error": str(e)})

    # Batch-remove completed targets under a single lock acquisition
    if symbols_to_remove:
        if _use_blob_store():
            for sym in symbols_to_remove:
                _remove_blob_target(sym)
        else:
            with _locked_targets() as (current_targets, save):
                for sym in symbols_to_remove:
                    current_targets.pop(sym, None)
                save(current_targets)

    return actions
