"""
Crassus 2.5 -- Yahoo Finance historical bar data fetcher.

Downloads OHLCV price bars from Yahoo Finance's chart API for use
with the backtesting engine.  Reuses the same cookie/crumb
authentication pattern from ``yahoo_client.py``.

Supported intervals: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo.

Usage::

    from backtesting.yahoo_fetch import fetch_bars

    bars = fetch_bars("AAPL", start="2024-01-01", end="2024-06-30", interval="1d")
    result = Engine().run(bars, signals)
"""

from __future__ import annotations

import time
import logging
from datetime import datetime, date
from typing import List, Optional

import requests

from backtesting.models import Bar

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

VALID_INTERVALS = {
    "1m", "2m", "5m", "15m", "30m", "60m", "90m",
    "1h", "1d", "5d", "1wk", "1mo", "3mo",
}


class YahooFetchError(Exception):
    """Raised when historical data cannot be fetched."""


class _ChartClient:
    """Lightweight Yahoo Finance chart API client with cookie/crumb auth.

    Separate from the options ``YahooCrumbClient`` so the backtesting
    package has no import dependency on ``function_app/``.
    """

    def __init__(self, retry_count: int = 3, backoff_base: int = 2):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT})
        self._crumb: Optional[str] = None
        self._retry_count = retry_count
        self._backoff_base = backoff_base

    def _refresh_credentials(self) -> None:
        """Fetch a fresh cookie + crumb pair."""
        self._session.cookies.clear()
        self._session.get("https://fc.yahoo.com", allow_redirects=True, timeout=15)

        for url in [
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            "https://query2.finance.yahoo.com/v1/test/getcrumb",
        ]:
            r = self._session.get(url, allow_redirects=True, timeout=15)
            txt = (r.text or "").strip()
            if r.status_code == 200 and txt and "Too Many" not in txt and "Invalid" not in txt:
                self._crumb = txt
                return

        raise YahooFetchError("Could not obtain Yahoo crumb for chart API.")

    def get_chart(self, ticker: str, params: dict) -> dict:
        """Fetch chart data with retries, crumb refresh, and backoff."""
        if not self._crumb:
            self._refresh_credentials()

        params = dict(params)
        params["crumb"] = self._crumb

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"

        last_response = None
        for attempt in range(self._retry_count):
            r = self._session.get(url, params=params, timeout=20)
            last_response = r

            if r.status_code == 401:
                self._refresh_credentials()
                params["crumb"] = self._crumb
                continue

            if r.status_code in (429, 500, 502, 503, 504):
                wait = self._backoff_base ** attempt
                logger.warning(
                    "Yahoo chart API %d, backing off %ds (attempt %d)",
                    r.status_code, wait, attempt,
                )
                time.sleep(wait)
                continue

            r.raise_for_status()
            return r.json()

        status = getattr(last_response, "status_code", None)
        raise YahooFetchError(
            f"Yahoo chart request failed after {self._retry_count} retries "
            f"(last status={status})"
        )


# Module-level client, reused across calls
_client: Optional[_ChartClient] = None


def _get_client() -> _ChartClient:
    global _client
    if _client is None:
        _client = _ChartClient()
    return _client


def _parse_date(value: str | date | datetime) -> datetime:
    """Convert a date string or object to a datetime."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{value}'")


def fetch_bars(
    ticker: str,
    start: str | date | datetime,
    end: str | date | datetime | None = None,
    interval: str = "1d",
) -> List[Bar]:
    """Fetch historical OHLCV bars from Yahoo Finance.

    Args:
        ticker: Stock symbol (e.g. ``"AAPL"``).
        start: Start date (inclusive).  Accepts ``"YYYY-MM-DD"`` strings,
            :class:`date`, or :class:`datetime`.
        end: End date (inclusive).  Defaults to today.
        interval: Bar interval.  One of: ``1m``, ``2m``, ``5m``, ``15m``,
            ``30m``, ``60m``, ``90m``, ``1h``, ``1d``, ``5d``, ``1wk``,
            ``1mo``, ``3mo``.

    Returns:
        List of :class:`Bar` objects sorted by timestamp.

    Raises:
        ValueError: If *interval* is not valid or dates are malformed.
        YahooFetchError: If the data cannot be fetched from Yahoo.
    """
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("Ticker must not be empty.")

    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval '{interval}'. Must be one of: {sorted(VALID_INTERVALS)}"
        )

    start_dt = _parse_date(start)
    end_dt = _parse_date(end) if end else datetime.now()

    if start_dt >= end_dt:
        raise ValueError(f"Start date ({start_dt.date()}) must be before end date ({end_dt.date()}).")

    period1 = int(start_dt.timestamp())
    period2 = int(end_dt.timestamp())

    client = _get_client()
    data = client.get_chart(ticker, {
        "period1": period1,
        "period2": period2,
        "interval": interval,
        "includePrePost": "false",
        "events": "",
    })

    # Parse response
    chart = data.get("chart", {})
    error = chart.get("error")
    if error:
        raise YahooFetchError(f"Yahoo chart error: {error}")

    results = chart.get("result")
    if not results:
        raise YahooFetchError(f"No chart data returned for {ticker}.")

    result = results[0]
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators", {})
    quotes = (indicators.get("quote") or [{}])[0]

    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    volumes = quotes.get("volume") or []

    if not timestamps:
        raise YahooFetchError(f"No price data returned for {ticker} in the given range.")

    bars: List[Bar] = []
    for i, ts in enumerate(timestamps):
        # Skip bars with None values (market holidays / gaps)
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        lo = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = volumes[i] if i < len(volumes) else None

        if any(x is None for x in (o, h, lo, c)):
            continue

        bars.append(Bar(
            timestamp=datetime.utcfromtimestamp(ts),
            open=float(o),
            high=float(h),
            low=float(lo),
            close=float(c),
            volume=float(v or 0),
            ticker=ticker,
        ))

    bars.sort(key=lambda b: b.timestamp)

    logger.info(
        "Fetched %d bars for %s (%s to %s, interval=%s)",
        len(bars), ticker, start_dt.date(), end_dt.date(), interval,
    )

    return bars
