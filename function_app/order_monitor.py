"""
Crassus 2.5 -- Order submission retry and status monitoring.

Provides:
  - ``submit_with_retry``: Wraps Alpaca order submission with exponential
    backoff for transient API failures.
  - ``check_stock_orders``: Timer-triggered function that polls open stock
    orders and logs status changes (fills, rejections, cancellations).
"""

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Callable, TypeVar

from alpaca.common.exceptions import APIError

from utils import get_logger, log_structured

logger = get_logger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3
_BASE_BACKOFF = 2  # seconds


def submit_with_retry(
    submit_fn: Callable[[], T],
    correlation_id: str,
    max_retries: int = _MAX_RETRIES,
    base_backoff: float = _BASE_BACKOFF,
) -> T:
    """Call ``submit_fn()`` with exponential backoff on transient failures.

    Retries on:
      - APIError with HTTP 5xx (server-side Alpaca issues)
      - APIError with HTTP 429 (rate limited)
      - ConnectionError / TimeoutError (network issues)

    Does NOT retry on:
      - 4xx errors (bad request, insufficient funds, etc.)

    Returns:
        The return value of ``submit_fn()``.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            return submit_fn()
        except APIError as e:
            last_exception = e
            status = e.status_code if e.status_code is not None else _extract_status(e)
            if status is not None and 400 <= status < 500 and status != 429:
                # Client error (not rate-limit) -- don't retry
                log_structured(
                    logger, logging.ERROR,
                    f"Order rejected (non-retryable): {e}",
                    correlation_id,
                    attempt=attempt,
                    status=status,
                )
                raise
            # Server error or rate limit -- retry
            _backoff_and_log(attempt, max_retries, base_backoff, correlation_id, e)

        except (ConnectionError, TimeoutError, OSError) as e:
            last_exception = e
            _backoff_and_log(attempt, max_retries, base_backoff, correlation_id, e)

    raise last_exception  # type: ignore[misc]


def _backoff_and_log(
    attempt: int,
    max_retries: int,
    base_backoff: float,
    correlation_id: str,
    error: Exception,
) -> None:
    if attempt >= max_retries:
        log_structured(
            logger, logging.ERROR,
            f"All {max_retries + 1} attempts exhausted: {error}",
            correlation_id,
        )
        return
    wait = base_backoff * (2 ** attempt)
    log_structured(
        logger, logging.WARNING,
        f"Retrying after error (attempt {attempt + 1}/{max_retries + 1}): {error}",
        correlation_id,
        wait_seconds=wait,
    )
    time.sleep(wait)


def _extract_status(exc: APIError) -> int | None:
    """Best-effort extraction of HTTP status from APIError."""
    if hasattr(exc, "status_code"):
        return exc.status_code
    msg = str(exc)
    for code in (429, 500, 502, 503, 504):
        if str(code) in msg:
            return code
    return None


# ---------------------------------------------------------------------------
# Order status monitoring
# ---------------------------------------------------------------------------

def check_stock_orders(trading_client, correlation_id: str) -> list[dict]:
    """Poll open stock orders and log their current status.

    Returns a list of status change events for logging/alerting.
    """
    try:
        orders = trading_client.get_orders()
    except Exception as e:
        log_structured(
            logger, logging.ERROR,
            f"Failed to fetch orders: {e}",
            correlation_id,
        )
        return []

    events = []
    for order in orders:
        status = str(order.status)
        symbol = str(order.symbol)

        # Log notable statuses
        if status in ("filled", "partially_filled", "canceled", "expired", "rejected"):
            event = {
                "order_id": str(order.id),
                "symbol": symbol,
                "status": status,
                "side": str(order.side),
                "qty": str(order.qty),
                "filled_qty": str(getattr(order, "filled_qty", "0")),
                "filled_avg_price": str(getattr(order, "filled_avg_price", None)),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            events.append(event)

            log_structured(
                logger, logging.INFO,
                f"Order status: {status}",
                correlation_id,
                **event,
            )

    if not events:
        log_structured(
            logger, logging.DEBUG,
            "No notable order status changes",
            correlation_id,
            total_open_orders=len(orders),
        )

    return events


def cancel_stale_orders(
    trading_client,
    max_age_minutes: int,
    correlation_id: str,
) -> list[str]:
    """Cancel unfilled orders older than ``max_age_minutes``.

    Only cancels orders with status ``new`` or ``accepted`` (not partially filled).

    Returns:
        List of cancelled order IDs.
    """
    max_age = int(os.environ.get("STALE_ORDER_MINUTES", str(max_age_minutes)))
    try:
        orders = trading_client.get_orders()
    except Exception as e:
        log_structured(
            logger, logging.ERROR,
            f"Failed to fetch orders for stale check: {e}",
            correlation_id,
        )
        return []

    cancelled = []
    now = datetime.now(timezone.utc)

    for order in orders:
        status = str(order.status)
        if status not in ("new", "accepted", "pending_new"):
            continue

        created = order.created_at
        if created is None:
            continue

        # Ensure timezone-aware comparison
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)

        age_minutes = (now - created).total_seconds() / 60.0
        if age_minutes >= max_age:
            try:
                trading_client.cancel_order_by_id(str(order.id))
                cancelled.append(str(order.id))
                log_structured(
                    logger, logging.INFO,
                    "Cancelled stale order",
                    correlation_id,
                    order_id=str(order.id),
                    symbol=str(order.symbol),
                    age_minutes=round(age_minutes, 1),
                )
            except Exception as e:
                log_structured(
                    logger, logging.ERROR,
                    f"Failed to cancel stale order {order.id}: {e}",
                    correlation_id,
                )

    return cancelled
