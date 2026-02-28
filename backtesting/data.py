"""
Crassus 2.5 -- Historical data loading for backtesting.

Reads OHLCV bar data and trade signals from CSV files.

Expected CSV formats
--------------------

**Bars CSV** (one row per bar)::

    timestamp,open,high,low,close,volume
    2024-01-02 09:30:00,150.00,151.25,149.80,150.50,1234567

  - ``timestamp`` is parsed flexibly (ISO-8601 or common US formats).
  - An optional ``ticker`` column is supported; when absent, the ticker
    must be supplied to :func:`load_bars_csv` via the *ticker* argument.

**Signals CSV** (one row per signal)::

    timestamp,ticker,side,price,strategy,mode
    2024-01-05 10:00:00,AAPL,buy,150.25,bollinger_mean_reversion,stock

  - ``mode`` defaults to ``"stock"`` if the column is absent.

Extension points:
  - Parquet / HDF5 readers
  - Direct download from Yahoo Finance / Alpaca historical API
  - Streaming / incremental data feeds
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union, Dict

from backtesting.models import Bar, Signal


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_TIMESTAMP_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y",
]


def _parse_timestamp(value: str) -> datetime:
    """Try multiple common timestamp formats and return the first match."""
    value = value.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse timestamp: '{value}'")


# ---------------------------------------------------------------------------
# Bar loading
# ---------------------------------------------------------------------------

def load_bars_csv(
    path: Union[str, Path],
    ticker: str = "",
) -> List[Bar]:
    """Load OHLCV bars from a CSV file.

    Args:
        path: Path to the CSV file.
        ticker: Default ticker symbol.  Overridden by a ``ticker`` column
            in the CSV if present.

    Returns:
        A list of :class:`Bar` objects sorted by timestamp (ascending).

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: On malformed rows.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Bars CSV not found: {path}")

    bars: List[Bar] = []

    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            try:
                ts = _parse_timestamp(row["timestamp"])
                bar = Bar(
                    timestamp=ts,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", "0")),
                    ticker=row.get("ticker", ticker),
                )
                bars.append(bar)
            except (KeyError, ValueError) as e:
                raise ValueError(f"Error on row {row_num}: {e}") from e

    bars.sort(key=lambda b: b.timestamp)
    return bars


def bars_from_dicts(records: List[Dict], ticker: str = "") -> List[Bar]:
    """Build :class:`Bar` objects from a list of dicts (useful for tests).

    Each dict should have keys: ``timestamp``, ``open``, ``high``, ``low``,
    ``close``, and optionally ``volume`` and ``ticker``.

    If ``timestamp`` is already a :class:`datetime`, it is used as-is;
    otherwise it is parsed as a string.
    """
    bars: List[Bar] = []
    for rec in records:
        ts = rec["timestamp"]
        if isinstance(ts, str):
            ts = _parse_timestamp(ts)
        bars.append(Bar(
            timestamp=ts,
            open=float(rec["open"]),
            high=float(rec["high"]),
            low=float(rec["low"]),
            close=float(rec["close"]),
            volume=float(rec.get("volume", 0)),
            ticker=rec.get("ticker", ticker),
        ))
    bars.sort(key=lambda b: b.timestamp)
    return bars


# ---------------------------------------------------------------------------
# Signal loading
# ---------------------------------------------------------------------------

def load_signals_csv(path: Union[str, Path]) -> List[Signal]:
    """Load trade signals from a CSV file.

    Args:
        path: Path to the CSV file.

    Returns:
        A list of :class:`Signal` objects sorted by timestamp (ascending).

    Raises:
        FileNotFoundError: If *path* does not exist.
        ValueError: On malformed rows.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Signals CSV not found: {path}")

    signals: List[Signal] = []

    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        for row_num, row in enumerate(reader, start=2):
            try:
                ts = _parse_timestamp(row["timestamp"])
                sig = Signal(
                    timestamp=ts,
                    ticker=row["ticker"].strip().upper(),
                    side=row["side"].strip().lower(),
                    price=float(row["price"]),
                    strategy=row["strategy"].strip().lower(),
                    mode=row.get("mode", "stock").strip().lower(),
                )
                signals.append(sig)
            except (KeyError, ValueError) as e:
                raise ValueError(f"Error on signal row {row_num}: {e}") from e

    signals.sort(key=lambda s: s.timestamp)
    return signals
