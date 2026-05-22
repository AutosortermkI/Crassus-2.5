"""
Crassus 2.5 -- Dashboard Alpaca client.

Provides portfolio, position, and order data for the dashboard UI.
"""

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from config_manager import read_env


def reload_env():
    """Re-read dashboard configuration without mutating process env."""
    return read_env()


def has_credentials() -> bool:
    """Return True if API key and secret are present (non-empty) in env."""
    env = reload_env()
    api_key = env.get("ALPACA_API_KEY", "").strip()
    secret_key = env.get("ALPACA_SECRET_KEY", "").strip()
    return bool(api_key) and bool(secret_key)


def verify_credentials() -> dict:
    """Test credentials against Alpaca and return status dict.

    Returns:
        {"ok": True, "account_id": "...", "paper": True/False}  on success
        {"ok": False, "error": "..."}  on failure
    """
    env = reload_env()
    api_key = env.get("ALPACA_API_KEY", "").strip()
    secret_key = env.get("ALPACA_SECRET_KEY", "").strip()
    if not api_key or not secret_key:
        return {"ok": False, "error": "API key and secret key are required."}
    paper = env.get("ALPACA_PAPER", "true").lower() == "true"
    try:
        client = TradingClient(api_key, secret_key, paper=paper)
        account = client.get_account()
        return {"ok": True, "account_id": str(account.id), "paper": paper}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _get_client() -> TradingClient:
    """Create an Alpaca TradingClient from .env credentials."""
    env = reload_env()
    api_key = env.get("ALPACA_API_KEY", "")
    secret_key = env.get("ALPACA_SECRET_KEY", "")
    paper = env.get("ALPACA_PAPER", "true").lower() == "true"
    return TradingClient(api_key, secret_key, paper=paper)


def get_account_summary() -> dict:
    """Return account overview data."""
    client = _get_client()
    env = reload_env()
    account = client.get_account()

    equity = float(account.equity)
    last_equity = float(account.last_equity)
    pl = equity - last_equity
    pl_pct = (pl / last_equity * 100) if last_equity else 0.0

    return {
        "equity": round(equity, 2),
        "buying_power": round(float(account.buying_power), 2),
        "cash": round(float(account.cash), 2),
        "portfolio_value": round(float(account.portfolio_value), 2),
        "profit_loss": round(pl, 2),
        "profit_loss_pct": round(pl_pct, 2),
        "paper": env.get("ALPACA_PAPER", "true").lower() == "true",
    }


def get_positions() -> list:
    """Return a list of open position dicts."""
    client = _get_client()
    positions = client.get_all_positions()

    result = []
    for p in positions:
        unrealized_pl = float(p.unrealized_pl)
        market_value = float(p.market_value)
        avg_entry = float(p.avg_entry_price)
        qty = float(p.qty)
        current_price = float(p.current_price)
        cost_basis = avg_entry * qty
        pl_pct = (unrealized_pl / cost_basis * 100) if cost_basis else 0.0

        result.append({
            "symbol": p.symbol,
            "qty": p.qty,
            "avg_entry": round(avg_entry, 2),
            "current_price": round(current_price, 2),
            "market_value": round(market_value, 2),
            "unrealized_pl": round(unrealized_pl, 2),
            "unrealized_pl_pct": round(pl_pct, 2),
        })

    return result


def get_recent_orders(limit: int = 20) -> list:
    """Return a list of recent order dicts."""
    client = _get_client()
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        limit=limit,
    )
    orders = client.get_orders(filter=request)

    result = []
    for o in orders:
        filled_price = None
        if o.filled_avg_price is not None:
            filled_price = round(float(o.filled_avg_price), 2)

        submitted_at = ""
        if o.submitted_at is not None:
            submitted_at = o.submitted_at.strftime("%Y-%m-%d %H:%M:%S")

        result.append({
            "symbol": o.symbol,
            "side": str(o.side.value) if o.side else "",
            "type": str(o.type.value) if o.type else "",
            "qty": str(o.qty),
            "status": str(o.status.value) if o.status else "",
            "filled_price": filled_price,
            "submitted_at": submitted_at,
        })

    return result
