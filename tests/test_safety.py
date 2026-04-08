"""Tests for live trading safety gate."""
from unittest.mock import MagicMock

import pytest
from safety import (
    check_daily_loss_limit,
    check_live_trading_gate,
    check_operator_halt,
    check_trading_safety,
    DailyLossLimitExceededError,
    LiveTradingNotConfirmedError,
    TradingHaltedError,
    is_paper_mode,
)


class TestLiveTradingSafetyGate:

    def test_paper_mode_always_passes(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "true")
        assert check_live_trading_gate("test-123") is True

    def test_paper_mode_default(self, monkeypatch):
        monkeypatch.delenv("ALPACA_PAPER", raising=False)
        assert check_live_trading_gate("test-123") is True

    def test_live_mode_without_confirmation_raises(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "false")
        monkeypatch.delenv("LIVE_TRADING_CONFIRMED", raising=False)
        with pytest.raises(LiveTradingNotConfirmedError):
            check_live_trading_gate("test-123")

    def test_live_mode_with_wrong_confirmation_raises(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "false")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "no")
        with pytest.raises(LiveTradingNotConfirmedError):
            check_live_trading_gate("test-123")

    def test_live_mode_with_confirmation_passes(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "false")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "yes")
        assert check_live_trading_gate("test-123") is True

    def test_live_mode_confirmation_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "false")
        monkeypatch.setenv("LIVE_TRADING_CONFIRMED", "YES")
        # "YES" lowered is "yes" -- should pass
        assert check_live_trading_gate("test-123") is True


class TestIsPaperMode:

    def test_default_is_paper(self, monkeypatch):
        monkeypatch.delenv("ALPACA_PAPER", raising=False)
        assert is_paper_mode() is True

    def test_explicit_paper(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "true")
        assert is_paper_mode() is True

    def test_live_mode(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "false")
        assert is_paper_mode() is False


class TestOperatorHalt:

    def test_trading_halt_disabled_passes(self, monkeypatch):
        monkeypatch.delenv("TRADING_HALTED", raising=False)
        assert check_operator_halt("test-123") is True

    def test_trading_halt_enabled_raises(self, monkeypatch):
        monkeypatch.setenv("TRADING_HALTED", "true")
        monkeypatch.setenv("TRADING_HALTED_REASON", "maintenance")
        with pytest.raises(TradingHaltedError, match="maintenance"):
            check_operator_halt("test-123")


class TestDailyLossLimit:

    def test_no_limits_configured_passes(self, monkeypatch):
        monkeypatch.delenv("MAX_DAILY_LOSS_DOLLARS", raising=False)
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        client = MagicMock()
        assert check_daily_loss_limit(client, "test-123") is True
        client.get_account.assert_not_called()

    def test_dollar_limit_raises(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_LOSS_DOLLARS", "100")
        client = MagicMock()
        account = MagicMock()
        account.equity = "9900"
        account.last_equity = "10050"
        client.get_account.return_value = account

        with pytest.raises(DailyLossLimitExceededError, match="\\$150.00"):
            check_daily_loss_limit(client, "test-123")

    def test_pct_limit_raises(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "2.0")
        client = MagicMock()
        account = MagicMock()
        account.equity = "9700"
        account.last_equity = "10000"
        client.get_account.return_value = account

        with pytest.raises(DailyLossLimitExceededError, match="3.00%"):
            check_daily_loss_limit(client, "test-123")

    def test_loss_below_threshold_passes(self, monkeypatch):
        monkeypatch.setenv("MAX_DAILY_LOSS_DOLLARS", "500")
        monkeypatch.setenv("MAX_DAILY_LOSS_PCT", "5")
        client = MagicMock()
        account = MagicMock()
        account.equity = "9900"
        account.last_equity = "10000"
        client.get_account.return_value = account

        assert check_daily_loss_limit(client, "test-123") is True


class TestTradingSafety:

    def test_combined_safety_checks(self, monkeypatch):
        monkeypatch.setenv("ALPACA_PAPER", "true")
        monkeypatch.delenv("TRADING_HALTED", raising=False)
        monkeypatch.delenv("MAX_DAILY_LOSS_DOLLARS", raising=False)
        monkeypatch.delenv("MAX_DAILY_LOSS_PCT", raising=False)
        client = MagicMock()

        assert check_trading_safety(client, "test-123") is True
