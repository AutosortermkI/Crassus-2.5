"""
Tastytrade broker integration for Crassus 2.5.

This module keeps Tastytrade-specific payloads, OAuth, account queries, and
risk pre-flight helpers out of the Azure Function entry point. The production
route can select this broker without replacing the existing TradingView parser,
webhook auth, deduplication, and activity logging.
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

from risk import (
    InsufficientBuyingPowerError,
    MaxPositionsExceededError,
    get_max_open_positions,
)
from utils import get_logger, log_structured, round_options_price, round_stock_price

logger = get_logger(__name__)

TASTYTRADE_CERT_BASE_URL = "https://api.cert.tastyworks.com"
TASTYTRADE_PROD_BASE_URL = "https://api.tastyworks.com"


class TastytradeConfigurationError(Exception):
    """Raised when required Tastytrade credentials are missing."""


class TastytradeAPIError(Exception):
    """Raised when Tastytrade returns a non-successful API response."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass(frozen=True)
class TastytradeConfig:
    """Configuration required for Tastytrade OAuth and account-scoped requests."""

    account_number: str
    client_secret: str
    refresh_token: str
    is_test: bool = True
    base_url: Optional[str] = None
    oauth_scopes: str = "read trade"
    timeout_seconds: float = 15.0

    @classmethod
    def from_env(cls) -> "TastytradeConfig":
        """Load Tastytrade settings from environment variables."""
        is_test = _env_bool("TASTYTRADE_IS_TEST", False)
        base_url = os.environ.get("TASTYTRADE_BASE_URL", "").strip() or None
        timeout = _env_float("TASTYTRADE_TIMEOUT_SECONDS", 15.0)
        config = cls(
            account_number=os.environ.get("TASTYTRADE_ACCOUNT_NUMBER", "").strip(),
            client_secret=os.environ.get("TASTYTRADE_CLIENT_SECRET", "").strip(),
            refresh_token=os.environ.get("TASTYTRADE_REFRESH_TOKEN", "").strip(),
            is_test=is_test,
            base_url=base_url,
            oauth_scopes=os.environ.get("TASTYTRADE_OAUTH_SCOPES", "read trade").strip(),
            timeout_seconds=timeout,
        )
        config.validate()
        return config

    @property
    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return TASTYTRADE_CERT_BASE_URL if self.is_test else TASTYTRADE_PROD_BASE_URL

    def validate(self) -> None:
        missing = []
        if not self.account_number:
            missing.append("TASTYTRADE_ACCOUNT_NUMBER")
        if not self.client_secret:
            missing.append("TASTYTRADE_CLIENT_SECRET")
        if not self.refresh_token:
            missing.append("TASTYTRADE_REFRESH_TOKEN")
        if missing:
            raise TastytradeConfigurationError(
                "Missing required Tastytrade settings: " + ", ".join(missing)
            )


@dataclass(frozen=True)
class TastytradeBracketParams:
    """Parameters for a Tastytrade equity OTOCO bracket order."""

    symbol: str
    side: str
    qty: int
    entry_price: float
    take_profit_price: float
    stop_price: float
    stop_limit_price: float


@dataclass(frozen=True)
class TastytradeOptionBracketParams:
    """Parameters for a Tastytrade long equity-option OTOCO bracket order."""

    option_symbol: str
    underlying: str
    side: str
    qty: int
    entry_price: float
    take_profit_price: float
    stop_price: float
    stop_limit_price: float


