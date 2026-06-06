"""
Crassus-owned paper ledger.

The ledger records Crassus decisions and broker responses independently from
Tastytrade sandbox state. Azure deployments use Blob Storage when a real
``AzureWebJobsStorage`` connection string is available; local development and
tests use a file fallback.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - optional in stripped local envs
    BlobServiceClient = None


CONTAINER_NAME = os.environ.get("PAPER_LEDGER_CONTAINER", "paper-ledger")
LOCAL_STORE = Path(__file__).resolve().parent / ".paper_ledger.json"
DEFAULT_FILL_POLICY = "preflight_only"


def record_ledger_event(event_type: str, **fields: Any) -> dict:
    """Persist one append-only paper ledger event and return the stored event."""
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "event_id": fields.pop("event_id", uuid.uuid4().hex),
        "event_type": event_type,
        "recorded_at": fields.pop("recorded_at", now),
        "correlation_id": fields.pop("correlation_id", ""),
    }
    event.update(fields)
    if _use_blob_store():
        _record_blob_event(event)
    else:
        _record_local_event(event)
    return event


def record_trade_lifecycle(
    *,
    payload: dict,
    parsed: Optional[dict],
    execution: dict,
    correlation_id: str,
) -> List[dict]:
    """Record signal and broker lifecycle events for one webhook execution."""
    events = [
        record_ledger_event(
            "signal_received",
            correlation_id=correlation_id,
            payload=payload or {},
            parsed=parsed,
        )
    ]

    execution_body = execution.get("body") if isinstance(execution, dict) else {}
    if not isinstance(execution_body, dict):
        execution_body = {"raw_body": execution_body}
    broker = execution_body.get("broker") or execution.get("broker") or ""
    event_type = _execution_event_type(execution, execution_body)
    events.append(
        record_ledger_event(
            event_type,
            correlation_id=correlation_id,
            broker=broker,
            execution=execution,
            parsed=parsed,
        )
    )
    return events


def get_ledger_events(limit: int = 50) -> List[dict]:
    """Return recent ledger events, newest first."""
    safe_limit = max(1, int(limit))
    if _use_blob_store():
        return _load_blob_events(safe_limit)
    return _load_local_events()[:safe_limit]


def get_paper_account() -> dict:
    """Materialize current paper account state from append-only events."""
    events = list(reversed(get_ledger_events(limit=1000)))
    starting_cash = _starting_cash()
    cash = starting_cash
    positions: Dict[str, dict] = {}
    realized_pl = 0.0

    for event in events:
        if event.get("event_type") != "paper_fill":
            continue
        fill = event.get("fill") if isinstance(event.get("fill"), dict) else {}
        symbol = str(fill.get("symbol") or fill.get("contract") or "").strip()
        side = str(fill.get("side") or "").strip().lower()
        qty = _float_value(fill.get("qty"), 0.0)
        price = _float_value(fill.get("price"), 0.0)
        if not symbol or qty <= 0 or price <= 0:
            continue
        position = positions.setdefault(symbol, {
            "symbol": symbol,
            "qty": 0.0,
            "avg_entry": 0.0,
            "current_mark": None,
            "realized_pl": 0.0,
            "unrealized_pl": 0.0,
            "source": "crassus_paper_ledger",
        })
        if side == "buy":
            previous_cost = position["avg_entry"] * position["qty"]
            new_cost = previous_cost + (qty * price)
            position["qty"] += qty
            position["avg_entry"] = new_cost / position["qty"] if position["qty"] else 0.0
            cash -= qty * price
        elif side == "sell":
            close_qty = min(qty, position["qty"])
            pnl = (price - position["avg_entry"]) * close_qty
            realized_pl += pnl
            position["realized_pl"] += pnl
            position["qty"] -= close_qty
            cash += close_qty * price

    open_positions = [
        _round_position(position)
        for position in positions.values()
        if position["qty"] > 0
    ]
    unrealized_pl = sum(float(position.get("unrealized_pl") or 0.0) for position in open_positions)
    total_equity = cash + sum(
        float(position.get("qty") or 0.0) * float(position.get("current_mark") or position.get("avg_entry") or 0.0)
        for position in open_positions
    )
    return {
        "source": "crassus_paper_ledger",
        "paper_fill_policy": _paper_fill_policy(),
        "starting_cash": round(starting_cash, 2),
        "cash": round(cash, 2),
        "realized_pl": round(realized_pl, 2),
        "unrealized_pl": round(unrealized_pl, 2),
        "total_equity": round(total_equity, 2),
        "open_positions": open_positions,
        "event_count": len(events),
        "last_event_at": events[-1].get("recorded_at") if events else "",
        "message": _paper_account_message(),
    }


def _execution_event_type(execution: dict, body: dict) -> str:
    if not execution.get("ok"):
        return "broker_rejected"
    if body.get("dry_run") is True:
        return "broker_preflight"
    return "broker_order"


def _paper_fill_policy() -> str:
    return (os.environ.get("PAPER_FILL_MODE") or DEFAULT_FILL_POLICY).strip().lower() or DEFAULT_FILL_POLICY


def _paper_account_message() -> str:
    if _paper_fill_policy() == DEFAULT_FILL_POLICY:
        return "Dry-run broker validations are recorded, but paper fills are not assumed."
    return f"Paper fills use configured policy: {_paper_fill_policy()}."


def _starting_cash() -> float:
    return _float_value(os.environ.get("PAPER_STARTING_CASH"), 0.0)


def _float_value(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_position(position: dict) -> dict:
    return {
        "symbol": position["symbol"],
        "qty": round(float(position["qty"]), 6),
        "avg_entry": round(float(position["avg_entry"]), 4),
        "current_mark": position.get("current_mark"),
        "realized_pl": round(float(position.get("realized_pl") or 0.0), 2),
        "unrealized_pl": round(float(position.get("unrealized_pl") or 0.0), 2),
        "source": position.get("source", "crassus_paper_ledger"),
    }


def _use_blob_store() -> bool:
    if BlobServiceClient is None:
        return False
    return bool(_connection_string())


def _connection_string() -> str:
    value = os.environ.get("AzureWebJobsStorage", "").strip()
    if not value or value == "UseDevelopmentStorage=true":
        return ""
    return value


def _blob_service() -> BlobServiceClient:
    return BlobServiceClient.from_connection_string(_connection_string())


def _container_client():
    service = _blob_service()
    client = service.get_container_client(CONTAINER_NAME)
    try:
        client.create_container()
    except Exception:
        pass
    return client


def _record_blob_event(event: dict) -> None:
    client = _container_client()
    stamp = str(event.get("recorded_at", datetime.now(timezone.utc).isoformat()))
    safe_stamp = stamp.replace(":", "-").replace("+", "_")
    blob_name = f"events/{safe_stamp}-{event.get('event_id', 'event')}.json"
    client.upload_blob(blob_name, json.dumps(event, indent=2), overwrite=True)


def _record_local_event(event: dict) -> None:
    events = _load_local_events()
    events.insert(0, event)
    LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_STORE, "w") as f:
        json.dump({"events": events}, f, indent=2)


def _load_blob_events(limit: int) -> List[dict]:
    client = _container_client()
    blobs = sorted(
        client.list_blobs(name_starts_with="events/"),
        key=lambda blob: blob.last_modified or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    events: List[dict] = []
    for blob in blobs[:limit]:
        try:
            raw = client.download_blob(blob.name).readall()
            events.append(json.loads(raw))
        except Exception:
            continue
    return events


def _load_local_events() -> List[dict]:
    if not LOCAL_STORE.exists():
        return []
    try:
        with open(LOCAL_STORE, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    events = data.get("events")
    return events if isinstance(events, list) else []
