"""Tests for signal deduplication."""
import time
from dedup import SignalDedup, is_duplicate_signal, reset_dedup_cache


class TestSignalDedup:
    """SignalDedup: thread-safe TTL-based deduplication."""

    def test_first_signal_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        assert not dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)

    def test_same_signal_is_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)

    def test_different_ticker_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert not dedup.is_duplicate("MSFT", "buy", "bmr", "stock", 150.0)

    def test_different_side_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert not dedup.is_duplicate("AAPL", "sell", "bmr", "stock", 150.0)

    def test_different_strategy_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert not dedup.is_duplicate("AAPL", "buy", "lc", "stock", 150.0)

    def test_different_price_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert not dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 151.0)

    def test_expired_signal_is_not_duplicate(self):
        dedup = SignalDedup(ttl=0)  # Immediate expiry
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        time.sleep(0.01)
        assert not dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)

    def test_clear_resets_cache(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        dedup.clear()
        assert not dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)

    def test_floating_point_price_rounded(self):
        """Prices that round to the same cent are considered equal."""
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.004)
        assert dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.001)

    def test_different_mode_is_not_duplicate(self):
        dedup = SignalDedup(ttl=60)
        dedup.is_duplicate("AAPL", "buy", "bmr", "stock", 150.0)
        assert not dedup.is_duplicate("AAPL", "buy", "bmr", "options", 150.0)


class TestModuleLevelDedup:
    """Module-level convenience functions."""

    def test_is_duplicate_signal_and_reset(self):
        reset_dedup_cache()
        assert not is_duplicate_signal("TSLA", "buy", "lc", "stock", 200.0)
        assert is_duplicate_signal("TSLA", "buy", "lc", "stock", 200.0)
        reset_dedup_cache()
        assert not is_duplicate_signal("TSLA", "buy", "lc", "stock", 200.0)