class TastytradeClient:
    """Small HTTP client for the Tastytrade endpoints used by Crassus."""

    def __init__(self, config: TastytradeConfig, session: Optional[requests.Session] = None):
        config.validate()
        self.config = config
        self.session = session or requests.Session()
        self._access_token: Optional[str] = None
        self._token_expires_at = 0.0

    def place_complex_order(self, order_payload: dict, dry_run: bool = False) -> dict:
        path = f"/accounts/{self.config.account_number}/complex-orders"
        if dry_run:
            path += "/dry-run"
        return self._post(path, order_payload)

    def place_order(self, order_payload: dict, dry_run: bool = False) -> dict:
        path = f"/accounts/{self.config.account_number}/orders"
        if dry_run:
            path += "/dry-run"
        return self._post(path, order_payload)

    def get_balance(self) -> dict:
        data = self._get(f"/accounts/{self.config.account_number}/balances")
        return _first_item(data)

    def get_positions(self) -> list[dict]:
        data = self._get(f"/accounts/{self.config.account_number}/positions")
        return _items(data)

    def get_orders(self, limit: int = 20) -> list[dict]:
        data = self._get(
            f"/accounts/{self.config.account_number}/orders",
            params={"per-page": limit, "sort": "Desc"},
        )
        return _items(data)

    def _post(self, path: str, payload: dict) -> dict:
        response = self.session.post(
            self._url(path),
            json=payload,
            headers=self._headers(),
            timeout=self.config.timeout_seconds,
        )
        return _parse_tastytrade_response(response)

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        response = self.session.get(
            self._url(path),
            params=params,
            headers=self._headers(),
            timeout=self.config.timeout_seconds,
        )
        return _parse_tastytrade_response(response)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _access_token_value(self) -> str:
        now = time.time()
        if self._access_token and now < self._token_expires_at - 30:
            return self._access_token

        payload = {
            "grant_type": "refresh_token",
            "client_secret": self.config.client_secret,
            "refresh_token": self.config.refresh_token,
        }
        if self.config.oauth_scopes:
            payload["scope"] = self.config.oauth_scopes

        response = self.session.post(
            self._url("/oauth/token"),
            json=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=self.config.timeout_seconds,
        )
        token_data = _parse_tastytrade_response(response, unwrap_data=False)
        access_token = token_data.get("access_token")
        if not access_token:
            raise TastytradeAPIError("Tastytrade OAuth response did not include access_token")

        try:
            expires_in = float(token_data.get("expires_in", 900))
        except (TypeError, ValueError):
            expires_in = 900.0
        self._access_token = str(access_token)
        self._token_expires_at = now + expires_in
        return self._access_token

    def _url(self, path: str) -> str:
        return f"{self.config.resolved_base_url}{path}"


_cached_client: Optional[TastytradeClient] = None


def get_tastytrade_client() -> TastytradeClient:
    """Return a cached Tastytrade client for Azure Function warm invocations."""
    global _cached_client
    if _cached_client is None:
        _cached_client = TastytradeClient(TastytradeConfig.from_env())
    return _cached_client


def get_order_broker() -> str:
    """Return the configured execution broker, defaulting to Alpaca compatibility."""
    broker = (
        os.environ.get("ORDER_BROKER")
        or os.environ.get("BROKER")
        or "alpaca"
    ).strip().lower()
    if broker not in {"alpaca", "tastytrade"}:
        raise ValueError("ORDER_BROKER must be 'alpaca' or 'tastytrade'")
    return broker


def tastytrade_dry_run_enabled() -> bool:
    """Return whether Tastytrade orders should use preflight-only dry runs."""
    return _env_bool("TASTYTRADE_DRY_RUN", True)


