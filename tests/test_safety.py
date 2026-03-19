"""Tests for live trading safety gate."""
import pytest
from safety import check_live_trading_gate, LiveTradingNotConfirmedError, is_paper_mode


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
