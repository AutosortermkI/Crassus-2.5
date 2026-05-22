from pathlib import Path
import sys
from types import SimpleNamespace


DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
if str(DASHBOARD_DIR) not in sys.path:
    sys.path.insert(0, str(DASHBOARD_DIR))

import tastytrade_client


def test_tastytrade_dashboard_has_credentials_from_env(monkeypatch):
    monkeypatch.setattr(
        tastytrade_client,
        "read_env",
        lambda: {
            "TASTYTRADE_ACCOUNT_NUMBER": "5WT12345",
            "TASTYTRADE_CLIENT_SECRET": "client-secret",
            "TASTYTRADE_REFRESH_TOKEN": "refresh-token",
        },
    )

    assert tastytrade_client.has_credentials() is True


def test_tastytrade_dashboard_account_summary_maps_balance_fields(monkeypatch):
    fake_client = SimpleNamespace(
        get_balance=lambda: {
            "net-liquidating-value": "10000.25",
            "equity-buying-power": "2500.50",
            "cash-balance": "1500.75",
        }
    )
    monkeypatch.setattr(tastytrade_client, "_get_client", lambda: fake_client)
    monkeypatch.setattr(
        tastytrade_client,
        "read_env",
        lambda: {"TASTYTRADE_IS_TEST": "true", "TASTYTRADE_DRY_RUN": "true"},
    )

    summary = tastytrade_client.get_account_summary()

    assert summary["equity"] == 10000.25
    assert summary["buying_power"] == 2500.50
    assert summary["cash"] == 1500.75
    assert summary["portfolio_value"] == 10000.25
    assert summary["paper"] is True
    assert summary["dry_run"] is True


def test_tastytrade_dashboard_positions_and_orders_are_ui_friendly(monkeypatch):
    fake_client = SimpleNamespace(
        get_positions=lambda: [
            {
                "symbol": "AAPL",
                "quantity": "2",
                "average-open-price": "100.12",
                "mark-price": "101.23",
                "mark": "101.23",
                "realized-day-gain": "4.56",
            }
        ],
        get_orders=lambda limit=20: [
            {
                "id": "order-1",
                "status": "Routed",
                "order-type": "Limit",
                "received-at": "2026-05-21T12:00:00Z",
                "legs": [{"symbol": "AAPL", "action": "Buy to Open", "quantity": "2"}],
                "price": "100.12",
            }
        ],
    )
    monkeypatch.setattr(tastytrade_client, "_get_client", lambda: fake_client)

    positions = tastytrade_client.get_positions()
    orders = tastytrade_client.get_recent_orders()

    assert positions == [
        {
            "symbol": "AAPL",
            "qty": "2",
            "avg_entry": 100.12,
            "current_price": 101.23,
            "market_value": 202.46,
            "unrealized_pl": 4.56,
            "unrealized_pl_pct": 0.0,
        }
    ]
    assert orders == [
        {
            "id": "order-1",
            "symbol": "AAPL",
            "side": "Buy to Open",
            "type": "Limit",
            "qty": "2",
            "status": "Routed",
            "filled_price": 100.12,
            "submitted_at": "2026-05-21T12:00:00Z",
        }
    ]