def build_tastytrade_equity_otoco_order(params: TastytradeBracketParams) -> dict:
    """Build a Tastytrade OTOCO payload for a stock bracket order."""
    side = params.side.strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("Tastytrade stock side must be 'buy' or 'sell'")
    if params.qty <= 0:
        raise ValueError("Tastytrade stock quantity must be positive")
    if params.entry_price <= 0:
        raise ValueError("Tastytrade entry price must be positive")

    symbol = params.symbol.strip().upper()
    entry_action = "Buy to Open" if side == "buy" else "Sell to Open"
    exit_action = "Sell to Close" if side == "buy" else "Buy to Close"
    entry_price_effect = "Debit" if side == "buy" else "Credit"
    exit_price_effect = "Credit" if side == "buy" else "Debit"

    leg = {
        "instrument-type": "Equity",
        "symbol": symbol,
        "quantity": int(params.qty),
    }

    stop_order_type = _normalize_stop_order_type(
        os.environ.get("TASTYTRADE_STOP_ORDER_TYPE", "Stop Limit")
    )
    stop_order = {
        "order-type": stop_order_type,
        "time-in-force": os.environ.get("TASTYTRADE_EXIT_TIME_IN_FORCE", "GTC"),
        "stop-trigger": round_stock_price(params.stop_price),
        "legs": [{**leg, "action": exit_action}],
    }
    if stop_order_type == "Stop Limit":
        stop_order["price"] = round_stock_price(params.stop_limit_price)
        stop_order["price-effect"] = exit_price_effect

    return {
        "type": "OTOCO",
        "source": os.environ.get("TASTYTRADE_ORDER_SOURCE", "crassus-2.5"),
        "trigger-order": {
            "order-type": "Limit",
            "price": round_stock_price(params.entry_price),
            "price-effect": entry_price_effect,
            "time-in-force": os.environ.get("TASTYTRADE_ENTRY_TIME_IN_FORCE", "Day"),
            "legs": [{**leg, "action": entry_action}],
        },
        "orders": [
            {
                "order-type": "Limit",
                "price": round_stock_price(params.take_profit_price),
                "price-effect": exit_price_effect,
                "time-in-force": os.environ.get("TASTYTRADE_EXIT_TIME_IN_FORCE", "GTC"),
                "legs": [{**leg, "action": exit_action}],
            },
            stop_order,
        ],
    }


def build_tastytrade_option_otoco_order(params: TastytradeOptionBracketParams) -> dict:
    """Build a Tastytrade OTOCO payload for a long equity-option trade."""
    side = params.side.strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("Tastytrade option side must be 'buy' or 'sell'")
    if params.qty <= 0:
        raise ValueError("Tastytrade option quantity must be positive")
    if params.entry_price <= 0:
        raise ValueError("Tastytrade option entry price must be positive")

    option_symbol = params.option_symbol.strip().upper()
    if not option_symbol:
        raise ValueError("Tastytrade option symbol is required")

    leg = {
        "instrument-type": "Equity Option",
        "symbol": option_symbol,
        "quantity": int(params.qty),
    }

    stop_order_type = _normalize_stop_order_type(
        os.environ.get("TASTYTRADE_STOP_ORDER_TYPE", "Stop Limit")
    )
    stop_order = {
        "order-type": stop_order_type,
        "time-in-force": os.environ.get("TASTYTRADE_EXIT_TIME_IN_FORCE", "GTC"),
        "stop-trigger": round_options_price(params.stop_price),
        "legs": [{**leg, "action": "Sell to Close"}],
    }
    if stop_order_type == "Stop Limit":
        stop_order["price"] = round_options_price(params.stop_limit_price)
        stop_order["price-effect"] = "Credit"

    return {
        "type": "OTOCO",
        "source": os.environ.get("TASTYTRADE_ORDER_SOURCE", "crassus-2.5"),
        "trigger-order": {
            "order-type": "Limit",
            "price": round_options_price(params.entry_price),
            "price-effect": "Debit",
            "time-in-force": os.environ.get("TASTYTRADE_ENTRY_TIME_IN_FORCE", "Day"),
            "legs": [{**leg, "action": "Buy to Open"}],
        },
        "orders": [
            {
                "order-type": "Limit",
                "price": round_options_price(params.take_profit_price),
                "price-effect": "Credit",
                "time-in-force": os.environ.get("TASTYTRADE_EXIT_TIME_IN_FORCE", "GTC"),
                "legs": [{**leg, "action": "Sell to Close"}],
            },
            stop_order,
        ],
    }


