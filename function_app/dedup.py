"""
Crassus 2.5 -- Signal deduplication.

Prevents duplicate orders when TradingView fires the same webhook twice.
Uses an in-memory cache with TTL (time-to-live) expiry.

A signal is considered duplicate if the same (ticker, side, strategy, mode,
price) combination arrives within the TTL window (default 60 seconds).
"""

import hashlib
import os
import threading
import time
from typing import Optional


# TTL in seconds -- how long a signal fingerprint is remembered.
_DEFAULT_TTL = 60


def _get_dedup_ttl() -> int:
    """Return dedup TTL in seconds from env or default."""
    return int(os.environ.get("DEDUP_TTL_SECONDS", str(_DEFAULT_TTL)))


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
) -> bool:
    """Module-level convenience: check if a signal is a duplicate."""
    return _dedup.is_duplicate(ticker, side, strategy, mode, price)


def reset_dedup_cache() -> None:
    """Reset the global dedup cache (for testing)."""
    _dedup.clear()
