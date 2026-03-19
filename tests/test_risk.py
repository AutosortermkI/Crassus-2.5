"""
Tests for risk sizing calculations.
"""
import pytest
from unittest.mock import MagicMock

from risk import (
    compute_options_qty,
    compute_stock_qty,
    validate_buying_power,
    validate_position_limit,
    get_account_equity,
    InsufficientBuyingPowerError,
    MaxPositionsExceededError,
)


# ---------------------------------------------------------------------------
# Options quantity computation
# ---------------------------------------------------------------------------

class TestOptionsQty:
    """compute_options_qty: qty = max_risk / (stop_distance * 100)."""

    def test_basic_sizing(self):
        """$50 risk, 10% SL, $5.00 premium -> 1 contract."""
        assert compute_options_qty(50.0, 10.0, 5.00) == 1

    def test_larger_risk(self):
        """$200 risk, 10% SL, $2.00 premium -> 10 contracts."""
        assert compute_options_qty(200.0, 10.0, 2.00) == 10

    def test_fractional_rounds_down(self):
        """Fractional contracts round down to int."""
        assert compute_options_qty(75.0, 10.0, 5.00) == 1

    def test_minimum_one_contract(self):
        """Even tiny risk returns at least 1."""
        assert compute_options_qty(1.0, 50.0, 100.0) >= 1

    def test_zero_premium_returns_one(self):
        assert compute_options_qty(50.0, 10.0, 0.0) == 1

    def test_zero_stop_loss_pct_returns_one(self):
        assert compute_options_qty(50.0, 0.0, 5.0) == 1

    def test_negative_premium_returns_one(self):
        assert compute_options_qty(50.0, 10.0, -1.0) == 1

    def test_high_risk_many_contracts(self):
        """$500 risk, 20% SL, $1.00 premium -> 25 contracts."""
        assert compute_options_qty(500.0, 20.0, 1.00) == 25


# ---------------------------------------------------------------------------
# Stock quantity -- fixed mode
# ---------------------------------------------------------------------------

class TestStockQtyFixed:
    """compute_stock_qty in fixed mode."""

    def test_default_qty(self):
        assert compute_stock_qty() >= 1

    def test_fixed_mode_ignores_equity(self, monkeypatch):
        monkeypatch.setenv("STOCK_SIZING_MODE", "fixed")
        monkeypatch.setenv("DEFAULT_STOCK_QTY", "5")
        assert compute_stock_qty(entry_price=100.0, stop_loss_pct=1.0, account_equity=50000) == 5

    def test_fixed_mode_default(self, monkeypatch):
        monkeypatch.delenv("STOCK_SIZING_MODE", raising=False)
        monkeypatch.setenv("DEFAULT_STOCK_QTY", "3")
        assert compute_stock_qty() == 3


# ---------------------------------------------------------------------------
# Stock quantity -- risk_pct mode
# ---------------------------------------------------------------------------

class TestStockQtyRiskPct:
    """compute_stock_qty in risk_pct mode."""

    def test_risk_pct_basic(self, monkeypatch):
        """$100k equity, 1% risk, $200 stock, 1% SL -> risk $1000, $2/share risk -> 500 shares."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.setenv("RISK_PCT_OF_EQUITY", "1.0")
        qty = compute_stock_qty(entry_price=200.0, stop_loss_pct=1.0, account_equity=100000.0)
        assert qty == 500

    def test_risk_pct_rounds_down(self, monkeypatch):
        """$50k equity, 0.5% risk, $150 stock, 0.8% SL -> risk $250, $1.20/share -> 208."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.setenv("RISK_PCT_OF_EQUITY", "0.5")
        qty = compute_stock_qty(entry_price=150.0, stop_loss_pct=0.8, account_equity=50000.0)
        # risk_dollars = 50000 * 0.005 = 250
        # dollar_risk_per_share = 150 * 0.008 = 1.20
        # qty = 250 / 1.20 = 208.33 -> 208
        assert qty == 208

    def test_risk_pct_minimum_one(self, monkeypatch):
        """Very small account still trades at least 1 share."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.setenv("RISK_PCT_OF_EQUITY", "0.1")
        qty = compute_stock_qty(entry_price=500.0, stop_loss_pct=2.0, account_equity=100.0)
        assert qty == 1

    def test_risk_pct_falls_back_without_equity(self, monkeypatch):
        """Falls back to fixed if account_equity is None."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.setenv("RISK_PCT_OF_EQUITY", "1.0")
        monkeypatch.setenv("DEFAULT_STOCK_QTY", "2")
        qty = compute_stock_qty(entry_price=100.0, stop_loss_pct=1.0, account_equity=None)
        assert qty == 2

    def test_risk_pct_falls_back_without_config(self, monkeypatch):
        """Falls back to fixed if RISK_PCT_OF_EQUITY is not set."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.delenv("RISK_PCT_OF_EQUITY", raising=False)
        monkeypatch.setenv("DEFAULT_STOCK_QTY", "7")
        qty = compute_stock_qty(entry_price=100.0, stop_loss_pct=1.0, account_equity=50000.0)
        assert qty == 7

    def test_risk_pct_falls_back_zero_sl(self, monkeypatch):
        """Falls back if stop_loss_pct is 0."""
        monkeypatch.setenv("STOCK_SIZING_MODE", "risk_pct")
        monkeypatch.setenv("RISK_PCT_OF_EQUITY", "1.0")
        monkeypatch.setenv("DEFAULT_STOCK_QTY", "4")
        qty = compute_stock_qty(entry_price=100.0, stop_loss_pct=0.0, account_equity=50000.0)
        assert qty == 4


# ---------------------------------------------------------------------------
# Buying power validation
# ---------------------------------------------------------------------------

class TestBuyingPower:

    def test_sufficient_buying_power(self):
        client = MagicMock()
        account = MagicMock()
        account.buying_power = "50000.00"
        client.get_account.return_value = account

        bp = validate_buying_power(client, 1000.0, "test-123")
        assert bp == 50000.0

    def test_insufficient_buying_power_raises(self):
        client = MagicMock()
        account = MagicMock()
        account.buying_power = "500.00"
        client.get_account.return_value = account

        with pytest.raises(InsufficientBuyingPowerError, match="Insufficient"):
            validate_buying_power(client, 1000.0, "test-123")


# ---------------------------------------------------------------------------
# Position limit validation
# ---------------------------------------------------------------------------

class TestPositionLimit:

    def test_within_limit(self, monkeypatch):
        monkeypatch.setenv("MAX_OPEN_POSITIONS", "10")
        client = MagicMock()
        client.get_all_positions.return_value = [MagicMock()] * 5

        count = validate_position_limit(client, "test-123")
        assert count == 5

    def test_at_limit_raises(self, monkeypatch):
        monkeypatch.setenv("MAX_OPEN_POSITIONS", "5")
        client = MagicMock()
        client.get_all_positions.return_value = [MagicMock()] * 5

        with pytest.raises(MaxPositionsExceededError):
            validate_position_limit(client, "test-123")

    def test_above_limit_raises(self, monkeypatch):
        monkeypatch.setenv("MAX_OPEN_POSITIONS", "3")
        client = MagicMock()
        client.get_all_positions.return_value = [MagicMock()] * 7

        with pytest.raises(MaxPositionsExceededError):
            validate_position_limit(client, "test-123")


# ---------------------------------------------------------------------------
# Account equity query
# ---------------------------------------------------------------------------

class TestAccountEquity:

    def test_returns_equity(self):
        client = MagicMock()
        account = MagicMock()
        account.equity = "75000.50"
        client.get_account.return_value = account

        assert get_account_equity(client) == 75000.50
