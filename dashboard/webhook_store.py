"""
Dashboard-local persistence for TradingView webhook snapshots.

The dashboard acts as the webhook inbox, so it needs a small durable store
for recent alerts and an easy way to derive "active" webhook signatures for
the sidebar snapshot.
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

STORE_PATH = Path(__file__).resolve().parent.parent / ".dashboard_webhooks.json"
_LOCK = threading.Lock()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_events() -> List[dict]:
    if not STORE_PATH.exists():
        return []
    try:
        with open(STORE_PATH, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    events = data.get("events")
    return events if isinstance(events, list) else []


def _save_events(events: List[dict]) -> None:
    with open(STORE_PATH, "w") as f:
        json.dump({"events": events}, f, indent=2)


def record_event(
    event: Dict[str, Any],
    max_snapshots: int = 50,
) -> Dict[str, Any]:
    """Persist a webhook event and retain only the newest N snapshots."""
    max_snapshots = max(1, int(max_snapshots))

    with _LOCK:
        events = _load_events()
        events.insert(0, event)
        trimmed = events[:max_snapshots]
        _save_events(trimmed)

    return event


def clear_events() -> None:
    """Remove all stored webhook snapshots."""
    with _LOCK:
        _save_events([])


def get_activity_snapshot(
    active_window_minutes: int = 60,
    recent_limit: int = 20,
) -> dict:
    """Return recent events plus grouped active webhook signatures."""
    active_window_minutes = max(1, int(active_window_minutes))
    recent_limit = max(1, int(recent_limit))

    with _LOCK:
        events = _load_events()

    latest_event = events[0] if events else None
    recent_events = events[:recent_limit]

    cutoff = _utcnow() - timedelta(minutes=active_window_minutes)
    grouped: "OrderedDict[str, dict]" = OrderedDict()

    for event in events:
        parsed = event.get("parsed") or {}
        signature = event.get("signature") or event.get("id")
        received_at = _parse_timestamp(event.get("received_at"))
        if received_at is None or received_at < cutoff:
            continue

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
                "last_status": _derive_status(event),
                "last_event_id": event.get("id"),
            }

        grouped[signature]["count"] += 1

    active_webhooks = list(grouped.values())

    return {
        "latest_event": latest_event,
        "recent_events": recent_events,
        "active_webhooks": active_webhooks,
        "counts": {
            "recent_events": len(events),
            "active_webhooks": len(active_webhooks),
        },
    }


def build_signature(parsed: Optional[Dict[str, Any]]) -> Optional[str]:
    """Create a stable grouping key for a parsed TradingView signal."""
    if not parsed:
        return None

    ticker = parsed.get("ticker")
    side = parsed.get("side")
    strategy = parsed.get("strategy")
    mode = parsed.get("mode")
    if not all([ticker, side, strategy, mode]):
        return None

    return f"{ticker}:{side}:{strategy}:{mode}"


def _parse_timestamp(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _derive_status(event: Dict[str, Any]) -> str:
    forward = event.get("forward") or {}
    if event.get("parse_error"):
        return "parse_error"
    if not forward:
        return "stored"
    if forward.get("ok"):
        return "forwarded"
    if forward.get("target") == "none":
        return "stored_only"
    return "forward_error"