def submit_tastytrade_stock_order(
    client: TastytradeClient,
    params: TastytradeBracketParams,
    correlation_id: str,
) -> str:
    """Submit or dry-run a Tastytrade equity OTOCO order and return an ID-like value."""
    dry_run = tastytrade_dry_run_enabled()
    payload = build_tastytrade_equity_otoco_order(params)
    log_structured(
        logger,
        logging.INFO,
        "Submitting Tastytrade stock OTOCO order",
        correlation_id,
        symbol=params.symbol.upper(),
        side=params.side,
        qty=params.qty,
        dry_run=dry_run,
    )
    response = client.place_complex_order(payload, dry_run=dry_run)
    order_id = _extract_order_id(response)
    log_structured(
        logger,
        logging.INFO,
        "Tastytrade stock OTOCO accepted",
        correlation_id,
        order_id=order_id,
        dry_run=dry_run,
    )
    return order_id


def submit_tastytrade_option_order(
    client: TastytradeClient,
    params: TastytradeOptionBracketParams,
    correlation_id: str,
) -> str:
    """Submit or dry-run a Tastytrade long-option OTOCO order and return an ID-like value."""
    dry_run = tastytrade_dry_run_enabled()
    payload = build_tastytrade_option_otoco_order(params)
    log_structured(
        logger,
        logging.INFO,
        "Submitting Tastytrade option OTOCO order",
        correlation_id,
        contract=params.option_symbol.upper(),
        underlying=params.underlying.upper(),
        side=params.side,
        qty=params.qty,
        dry_run=dry_run,
    )
    response = client.place_complex_order(payload, dry_run=dry_run)
    order_id = _extract_order_id(response)
    log_structured(
        logger,
        logging.INFO,
        "Tastytrade option OTOCO accepted",
        correlation_id,
        order_id=order_id,
        dry_run=dry_run,
    )
    return order_id


def resolve_tastytrade_option_symbol(data: dict) -> str:
    """Resolve a Tastytrade equity-option symbol from direct fields or OCC parts."""
    explicit = data.get("option_symbol") or data.get("contract_symbol")
    if explicit:
        return str(explicit).upper()

    underlying = str(data.get("underlying") or data.get("symbol") or "").upper().strip()
    expiration = str(data.get("expiration") or "").strip()
    option_type = str(data.get("option_type") or "").strip().upper()
    strike = data.get("strike")
    if not all([underlying, expiration, option_type, strike is not None]):
        raise ValueError(
            "Option symbol requires option_symbol, or underlying, expiration, "
            "option_type, and strike"
        )
    if len(underlying) > 6:
        raise ValueError("Tastytrade equity option root symbol cannot exceed 6 characters")
    if option_type in {"CALL", "C"}:
        option_type = "C"
    elif option_type in {"PUT", "P"}:
        option_type = "P"
    else:
        raise ValueError("option_type must be call/put or C/P")

    expiration_code = _expiration_to_yymmdd(expiration)
    strike_code = _strike_to_occ_thousandths(strike)
    return f"{underlying:<6}{expiration_code}{option_type}{strike_code}"


def get_tastytrade_account_equity(client) -> float:
    """Return net liquidating value from a Tastytrade balance response."""
    balance = client.get_balance()
    return _first_float(
        balance,
        (
            "net-liquidating-value",
            "margin-equity",
            "cash-balance",
        ),
        "Tastytrade account equity is unavailable",
    )


def validate_tastytrade_buying_power(
    client,
    required_dollars: float,
    correlation_id: str,
) -> float:
    """Check Tastytrade buying power before submitting an equity order."""
    balance = client.get_balance()
    buying_power = _first_float(
        balance,
        (
            "equity-buying-power",
            "available-trading-funds",
            "cash-available-to-withdraw",
            "cash-balance",
            "net-liquidating-value",
        ),
        "Tastytrade buying power is unavailable",
    )
    log_structured(
        logger,
        logging.INFO,
        "Tastytrade buying power check",
        correlation_id,
        buying_power=buying_power,
        required=required_dollars,
    )
    if buying_power < required_dollars:
        raise InsufficientBuyingPowerError(
            f"Insufficient Tastytrade buying power: ${buying_power:.2f} available, "
            f"${required_dollars:.2f} required"
        )
    return buying_power


