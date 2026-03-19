"""Tests for order submission retry and status monitoring."""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

from alpaca.common.exceptions import APIError

from order_monitor import submit_with_retry, check_stock_orders, cancel_stale_orders


def _make_api_error(message: str, status_code: int) -> APIError:
    """Create an APIError with a mocked HTTP status code."""
    http_error = MagicMock()
    http_error.response.status_code = status_code
    return APIError(message, http_error=http_error)


class TestSubmitWithRetry:

    def test_success_on_first_attempt(self):
        fn = MagicMock(return_value="order-123")
        result = submit_with_retry(fn, "corr-1")
        assert result == "order-123"
        assert fn.call_count == 1

    def test_retries_on_connection_error(self):
        fn = MagicMock(side_effect=[ConnectionError("timeout"), "order-456"])
        with patch("order_monitor.time.sleep"):
            result = submit_with_retry(fn, "corr-2", max_retries=2, base_backoff=0)
        assert result == "order-456"
        assert fn.call_count == 2

    def test_raises_after_max_retries(self):
        fn = MagicMock(side_effect=ConnectionError("timeout"))
        with patch("order_monitor.time.sleep"):
            with pytest.raises(ConnectionError):
                submit_with_retry(fn, "corr-3", max_retries=2, base_backoff=0)
        assert fn.call_count == 3  # initial + 2 retries

    def test_no_retry_on_4xx_api_error(self):
        err = _make_api_error("Insufficient funds", 403)
        fn = MagicMock(side_effect=err)
        with pytest.raises(APIError):
            submit_with_retry(fn, "corr-4")
        assert fn.call_count == 1  # No retry

    def test_retry_on_429_rate_limit(self):
        err_429 = _make_api_error("Rate limited", 429)
        fn = MagicMock(side_effect=[err_429, "order-789"])
        with patch("order_monitor.time.sleep"):
            result = submit_with_retry(fn, "corr-5", max_retries=2, base_backoff=0)
        assert result == "order-789"

    def test_retry_on_500_server_error(self):
        err_500 = _make_api_error("Server error", 500)
        fn = MagicMock(side_effect=[err_500, "order-abc"])
        with patch("order_monitor.time.sleep"):
            result = submit_with_retry(fn, "corr-6", max_retries=2, base_backoff=0)
        assert result == "order-abc"


class TestCheckStockOrders:

    def test_returns_events_for_filled_orders(self):
        order = MagicMock()
        order.id = "o-1"
        order.symbol = "AAPL"
        order.status = "filled"
        order.side = "buy"
        order.qty = "10"
        order.filled_qty = "10"
        order.filled_avg_price = "150.25"

        client = MagicMock()
        client.get_orders.return_value = [order]

        events = check_stock_orders(client, "corr-7")
        assert len(events) == 1
        assert events[0]["status"] == "filled"
        assert events[0]["symbol"] == "AAPL"

    def test_ignores_new_orders(self):
        order = MagicMock()
        order.status = "new"
        order.symbol = "MSFT"

        client = MagicMock()
        client.get_orders.return_value = [order]

        events = check_stock_orders(client, "corr-8")
        assert len(events) == 0

    def test_handles_api_failure_gracefully(self):
        client = MagicMock()
        client.get_orders.side_effect = Exception("API down")

        events = check_stock_orders(client, "corr-9")
        assert events == []


class TestCancelStaleOrders:

    def test_cancels_old_unfilled_orders(self):
        order = MagicMock()
        order.id = "stale-1"
        order.symbol = "TSLA"
        order.status = "new"
        order.created_at = datetime.now(timezone.utc) - timedelta(minutes=200)

        client = MagicMock()
        client.get_orders.return_value = [order]

        cancelled = cancel_stale_orders(client, 120, "corr-10")
        assert "stale-1" in cancelled
        client.cancel_order_by_id.assert_called_once_with("stale-1")

    def test_does_not_cancel_recent_orders(self):
        order = MagicMock()
        order.id = "fresh-1"
        order.symbol = "AAPL"
        order.status = "new"
        order.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)

        client = MagicMock()
        client.get_orders.return_value = [order]

        cancelled = cancel_stale_orders(client, 120, "corr-11")
        assert cancelled == []

    def test_does_not_cancel_partially_filled(self):
        order = MagicMock()
        order.id = "pf-1"
        order.symbol = "AAPL"
        order.status = "partially_filled"
        order.created_at = datetime.now(timezone.utc) - timedelta(minutes=200)

        client = MagicMock()
        client.get_orders.return_value = [order]

        cancelled = cancel_stale_orders(client, 120, "corr-12")
        assert cancelled == []
