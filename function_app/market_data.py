"""
Tastytrade DXLink market-data worker foundation and quote cache.

This module is intentionally worker-oriented. It provides the pieces needed by
an Azure Container App or WebJob to fetch quote tokens, connect to DXLink, and
persist latest quote state for the dashboard and paper ledger.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from tastytrade_orders import TastytradeAPIError, get_tastytrade_client

try:
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - optional in stripped local envs
    BlobServiceClient = None


CONTAINER_NAME = os.environ.get("MARKET_DATA_CONTAINER", "market-data")
LOCAL_STORE = Path(__file__).resolve().parent / ".market_data.json"
QUOTE_CACHE_BLOB = "quote_latest.json"
DEFAULT_STALE_SECONDS = 60


@dataclass(frozen=True)
class ApiQuoteToken:
    token: str
    dxlink_url: str
    fetched_at: datetime
    expires_at: datetime
    refresh_after: datetime
    raw: dict


def fetch_api_quote_token(client=None, now: Optional[datetime] = None) -> ApiQuoteToken:
    """Fetch a Tastytrade API quote token and compute refresh metadata."""
    client = client or get_tastytrade_client()
    fetched_at = now or datetime.now(timezone.utc)
    data = client._get("/api-quote-tokens")  # Tastytrade endpoint for DXLink quote auth.
    token_data = _first_payload(data)
    token = token_data.get("token") or token_data.get("api-quote-token")
    dxlink_url = token_data.get("dxlink-url") or token_data.get("dxlink_url")
    if not token or not dxlink_url:
        raise TastytradeAPIError("Tastytrade API quote token response did not include token and dxlink-url")

    expires_at = _parse_datetime(token_data.get("expires-at") or token_data.get("expires_at"))
    if expires_at is None:
        expires_at = fetched_at + timedelta(hours=24)
    refresh_after = expires_at - timedelta(minutes=30)
    return ApiQuoteToken(
        token=str(token),
        dxlink_url=str(dxlink_url),
        fetched_at=fetched_at,
        expires_at=expires_at,
        refresh_after=refresh_after,
        raw=token_data,
    )


def build_dxlink_subscription_messages(
    *,
    token: str,
    symbols: Iterable[str],
    channel: int = 1,
) -> List[dict]:
    """Build the initial DXLink SETUP/AUTH/FEED subscription messages."""
    normalized_symbols = _dedupe_symbols(symbols)
    return [
        {
            "type": "SETUP",
            "channel": 0,
            "version": "0.1-DXF-JS/0.3.0",
            "keepaliveTimeout": 60,
            "acceptKeepaliveTimeout": 60,
        },
        {
            "type": "AUTH",
            "channel": 0,
            "token": token,
        },
        {
            "type": "CHANNEL_REQUEST",
            "channel": channel,
            "service": "FEED",
            "parameters": {"contract": "AUTO"},
        },
        {
            "type": "FEED_SETUP",
            "channel": channel,
            "acceptAggregationPeriod": 0.1,
            "acceptDataFormat": "COMPACT",
            "acceptEventFields": {
                "Quote": ["eventType", "eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize", "time"],
                "Trade": ["eventType", "eventSymbol", "price", "size", "time"],
                "Summary": ["eventType", "eventSymbol", "openInterest", "dayVolume", "time"],
            },
        },
        {
            "type": "FEED_SUBSCRIPTION",
            "channel": channel,
            "add": [
                {"type": event_type, "symbol": symbol}
                for symbol in normalized_symbols
                for event_type in ("Quote", "Trade", "Summary")
            ],
        },
    ]


def normalize_market_event(event: Any) -> Optional[dict]:
    """Normalize one DXLink market event into the Crassus quote-cache schema."""
    if isinstance(event, list):
        event = _compact_event_to_dict(event)
    if not isinstance(event, dict):
        return None

    event_type = event.get("eventType") or event.get("event_type")
    symbol = event.get("eventSymbol") or event.get("symbol")
    if not event_type or not symbol:
        return None

    return {
        "source": "tastytrade_dxlink",
        "event_type": str(event_type),
        "symbol": str(symbol),
        "bid": _float_or_none(event.get("bidPrice") or event.get("bid")),
        "ask": _float_or_none(event.get("askPrice") or event.get("ask")),
        "last": _float_or_none(event.get("price") or event.get("last")),
        "bid_size": _int_or_none(event.get("bidSize") or event.get("bid_size")),
        "ask_size": _int_or_none(event.get("askSize") or event.get("ask_size")),
        "trade_size": _int_or_none(event.get("size") or event.get("trade_size")),
        "timestamp": str(event.get("time") or event.get("timestamp") or datetime.now(timezone.utc).isoformat()),
        "raw": event,
    }


def normalize_dxlink_message(message: Any) -> List[dict]:
    """Extract normalized quote records from a DXLink FEED_DATA message."""
    if isinstance(message, str):
        message = json.loads(message)
    if not isinstance(message, dict) or message.get("type") != "FEED_DATA":
        return []
    data = message.get("data") or []
    events = []
    for item in data:
        normalized = normalize_market_event(item)
        if normalized:
            events.append(normalized)
    return events


def record_quote(record: dict) -> dict:
    """Persist the latest quote/trade record for its symbol."""
    symbol = str(record.get("symbol") or "").strip()
    if not symbol:
        raise ValueError("Quote record requires symbol")
    cache = _load_cache()
    quote = dict(record)
    quote["updated_at"] = datetime.now(timezone.utc).isoformat()
    cache.setdefault("quotes", {})[symbol] = quote
    _save_cache(cache)
    return quote


def get_latest_quotes() -> Dict[str, dict]:
    """Return latest quote records keyed by symbol."""
    quotes = _load_cache().get("quotes")
    return quotes if isinstance(quotes, dict) else {}


def record_worker_status(**fields: Any) -> dict:
    """Persist current market-data worker status for dashboard visibility."""
    cache = _load_cache()
    worker = {
        "source": "tastytrade_dxlink",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    worker.update(fields)
    cache["worker"] = worker
    _save_cache(cache)
    return worker


def get_market_data_summary(now: Optional[datetime] = None) -> dict:
    """Return dashboard-friendly quote cache and worker status."""
    now = now or datetime.now(timezone.utc)
    cache = _load_cache()
    quotes = cache.get("quotes") if isinstance(cache.get("quotes"), dict) else {}
    worker = cache.get("worker") if isinstance(cache.get("worker"), dict) else {}
    stale_after = _stale_after_seconds()
    latest_at = _latest_quote_time(quotes)
    stale = True
    if latest_at is not None:
        stale = (now - latest_at).total_seconds() > stale_after
    if not quotes:
        status = "not_configured"
    elif stale:
        status = "stale"
    else:
        status = "ok"
    return {
        "status": status,
        "source": "tastytrade_dxlink",
        "connected": bool(worker.get("connected")) and not stale,
        "stale": stale,
        "stale_after_seconds": stale_after,
        "subscribed_symbols": sorted(quotes),
        "last_quote_at": latest_at.isoformat() if latest_at else "",
        "quote_count": len(quotes),
        "token_expires_at": worker.get("token_expires_at", ""),
        "dxlink_url": worker.get("dxlink_url", ""),
        "message": worker.get("message") or (
            "Quote cache is current." if status == "ok" else "No current DXLink quote cache is available."
        ),
    }


async def stream_market_data(symbols: Iterable[str], *, stop_after_messages: Optional[int] = None) -> None:
    """Connect to DXLink, subscribe to symbols, and persist normalized quote events."""
    import websockets

    quote_token = fetch_api_quote_token()
    record_worker_status(
        connected=False,
        token_expires_at=quote_token.expires_at.isoformat(),
        dxlink_url=quote_token.dxlink_url,
        message="Connecting to DXLink.",
    )

    message_count = 0
    async with websockets.connect(quote_token.dxlink_url) as websocket:
        for message in build_dxlink_subscription_messages(token=quote_token.token, symbols=symbols):
            await websocket.send(json.dumps(message))
        record_worker_status(
            connected=True,
            token_expires_at=quote_token.expires_at.isoformat(),
            dxlink_url=quote_token.dxlink_url,
            subscribed_symbols=_dedupe_symbols(symbols),
            message="Connected to DXLink.",
        )

        async for raw_message in websocket:
            message_count += 1
            for quote in normalize_dxlink_message(raw_message):
                record_quote(quote)
            if stop_after_messages is not None and message_count >= stop_after_messages:
                break


def run_worker_from_env() -> None:
    """CLI entry point for Azure WebJob/Container App execution."""
    symbols = _env_symbols()
    if not symbols:
        record_worker_status(connected=False, message="MARKET_DATA_WATCHLIST has no symbols.")
        return
    asyncio.run(stream_market_data(symbols))


def _first_payload(data: Any) -> dict:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list) and data["items"]:
            return data["items"][0]
        if isinstance(data.get("data"), dict):
            return _first_payload(data["data"])
        return data
    if isinstance(data, list) and data:
        return data[0]
    return {}


def _dedupe_symbols(symbols: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for symbol in symbols:
        normalized = str(symbol or "").strip().upper()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _compact_event_to_dict(event: list) -> dict:
    if not event:
        return {}
    event_type = event[0]
    if event_type == "Quote":
        keys = ["eventType", "eventSymbol", "bidPrice", "askPrice", "bidSize", "askSize", "time"]
    elif event_type == "Trade":
        keys = ["eventType", "eventSymbol", "price", "size", "time"]
    elif event_type == "Summary":
        keys = ["eventType", "eventSymbol", "openInterest", "dayVolume", "time"]
    else:
        keys = ["eventType", "eventSymbol"]
    return {key: event[index] for index, key in enumerate(keys) if index < len(event)}


def _latest_quote_time(quotes: dict) -> Optional[datetime]:
    latest = None
    for quote in quotes.values():
        if not isinstance(quote, dict):
            continue
        parsed = _parse_datetime(quote.get("timestamp") or quote.get("updated_at"))
        if parsed is not None and (latest is None or parsed > latest):
            latest = parsed
    return latest


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _float_or_none(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _stale_after_seconds() -> int:
    try:
        return max(1, int(os.environ.get("MARKET_DATA_STALE_SECONDS", str(DEFAULT_STALE_SECONDS))))
    except ValueError:
        return DEFAULT_STALE_SECONDS


def _env_symbols() -> List[str]:
    raw = os.environ.get("MARKET_DATA_WATCHLIST", "")
    return _dedupe_symbols(raw.replace(";", ",").split(","))


def _use_blob_store() -> bool:
    if BlobServiceClient is None:
        return False
    return bool(_connection_string())


def _connection_string() -> str:
    value = os.environ.get("AzureWebJobsStorage", "").strip()
    if not value or value == "UseDevelopmentStorage=true":
        return ""
    return value


def _container_client():
    service = BlobServiceClient.from_connection_string(_connection_string())
    client = service.get_container_client(CONTAINER_NAME)
    try:
        client.create_container()
    except Exception:
        pass
    return client


def _load_cache() -> dict:
    if _use_blob_store():
        return _load_blob_cache()
    if not LOCAL_STORE.exists():
        return {"quotes": {}, "worker": {}}
    try:
        with open(LOCAL_STORE, "r") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"quotes": {}, "worker": {}}
    return data if isinstance(data, dict) else {"quotes": {}, "worker": {}}


def _save_cache(cache: dict) -> None:
    if _use_blob_store():
        _container_client().upload_blob(QUOTE_CACHE_BLOB, json.dumps(cache, indent=2), overwrite=True)
        return
    LOCAL_STORE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCAL_STORE, "w") as f:
        json.dump(cache, f, indent=2)


def _load_blob_cache() -> dict:
    try:
        raw = _container_client().download_blob(QUOTE_CACHE_BLOB).readall()
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"quotes": {}, "worker": {}}
    except Exception:
        return {"quotes": {}, "worker": {}}


if __name__ == "__main__":
    run_worker_from_env()