def validate_tastytrade_position_limit(client, correlation_id: str) -> int:
    """Check the configured max-open-position limit using Tastytrade positions."""
    positions = [pos for pos in client.get_positions() if _position_quantity(pos) != 0]
    count = len(positions)
    max_positions = get_max_open_positions()
    log_structured(
        logger,
        logging.INFO,
        "Tastytrade position limit check",
        correlation_id,
        open_positions=count,
        max_positions=max_positions,
    )
    if count >= max_positions:
        raise MaxPositionsExceededError(f"Max open positions reached: {count}/{max_positions}")
    return count


def _parse_tastytrade_response(response, unwrap_data: bool = True) -> Any:
    try:
        body = response.json()
    except ValueError:
        body = {"raw_body": getattr(response, "text", "")}

    status_code = getattr(response, "status_code", None)
    if status_code is not None and status_code >= 400:
        raise TastytradeAPIError(_error_message(body), status_code=status_code, response_body=body)

    if unwrap_data and isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _error_message(body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        if error:
            return str(error)
        for key in ("error_description", "message"):
            if body.get(key):
                return str(body[key])
    return str(body)


def _items(data: Any) -> list[dict]:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data.get("data"), dict):
            return _items(data["data"])
        if isinstance(data.get("data"), list):
            return data["data"]
    if isinstance(data, list):
        return data
    return []


def _first_item(data: Any) -> dict:
    if isinstance(data, dict):
        if isinstance(data.get("items"), list):
            return data["items"][0] if data["items"] else {}
        if isinstance(data.get("data"), (dict, list)):
            return _first_item(data["data"])
        return data
    if isinstance(data, list):
        return data[0] if data else {}
    return {}


def _first_float(source: dict, keys: tuple[str, ...], missing_message: str) -> float:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    raise TastytradeAPIError(missing_message, response_body=source)


def _position_quantity(position: dict) -> float:
    value = position.get("quantity")
    if isinstance(value, dict):
        for key in ("value", "quantity", "amount"):
            if value.get(key) not in (None, ""):
                value = value[key]
                break
    if value in (None, ""):
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


def _extract_order_id(response: dict) -> str:
    if not isinstance(response, dict):
        return "tastytrade-response"
    order = response.get("order")
    if isinstance(order, dict) and order.get("id"):
        return str(order["id"])
    for key in ("id", "preflight-id", "complex-order-id"):
        if response.get(key):
            return str(response[key])
    return "tastytrade-dry-run" if tastytrade_dry_run_enabled() else "tastytrade-order"


def _normalize_stop_order_type(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("_", " ").replace("-", " ")
    if normalized in {"stop limit", "stoplimit"}:
        return "Stop Limit"
    if normalized == "stop":
        return "Stop"
    raise ValueError("TASTYTRADE_STOP_ORDER_TYPE must be 'Stop' or 'Stop Limit'")


def _expiration_to_yymmdd(expiration: str) -> str:
    digits = "".join(ch for ch in expiration if ch.isdigit())
    if len(digits) == 8:
        return digits[2:]
    if len(digits) == 6:
        return digits
    raise ValueError("expiration must be YYYY-MM-DD, YYYYMMDD, or YYMMDD")


def _strike_to_occ_thousandths(strike: Any) -> str:
    try:
        strike_value = float(strike)
    except (TypeError, ValueError) as exc:
        raise ValueError("strike must be numeric") from exc
    return f"{int(round(strike_value * 1000)):08d}"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default
