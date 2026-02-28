"""
Tests for backtesting data loading.
"""

import os
import pytest
import tempfile
from datetime import datetime

from backtesting.data import (
    load_bars_csv,
    load_signals_csv,
    bars_from_dicts,
    _parse_timestamp,
)


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

class TestParseTimestamp:
    def test_iso_datetime(self):
        ts = _parse_timestamp("2024-01-02 09:30:00")
        assert ts == datetime(2024, 1, 2, 9, 30, 0)

    def test_iso_t_separator(self):
        ts = _parse_timestamp("2024-01-02T09:30:00")
        assert ts == datetime(2024, 1, 2, 9, 30, 0)

    def test_date_only(self):
        ts = _parse_timestamp("2024-01-02")
        assert ts == datetime(2024, 1, 2)

    def test_us_format(self):
        ts = _parse_timestamp("01/02/2024 09:30:00")
        assert ts == datetime(2024, 1, 2, 9, 30, 0)

    def test_invalid(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_timestamp("not-a-date")

    def test_whitespace_stripped(self):
        ts = _parse_timestamp("  2024-01-02  ")
        assert ts == datetime(2024, 1, 2)


# ---------------------------------------------------------------------------
# Bars CSV
# ---------------------------------------------------------------------------

class TestLoadBarsCSV:
    def test_basic_load(self, tmp_path):
        csv_file = tmp_path / "bars.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2024-01-02 09:30:00,150.00,151.25,149.80,150.50,1000000\n"
            "2024-01-02 09:31:00,150.50,152.00,150.00,151.75,500000\n"
        )
        bars = load_bars_csv(csv_file, ticker="AAPL")
        assert len(bars) == 2
        assert bars[0].open == 150.0
        assert bars[0].ticker == "AAPL"
        assert bars[1].close == 151.75

    def test_with_ticker_column(self, tmp_path):
        csv_file = tmp_path / "bars.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close,volume,ticker\n"
            "2024-01-02,100,101,99,100.5,1000,MSFT\n"
        )
        bars = load_bars_csv(csv_file)
        assert bars[0].ticker == "MSFT"

    def test_sorted_by_timestamp(self, tmp_path):
        csv_file = tmp_path / "bars.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2024-01-03,102,103,101,102.5,1000\n"
            "2024-01-02,100,101,99,100.5,1000\n"
        )
        bars = load_bars_csv(csv_file)
        assert bars[0].timestamp < bars[1].timestamp

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_bars_csv("/nonexistent/path.csv")

    def test_malformed_row(self, tmp_path):
        csv_file = tmp_path / "bars.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close,volume\n"
            "2024-01-02,not_a_number,101,99,100.5,1000\n"
        )
        with pytest.raises(ValueError, match="row 2"):
            load_bars_csv(csv_file)

    def test_missing_volume(self, tmp_path):
        csv_file = tmp_path / "bars.csv"
        csv_file.write_text(
            "timestamp,open,high,low,close\n"
            "2024-01-02,100,101,99,100.5\n"
        )
        bars = load_bars_csv(csv_file)
        assert bars[0].volume == 0.0


# ---------------------------------------------------------------------------
# bars_from_dicts
# ---------------------------------------------------------------------------

class TestBarsFromDicts:
    def test_basic(self):
        records = [
            {"timestamp": "2024-01-02", "open": 100, "high": 101, "low": 99, "close": 100.5},
            {"timestamp": "2024-01-03", "open": 101, "high": 102, "low": 100, "close": 101.5},
        ]
        bars = bars_from_dicts(records, ticker="AAPL")
        assert len(bars) == 2
        assert bars[0].ticker == "AAPL"

    def test_datetime_objects(self):
        records = [
            {"timestamp": datetime(2024, 1, 2), "open": 100, "high": 101, "low": 99, "close": 100.5},
        ]
        bars = bars_from_dicts(records)
        assert bars[0].timestamp == datetime(2024, 1, 2)

    def test_sorted(self):
        records = [
            {"timestamp": "2024-01-05", "open": 105, "high": 106, "low": 104, "close": 105.5},
            {"timestamp": "2024-01-02", "open": 100, "high": 101, "low": 99, "close": 100.5},
        ]
        bars = bars_from_dicts(records)
        assert bars[0].timestamp < bars[1].timestamp


# ---------------------------------------------------------------------------
# Signals CSV
# ---------------------------------------------------------------------------

class TestLoadSignalsCSV:
    def test_basic_load(self, tmp_path):
        csv_file = tmp_path / "signals.csv"
        csv_file.write_text(
            "timestamp,ticker,side,price,strategy,mode\n"
            "2024-01-05 10:00:00,AAPL,buy,150.25,bollinger_mean_reversion,stock\n"
            "2024-01-06 14:30:00,MSFT,sell,300.50,lorentzian_classification,options\n"
        )
        signals = load_signals_csv(csv_file)
        assert len(signals) == 2
        assert signals[0].ticker == "AAPL"
        assert signals[0].side == "buy"
        assert signals[1].mode == "options"

    def test_default_mode(self, tmp_path):
        csv_file = tmp_path / "signals.csv"
        csv_file.write_text(
            "timestamp,ticker,side,price,strategy\n"
            "2024-01-05,AAPL,buy,150.25,bollinger_mean_reversion\n"
        )
        signals = load_signals_csv(csv_file)
        assert signals[0].mode == "stock"

    def test_sorted_by_timestamp(self, tmp_path):
        csv_file = tmp_path / "signals.csv"
        csv_file.write_text(
            "timestamp,ticker,side,price,strategy\n"
            "2024-01-10,AAPL,sell,155.0,bollinger_mean_reversion\n"
            "2024-01-05,AAPL,buy,150.0,bollinger_mean_reversion\n"
        )
        signals = load_signals_csv(csv_file)
        assert signals[0].timestamp < signals[1].timestamp

    def test_ticker_uppercased(self, tmp_path):
        csv_file = tmp_path / "signals.csv"
        csv_file.write_text(
            "timestamp,ticker,side,price,strategy\n"
            "2024-01-05,aapl,buy,150.0,bollinger_mean_reversion\n"
        )
        signals = load_signals_csv(csv_file)
        assert signals[0].ticker == "AAPL"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_signals_csv("/nonexistent/signals.csv")
