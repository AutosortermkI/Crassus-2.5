from datetime import datetime, timezone
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
FUNCTION_DIR = ROOT_DIR / "function_app"
DASHBOARD_DIR = ROOT_DIR / "dashboard"
for path in (FUNCTION_DIR, DASHBOARD_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from webhook_activity import _build_snapshot as build_function_snapshot
from webhook_store import get_activity_snapshot
import webhook_store


def _parsed_stock_signal():
    return {
        "ticker": "AAPL",
        "side": "buy",
        "strategy": "bollinger_mean_reversion",
        "mode": "stock",
        "price": 189.5,
    }


def test_function_activity_reports_duplicate_signal_without_parse_error_label():
    event = {
        "id": "evt-1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "parsed": _parsed_stock_signal(),
        "parse_error": "Duplicate signal",
        "execution": {"ok": False, "status_code": 409, "message": "Duplicate signal"},
        "signature": "AAPL:buy:bollinger_mean_reversion:stock",
    }

    snapshot = build_function_snapshot([event], active_minutes=60, recent_limit=20)

    assert snapshot["active_webhooks"][0]["last_status"] == "duplicate_signal"


def test_function_activity_reports_downstream_http_failure_without_parse_error_label():
    event = {
        "id": "evt-2",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "parsed": _parsed_stock_signal(),
        "parse_error": "HTTPSConnectionPool(host='paper-api.alpaca.markets')",
        "execution": {"ok": False, "status_code": 500, "message": "Internal error"},
        "signature": "AAPL:buy:bollinger_mean_reversion:stock",
    }

    snapshot = build_function_snapshot([event], active_minutes=60, recent_limit=20)

    assert snapshot["active_webhooks"][0]["last_status"] == "http_500"


def test_function_activity_keeps_true_parse_error_label_for_unparsed_event():
    event = {
        "id": "evt-3",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "parsed": None,
        "parse_error": "Missing or invalid price",
        "execution": {"ok": False, "status_code": 400, "message": "Missing or invalid price"},
        "signature": None,
    }

    snapshot = build_function_snapshot([event], active_minutes=60, recent_limit=20)

    assert snapshot["active_webhooks"][0]["last_status"] == "parse_error"


def test_dashboard_local_activity_reports_parsed_downstream_failure(monkeypatch, tmp_path):
    store_path = tmp_path / "webhooks.json"
    monkeypatch.setattr(webhook_store, "STORE_PATH", store_path)
    webhook_store.record_event(
        {
            "id": "evt-4",
            "received_at": datetime.now(timezone.utc).isoformat(),
            "parsed": _parsed_stock_signal(),
            "parse_error": "Duplicate signal",
            "forward": {"ok": False, "status_code": 409, "message": "Duplicate signal"},
            "signature": "AAPL:buy:bollinger_mean_reversion:stock",
        }
    )

    snapshot = get_activity_snapshot(active_window_minutes=60, recent_limit=20)

    assert snapshot["active_webhooks"][0]["last_status"] == "duplicate_signal"
