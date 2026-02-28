"""
Tests for backtesting Yahoo Finance historical data fetcher.

Uses mocked HTTP responses to avoid hitting Yahoo's API during tests.
"""

import pytest
from datetime import datetime, date
from unittest.mock import patch, MagicMock

from backtesting.yahoo_fetch import (
    fetch_bars,
    _parse_date,
    _ChartClient,
    YahooFetchError,
    VALID_INTERVALS,
)


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

class TestParseDate:
    def test_iso_string(self):
        dt = _parse_date("2024-01-15")
        assert dt == datetime(2024, 1, 15)

    def test_us_format(self):
        dt = _parse_date("01/15/2024")
        assert dt == datetime(2024, 1, 15)

    def test_datetime_passthrough(self):
        dt = datetime(2024, 6, 15, 12, 0)
        assert _parse_date(dt) is dt

    def test_date_object(self):
        d = date(2024, 6, 15)
        dt = _parse_date(d)
        assert dt == datetime(2024, 6, 15)

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_date("not-a-date")

    def test_whitespace_stripped(self):
        dt = _parse_date("  2024-01-15  ")
        assert dt == datetime(2024, 1, 15)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestFetchBarsValidation:
    def test_empty_ticker_raises(self):
        with pytest.raises(ValueError, match="Ticker must not be empty"):
            fetch_bars("", start="2024-01-01", end="2024-06-30")

    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError, match="Invalid interval"):
            fetch_bars("AAPL", start="2024-01-01", end="2024-06-30", interval="2d")

    def test_start_after_end_raises(self):
        with pytest.raises(ValueError, match="must be before"):
            fetch_bars("AAPL", start="2024-06-30", end="2024-01-01")

    def test_valid_intervals(self):
        assert "1d" in VALID_INTERVALS
        assert "1h" in VALID_INTERVALS
        assert "5m" in VALID_INTERVALS
        assert "1wk" in VALID_INTERVALS


# ---------------------------------------------------------------------------
# Chart client
# ---------------------------------------------------------------------------

class TestChartClient:
    @patch("backtesting.yahoo_fetch.requests.Session")
    def test_refresh_credentials(self, MockSession):
        session = MockSession.return_value

        # Mock fc.yahoo.com
        session.get.return_value = MagicMock(status_code=200, text="test_crumb")

        client = _ChartClient()
        client._session = session
        client._refresh_credentials()

        assert client._crumb == "test_crumb"

    @patch("backtesting.yahoo_fetch.requests.Session")
    def test_refresh_failure_raises(self, MockSession):
        session = MockSession.return_value

        # All crumb requests fail
        session.get.return_value = MagicMock(status_code=403, text="Forbidden")

        client = _ChartClient()
        client._session = session

        with pytest.raises(YahooFetchError, match="Could not obtain Yahoo crumb"):
            client._refresh_credentials()


# ---------------------------------------------------------------------------
# Response parsing (mocked)
# ---------------------------------------------------------------------------

class TestFetchBarsParsing:
    """Test that fetch_bars correctly parses Yahoo chart API responses."""

    @patch("backtesting.yahoo_fetch._get_client")
    def test_parses_valid_response(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": [{
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1704196200, 1704282600, 1704369000],
                    "indicators": {
                        "quote": [{
                            "open": [150.0, 151.0, 152.0],
                            "high": [152.0, 153.0, 154.0],
                            "low": [149.0, 150.0, 151.0],
                            "close": [151.0, 152.0, 153.0],
                            "volume": [1000000, 2000000, 3000000],
                        }]
                    }
                }],
                "error": None,
            }
        }

        bars = fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")
        assert len(bars) == 3
        assert bars[0].ticker == "AAPL"
        assert bars[0].open == 150.0
        assert bars[0].close == 151.0
        assert bars[0].volume == 1000000.0

    @patch("backtesting.yahoo_fetch._get_client")
    def test_skips_none_values(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": [{
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1704196200, 1704282600],
                    "indicators": {
                        "quote": [{
                            "open": [150.0, None],
                            "high": [152.0, None],
                            "low": [149.0, None],
                            "close": [151.0, None],
                            "volume": [1000000, None],
                        }]
                    }
                }],
                "error": None,
            }
        }

        bars = fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")
        assert len(bars) == 1  # Second bar skipped (None values)

    @patch("backtesting.yahoo_fetch._get_client")
    def test_empty_timestamps_raises(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": [{
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [],
                    "indicators": {"quote": [{}]}
                }],
                "error": None,
            }
        }

        with pytest.raises(YahooFetchError, match="No price data"):
            fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")

    @patch("backtesting.yahoo_fetch._get_client")
    def test_chart_error_raises(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": None,
                "error": {"code": "Not Found", "description": "No data found"},
            }
        }

        with pytest.raises(YahooFetchError, match="chart error"):
            fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")

    @patch("backtesting.yahoo_fetch._get_client")
    def test_no_results_raises(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": [],
                "error": None,
            }
        }

        with pytest.raises(YahooFetchError, match="No chart data"):
            fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")

    @patch("backtesting.yahoo_fetch._get_client")
    def test_bars_sorted_by_timestamp(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        # Timestamps intentionally out of order
        mock_client.get_chart.return_value = {
            "chart": {
                "result": [{
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1704369000, 1704196200],
                    "indicators": {
                        "quote": [{
                            "open": [152.0, 150.0],
                            "high": [154.0, 152.0],
                            "low": [151.0, 149.0],
                            "close": [153.0, 151.0],
                            "volume": [3000000, 1000000],
                        }]
                    }
                }],
                "error": None,
            }
        }

        bars = fetch_bars("AAPL", start="2024-01-01", end="2024-01-05")
        assert bars[0].timestamp < bars[1].timestamp

    @patch("backtesting.yahoo_fetch._get_client")
    def test_ticker_uppercased(self, mock_get_client):
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_client.get_chart.return_value = {
            "chart": {
                "result": [{
                    "meta": {"symbol": "AAPL"},
                    "timestamp": [1704196200],
                    "indicators": {
                        "quote": [{
                            "open": [150.0],
                            "high": [152.0],
                            "low": [149.0],
                            "close": [151.0],
                            "volume": [1000000],
                        }]
                    }
                }],
                "error": None,
            }
        }

        bars = fetch_bars("aapl", start="2024-01-01", end="2024-01-05")
        assert bars[0].ticker == "AAPL"
