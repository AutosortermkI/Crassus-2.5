"""
Dashboard helpers for Tastytrade account verification and snapshots.
"""

import sys
from pathlib import Path
from typing import Any

FUNCTION_APP_DIR = Path(__file__).resolve().parent.parent / "function_app"
if str(FUNCTION_APP_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTION_APP_DIR))

from config_manager import read_env
from tastytrade_orders import TastytradeClient, TastytradeConfig


def has_credentials() -> bool:
    env = read_env()
    return bool(
        (env.get("TASTYTRADE_ACCOUNT_NUMBER") or "").strip()
        and (env.get("TASTYTRADE_CLIENT_SECRET") or "").strip()
        and (env.get("TASTYTRADE_REFRESH_TOKEN") or "").strip()
    )


def verify_credentials() -> dict:
    """Verify Tastytrade credentials by fetching the account balance."""
    if not has_credentials():
        return {"ok": False, "error": "Tastytrade account number, client secret, and refresh token are required."}
    try:
        client = _get_client()
        client.get_balance()
        env = read_env()
        return {
            "ok": True,
            "account_id": env.get("TASTYTRADE_ACCOUNT_NUMBER", ""),
            "paper": _env_bool(env, "TASTYTRADE_IS_TEST", True),
            "dry_run": _env_bool(env, "TASTYTRADE_DRY_RUN", True),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify_credentials_with_values(
    *,
    account_number: str,
    client_secret: str,
    refresh_token: str,
    is_test: bool = True,
) -> dict:
    """Verify submitted Tastytrade credentials without requiring saved app settings."""
    try:
        config = TastytradeConfig(
            account_number=account_number.strip(),
            client_secret=client_secret.strip(),
            refresh_token=refresh_token.strip(),
            is_test=is_test,
        )
        client = TastytradeClient(config)
        client.get_balance()
        return {
            "ok": True,
            "account_id": account_number.strip(),
            "paper": is_test,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_account_summary() -> dict:
    balance = _get_client().get_balance()
    env = read_env()
    equity = _float_value(balance, "net-liquidating-value", "margin-equity", "cash-balance")
    buying_power = _float_value(
        balance,
        "equity-buying-power",
        "available-trading-funds",
        "cash-available-to-withdraw",
        "cash-balance",
    )
    cash = _float_value(balance, "cash-balance", "cash-available-to-withdraw")
    return {
        "equity": round(equity, 2),
        "buying_power": round(buying_power, 2),
        "cash": round(cash, 2),
        "portfolio_value": round(equity, 2),
        "profit_loss": round(_float_value(balance, "realized-day-gain", default=0.0), 2),
        "profit_loss_pct": 0.0,
        "paper": _env_bool(env, "TASTYTRADE_IS_TEST", True),
        "dry_run": _env_bool(env, "TASTYTRADE_DRY_RUN", True),
    }


def get_positions() -> list[dict]:
    result = []
    for position in _get_client().get_positions():
        qty = _quantity(position)
        avg_entry = _float_value(position, "average-open-price", "average-daily-market-close-price", default=0.0)
        current_price = _float_value(position, "mark-price", "mark", "close-price", default=0.0)
        market_value = abs(qty) * current_price
        unrealized_pl = _float_value(position, "realized-day-gain", "realized-today", default=0.0)
        result.append({
            "symbol": position.get("symbol", ""),
            "qty": str(position.get("quantity", "")),
            "avg_entry": round(avg_entry, 2),
            "current_price": round(current_price, 2),
            "market_value": round(market_value, 2),
            "unrealized_pl": round(unrealized_pl, 2),
            "unrealized_pl_pct": 0.0,
        })
    return result


def get_recent_orders(limit: int = 20) -> list[dict]:
    result = []
    for order in _get_client().get_orders(limit=limit):
        leg = _first_leg(order)
        result.append({
            "id": str(order.get("id", "")),
            "symbol": leg.get("symbol", order.get("underlying-symbol", "")),
            "side": leg.get("action", ""),
            "type": order.get("order-type", ""),
            "qty": str(leg.get("quantity", "")),
            "status": order.get("status", ""),
            "filled_price": _float_or_none(order.get("price")),
            "submitted_at": order.get("received-at") or order.get("live-at") or "",
        })
    return result


def _get_client() -> TastytradeClient:
    env = read_env()
    config = TastytradeConfig(
        account_number=(env.get("TASTYTRADE_ACCOUNT_NUMBER") or "").strip(),
        client_secret=(env.get("TASTYTRADE_CLIENT_SECRET") or "").strip(),
        refresh_token=(env.get("TASTYTRADE_REFRESH_TOKEN") or "").strip(),
        is_test=_env_bool(env, "TASTYTRADE_IS_TEST", True),
        base_url=(env.get("TASTYTRADE_BASE_URL") or "").strip() or None,
        oauth_scopes=(env.get("TASTYTRADE_OAUTH_SCOPES") or "read trade").strip(),
    )
    return TastytradeClient(config)


def _first_leg(order: dict) -> dict:
    legs = order.get("legs")
    if isinstance(legs, list) and legs:
        return legs[0]
    return {}


def _quantity(position: dict) -> float:
    value: Any = position.get("quantity", 0.0)
    if isinstance(value, dict):
        value = value.get("value") or value.get("quantity") or 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _float_value(source: dict, *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if source.get(key) in (None, ""):
            continue
        parsed = _float_or_none(source.get(key))
        if parsed is not None:
            return parsed
    return default


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _env_bool(env: dict, key: str, default: bool) -> bool:
    value = env.get(key)
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
