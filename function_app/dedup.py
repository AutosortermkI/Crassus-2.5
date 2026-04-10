"""
Crassus 2.5 -- Signal deduplication.

Prevents duplicate orders when TradingView fires the same webhook twice.
Uses an in-memory cache with TTL (time-to-live) expiry, and when running in
Azure, can back the dedup key with shared Blob Storage so duplicates are still
rejected across Function App instances.

A signal is considered duplicate if the same (ticker, side, strategy, mode,
price) combination arrives within the TTL window (default 60 seconds).
"""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from utils import get_logger, log_structured

try:
    from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
    from azure.storage.blob import BlobServiceClient
except ImportError:  # pragma: no cover - exercised in environments without Azure SDK
    BlobServiceClient = None
    ResourceExistsError = ResourceNotFoundError = None


logger = get_logger(__name__)


# TTL in seconds -- how long a signal fingerprint is remembered.
_DEFAULT_TTL = 60
CONTAINER_NAME = os.environ.get("SIGNAL_DEDUP_CONTAINER", "signal-dedup")


def _get_dedup_ttl() -> int:
    """Return dedup TTL in seconds from env or default."""
    return int(os.environ.get("DEDUP_TTL_SECONDS", str(_DEFAULT_TTL)))


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


def _blob_client(fingerprint: str):
    return _container_client().get_blob_client(f"{fingerprint}.json")


def _parse_expiry(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _shared_check_and_register(
    fingerprint: str,
    ttl_seconds: int,
    correlation_id: str = "",
) -> bool:
    """Check and register a dedup fingerprint in shared Blob Storage.

    Returns:
        True if the signal already exists and has not expired.
        False if this call successfully registered a fresh fingerprint or if
        shared storage is unavailable.
    """
    if not _use_blob_store():
        return False

    client = _blob_client(fingerprint)
    now = datetime.now(timezone.utc)

    try:
        props = client.get_blob_properties()
        expires_at = _parse_expiry((props.metadata or {}).get("expires_at"))
        if expires_at is not None and expires_at > now:
            return True
        try:
            client.delete_blob()
        except Exception:
            pass
    except Exception as exc:
        if ResourceNotFoundError and isinstance(exc, ResourceNotFoundError):
            pass
        else:
            log_structured(
                logger, logging.WARNING,
                "Shared dedup lookup unavailable; falling back to local cache",
                correlation_id,
                error=str(exc),
            )
            return False

    expires_at = now + timedelta(seconds=max(ttl_seconds, 0))
    payload = json.dumps({
        "fingerprint": fingerprint,
        "expires_at": expires_at.isoformat(),
    })

    try:
        client.upload_blob(
            payload,
            overwrite=False,
            metadata={"expires_at": expires_at.isoformat()},
        )
        return False
    except Exception as exc:
        if ResourceExistsError and isinstance(exc, ResourceExistsError):
            return True
        log_structured(
            logger, logging.WARNING,
            "Shared dedup registration failed; falling back to local cache",
            correlation_id,
            error=str(exc),
        )
        return False


class SignalDedup:
    """Thread-safe signal deduplication cache with TTL expiry."""

    def __init__(self, ttl: Optional[int] = None):
        self._ttl = ttl if ttl is not None else _get_dedup_ttl()
        self._cache: dict[str, float] = {}  # fingerprint -> expiry timestamp
        self._lock = threading.Lock()

    def _fingerprint(
        self,
        ticker: str,
        side: str,
        strategy: str,
        mode: str,
        price: float,
    ) -> str:
        """Create a stable hash of the signal parameters."""
        # Round price to cents to avoid floating-point noise
        raw = f"{ticker}:{side}:{strategy}:{mode}:{round(price, 2)}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _evict_expired(self) -> None:
        """Remove entries whose TTL has elapsed. Must hold _lock."""
        now = time.monotonic()
        expired = [k for k, exp in self._cache.items() if now >= exp]
        for k in expired:
            del self._cache[k]

    def is_duplicate(
        self,
        ticker: str,
        side: str,
        strategy: str,
        mode: str,
        price: float,
        correlation_id: str = "",
    ) -> bool:
        """Check if a signal is a duplicate. If not, register it.

        Returns:
            True if the signal was already seen within the TTL window.
        """
        fp = self._fingerprint(ticker, side, strategy, mode, price)

        with self._lock:
            self._evict_expired()
            now = time.monotonic()

            if fp in self._cache:
                return True

            if _shared_check_and_register(fp, self._ttl, correlation_id):
                self._cache[fp] = now + self._ttl
                return True

            # Register this signal
            self._cache[fp] = now + self._ttl
            return False

    def clear(self) -> None:
        """Clear all cached fingerprints (useful for testing)."""
        with self._lock:
            self._cache.clear()


# Module-level singleton -- shared across requests in the same process.
_dedup = SignalDedup()


def is_duplicate_signal(
    ticker: str,
    side: str,
    strategy: str,
    mode: str,
    price: float,
    correlation_id: str = "",
) -> bool:
    """Module-level convenience: check if a signal is a duplicate."""
    return _dedup.is_duplicate(ticker, side, strategy, mode, price, correlation_id)


def reset_dedup_cache() -> None:
    """Reset the global dedup cache (for testing)."""
    _dedup.clear()
