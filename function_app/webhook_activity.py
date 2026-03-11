"""
Shared webhook activity store for Azure-first dashboard snapshots.

When deployed, this module writes each webhook event as a JSON blob into the
Function App storage account. Local development falls back to a file-based
store so the same dashboard APIs keep working without Azure infrastructure.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - exercised in environments without Azure SDK
    BlobServiceClient = None


CONTAINER_NAME = os.environ.get("WEBHOOK_ACTIVITY_CONTAINER", "webhook-activity")
LOCAL_STORE = Path(__file__).resolve().parent / ".webhook_activity.json"


def record_webhook_event(event: dict) -> None:
    """Persist a webhook activity event into Azure Blob Storage or local file."""
    if _use_blob_store():
        _record_blob_event(event)
        return
    _record_local_event(event)


def get_webhook_activity_snapshot(
    active_minutes: int = 60,
    recent_limit: int = 20,
) -> dict:
    """Return recent events and active webhook groups from shared storage."""
    events = _load_events(recent_limit=max(recent_limit, 50))
    return _build_snapshot(events, active_minutes=active_minutes, recent_limit=recent_limit)


def build_signature(parsed: Optional[dict]) -> Optional[str]:
    """Create a stable grouping key for a normalized webhook event."""
    if not parsed:
        return None
    keys = [parsed.get("ticker"), parsed.get("side"), parsed.get("strategy"), parsed.get("mode")]
    if not all(keys):
        return None
    return ":".join(str(value) for value in keys)


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
    stamp = str(event.get("received_at", datetime.now(timezone.utc).isoformat()))
    safe_stamp = stamp.replace(":", "-").replace("+", "_")
    blob_name = f"{safe_stamp}-{event.get('id', 'event')}.json"
    client.upload_blob(blob_name, json.dumps(event, indent=2), overwrite=True)


def _record_local_event(event: dict) -> None:
    events = _load_local_events()
    events.insert(0, event)
    with open(LOCAL_STORE, "w") as f:
        json.dump({"events": events[:100]}, f, indent=2)


def _load_events(recent_limit: int) -> List[dict]:
    if _use_blob_store():
        return _load_blob_events(recent_limit)
    return _load_local_events()[:recent_limit]


def _load_blob_events(recent_limit: int) -> List[dict]:
    client = _container_client()
    blobs = sorted(
        client.list_blobs(),
        key=lambda blob: blob.last_modified or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    events: List[dict] = []
    for blob in blobs[:recent_limit]:
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


def _build_snapshot(events: List[dict], active_minutes: int, recent_limit: int) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(active_minutes)))
    grouped: "OrderedDict[str, dict]" = OrderedDict()

    for event in events:
        received_at = _parse_time(event.get("received_at"))
        if received_at is None or received_at < cutoff:
            continue

        signature = event.get("signature") or event.get("id")
        parsed = event.get("parsed") or {}
        if signature not in grouped:
            grouped[signature] = {
                "signature": signature,
                "ticker": parsed.get("ticker", "Unknown"),
                "side": parsed.get("side", "unknown"),
                "strategy": parsed.get("strategy", "unparsed"),
                "mode": parsed.get("mode", "unknown"),
                "last_price": parsed.get("price"),
                "last_seen": event.get("received_at"),
                "count": 0,
                "last_status": _last_status(event),
                "last_event_id": event.get("id"),
            }
        grouped[signature]["count"] += 1

    return {
        "latest_event": events[0] if events else None,
        "recent_events": events[:recent_limit],
        "active_webhooks": list(grouped.values()),
        "counts": {
            "recent_events": len(events[:recent_limit]),
            "active_webhooks": len(grouped),
        },
    }


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _last_status(event: Dict[str, Any]) -> str:
    if event.get("parse_error"):
        return "parse_error"
    execution = event.get("execution") or {}
    if execution.get("ok"):
        return "executed"
    if execution.get("status_code"):
        return f"http_{execution['status_code']}"
    if execution.get("message"):
        return execution.get("message")
    return "stored"
