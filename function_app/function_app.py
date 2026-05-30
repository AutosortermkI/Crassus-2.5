"""
Crassus 2.5 -- Azure Function entry point.

HTTP trigger that receives TradingView webhook alerts and routes them
to stock or options bracket-order logic.

Flow:
  1. Authenticate via ``X-Webhook-Token`` header or ``?token=`` query param
  2. Parse the TradingView ``content`` string
  3. Look up strategy configuration
  4. Route to stock or options order path
  5. Return structured JSON HTTP response

Endpoint: ``POST /api/trade``
"""

import hmac
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import azure.functions as func
from alpaca.trading.client import TradingClient
from alpaca.common.exceptions import APIError

from parser import parse_webhook_payload, ParseError
from strategy import (
    get_strategy,
    compute_stock_bracket_prices,
    compute_options_exit_prices,
    UnknownStrategyError,
)
from stock_orders import StockBracketParams, submit_stock_order
from options_screener import (
    screen_option_contracts,
    NoContractFoundError,
)
from options_orders import OptionsOrderParams, submit_options_entry_order
from exit_monitor import ExitTarget, register_exit_target, check_options_exits
from risk import (
    compute_options_qty,
    compute_stock_qty,
    get_max_dollar_risk,
    get_account_equity,
    validate_buying_power,
    validate_position_limit,
    InsufficientBuyingPowerError,
    MaxPositionsExceededError,
)
from dedup import is_duplicate_signal
from safety import (
    check_trading_safety,
    check_operator_halt,
    check_live_trading_gate,
    LiveTradingNotConfirmedError,
    TradingHaltedError,
    DailyLossLimitExceededError,
)
from order_monitor import submit_with_retry, check_stock_orders, cancel_stale_orders
from tastytrade_orders import (
    TastytradeAPIError,
    TastytradeBracketParams,
    TastytradeConfigurationError,
    get_tastytrade_account_equity,
    get_tastytrade_client,
    submit_tastytrade_stock_order,
    tastytrade_dry_run_enabled,
    validate_tastytrade_buying_power,
    validate_tastytrade_position_limit,
)
from utils import (
    generate_correlation_id,
    log_structured,
    get_logger,
    round_stock_price,
    round_options_price,
)
from webhook_activity import (
    build_signature,
    get_webhook_activity_snapshot,
    record_webhook_event,
)

logger = get_logger(__name__)

# ------------------------------------------------------------------
# App settings (Environment Variables)
# ------------------------------------------------------------------
WEBHOOK_AUTH_TOKEN = os.environ.get("WEBHOOK_AUTH_TOKEN", "")
ALLOWED_BROKERS = {"alpaca", "tastytrade"}

_alpaca_trading_client = None
_alpaca_client_signature = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ======================================================================
# HTTP Trigger
# ======================================================================

@app.route(route="trade-stock", methods=["POST"])
def trade_stock(req: func.HttpRequest) -> func.HttpResponse:
    """Stock/share webhook handler."""
    return _handle_trade_request(req, expected_mode="stock")


@app.route(route="trade-options", methods=["POST"])
def trade_options(req: func.HttpRequest) -> func.HttpResponse:
    """Options webhook handler."""
    return _handle_trade_request(req, expected_mode="options")


@app.route(route="trade", methods=["POST"])
def trade(req: func.HttpRequest) -> func.HttpResponse:
    """Legacy webhook handler that routes by parsed signal mode."""
    return _handle_trade_request(req, expected_mode=None, legacy=True)


def get_stock_broker() -> str:
    """Return the per-request stock broker, with ORDER_BROKER as a legacy fallback."""
    return _get_broker_setting("STOCK_BROKER", "alpaca")


def get_options_broker() -> str:
    """Return the per-request options broker, with ORDER_BROKER as a legacy fallback."""
    return _get_broker_setting("OPTIONS_BROKER", "tastytrade")


def get_alpaca_trading_client() -> TradingClient:
    """Create the Alpaca trading client only when an Alpaca path needs it."""
    global _alpaca_trading_client, _alpaca_client_signature

    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
    if not api_key or not secret_key:
        raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set for Alpaca routing")

    signature = (api_key, secret_key, paper)
    if _alpaca_trading_client is None or _alpaca_client_signature != signature:
        _alpaca_trading_client = TradingClient(api_key, secret_key, paper=paper)
        _alpaca_client_signature = signature
    return _alpaca_trading_client


def _get_broker_setting(primary_key: str, default: str) -> str:
    broker = os.environ.get(primary_key, "").strip().lower()
    if not broker:
        broker = os.environ.get("ORDER_BROKER", "").strip().lower()
    if not broker:
        broker = default
    if broker not in ALLOWED_BROKERS:
        raise ValueError(f"{primary_key} must be 'alpaca' or 'tastytrade'")
    return broker


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "")
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _authenticate_request(req: func.HttpRequest, correlation_id: str, expected_mode: Optional[str]) -> Optional[func.HttpResponse]:
    token = req.headers.get("X-Webhook-Token", "") or req.params.get("token", "")
    token_key = {
        "stock": "STOCK_WEBHOOK_AUTH_TOKEN",
        "options": "OPTIONS_WEBHOOK_AUTH_TOKEN",
    }.get(expected_mode, "WEBHOOK_AUTH_TOKEN")
    expected_token = os.environ.get(token_key, "").strip() or os.environ.get("WEBHOOK_AUTH_TOKEN", "").strip()
    if not token or not expected_token or not hmac.compare_digest(token, expected_token):
        log_structured(logger, logging.WARNING, "Unauthorized request", correlation_id)
        return _json_response({"error": "Unauthorized", "correlation_id": correlation_id}, 401)
    return None


def _parse_signal(req: func.HttpRequest, correlation_id: str) -> tuple[Optional[dict], Optional[object], Optional[func.HttpResponse]]:
    try:
        data = req.get_json()
    except ValueError:
        log_structured(logger, logging.WARNING, "Invalid JSON body", correlation_id)
        return None, None, _json_response({"error": "Invalid JSON", "correlation_id": correlation_id}, 400)

    try:
        signal = parse_webhook_payload(data)
    except ParseError as e:
        log_structured(logger, logging.WARNING, f"Parse error: {e}", correlation_id)
        return data, None, _json_response({"error": str(e), "correlation_id": correlation_id}, 400)
    return data, signal, None


def _mode_error(signal, expected_mode: Optional[str], correlation_id: str) -> Optional[func.HttpResponse]:
    if expected_mode == "stock" and signal.mode == "options":
        return _json_response(
            {"error": "Options signals must use /api/trade-options", "correlation_id": correlation_id},
            400,
        )
    if expected_mode == "options" and signal.mode != "options":
        return _json_response(
            {"error": "Stock signals must use /api/trade-stock; options route requires Mode: options", "correlation_id": correlation_id},
            400,
        )
    return None


def _run_common_preflight(signal, correlation_id: str) -> None:
    _check_request_safety(correlation_id, signal.mode)


def _get_strategy_config(signal, correlation_id: str):
    try:
        return get_strategy(signal.strategy), None
    except UnknownStrategyError as e:
        log_structured(logger, logging.WARNING, f"Unknown strategy: {e}", correlation_id)
        return None, _json_response({"error": str(e), "correlation_id": correlation_id}, 400)


def _handle_trade_request(
    req: func.HttpRequest,
    expected_mode: Optional[str],
    legacy: bool = False,
) -> func.HttpResponse:
    route_name = "trade" if legacy else f"trade-{expected_mode}"
    correlation_id = generate_correlation_id()
    log_structured(logger, logging.INFO, f"Received {route_name} request", correlation_id)

    auth_response = _authenticate_request(req, correlation_id, expected_mode)
    if auth_response is not None:
        return auth_response

    data, signal, parse_response = _parse_signal(req, correlation_id)
    if parse_response is not None:
        _record_activity(data or {}, correlation_id, parse_response, signal=None, parse_error=json.loads(parse_response.get_body()).get("error"))
        return parse_response

    mode_response = _mode_error(signal, expected_mode, correlation_id)
    if mode_response is not None:
        _record_activity(data, correlation_id, mode_response, signal=signal, parse_error=json.loads(mode_response.get_body()).get("error"))
        return mode_response

    log_structured(
        logger, logging.INFO, "Signal parsed", correlation_id,
        ticker=signal.ticker, side=signal.side,
        strategy=signal.strategy, price=signal.price, mode=signal.mode,
    )

    try:
        _run_common_preflight(signal, correlation_id)
    except LiveTradingNotConfirmedError as e:
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 403)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except TradingHaltedError as e:
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 503)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except DailyLossLimitExceededError as e:
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 403)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except (TastytradeConfigurationError, ValueError) as e:
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 503)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except TastytradeAPIError as e:
        response = _json_response(
            {"error": f"Tastytrade API error: {str(e)}", "broker": "tastytrade", "correlation_id": correlation_id},
            502,
        )
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response

    if is_duplicate_signal(
        ticker=signal.ticker,
        side=signal.side,
        strategy=signal.strategy,
        mode=signal.mode,
        price=signal.price,
        correlation_id=correlation_id,
    ):
        response = _json_response({
            "error": "Duplicate signal",
            "detail": "This signal was already processed recently",
            "correlation_id": correlation_id,
        }, 409)
        _record_activity(data, correlation_id, response, signal=signal, parse_error="Duplicate signal")
        return response

    strategy_config, strategy_response = _get_strategy_config(signal, correlation_id)
    if strategy_response is not None:
        _record_activity(data, correlation_id, strategy_response, signal=signal, parse_error=json.loads(strategy_response.get_body()).get("error"))
        return strategy_response

    try:
        if signal.mode == "stock":
            body, status_code = _route_stock_order(signal, strategy_config, correlation_id)
        elif signal.mode == "options":
            body, status_code = _route_options_order(signal, strategy_config, correlation_id)
        else:
            body, status_code = {"error": f"Unsupported mode: {signal.mode}"}, 400

        if signal.mode == "stock":
            body.setdefault("route", "trade-stock")
            body.setdefault("broker", get_stock_broker())
            body.setdefault("symbol", signal.ticker)
        elif signal.mode == "options":
            body.setdefault("route", "trade-options")
            body.setdefault("broker", get_options_broker())
            body.setdefault("underlying", signal.ticker)
        body.setdefault("side", signal.side)
        body.setdefault("strategy", signal.strategy)
        body.setdefault("correlation_id", correlation_id)
        if legacy:
            body["legacy_warning"] = "The /api/trade route is deprecated; move to /api/trade-stock or /api/trade-options."
        response = _json_response(body, status_code)
        _record_activity(data, correlation_id, response, signal=signal)
        return response

    except APIError as e:
        response = _json_response({"error": f"Alpaca API error: {str(e)}", "correlation_id": correlation_id}, 502)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except TastytradeConfigurationError as e:
        response = _json_response({"error": str(e), "broker": "tastytrade", "correlation_id": correlation_id}, 503)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except TastytradeAPIError as e:
        response = _json_response(
            {"error": f"Tastytrade API error: {str(e)}", "broker": "tastytrade", "correlation_id": correlation_id},
            502,
        )
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response
    except Exception as e:
        response = _json_response({"error": f"Internal error: {str(e)}", "correlation_id": correlation_id}, 500)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response


@app.route(route="webhook-activity", methods=["GET"])
def webhook_activity(req: func.HttpRequest) -> func.HttpResponse:
    """Return recent webhook snapshots and the active-webhook rollup."""
    correlation_id = generate_correlation_id()
    token = req.headers.get("X-Webhook-Token", "") or req.params.get("token", "")
    if not token or not hmac.compare_digest(token, WEBHOOK_AUTH_TOKEN):
        return _json_response({"error": "Unauthorized", "correlation_id": correlation_id}, 401)

    try:
        active_minutes = int(req.params.get("active_minutes", "60"))
    except ValueError:
        active_minutes = 60
    try:
        recent_limit = int(req.params.get("limit", "20"))
    except ValueError:
        recent_limit = 20

    snapshot = get_webhook_activity_snapshot(
        active_minutes=max(1, active_minutes),
        recent_limit=max(1, recent_limit),
    )
    snapshot["correlation_id"] = correlation_id
    return _json_response(snapshot, 200)


# ======================================================================
# Timer Trigger: Options exit monitoring (runs every minute)
# ======================================================================

@app.timer_trigger(schedule="0 */1 * * * *", arg_name="timer",
                   run_on_startup=False)
def check_options_exits_timer(timer: func.TimerRequest) -> None:
    """Poll open options positions and submit exit orders at TP/SL."""
    correlation_id = generate_correlation_id()
    log_structured(logger, logging.INFO, "Options exit monitor tick", correlation_id)
    try:
        actions = check_options_exits(get_alpaca_trading_client())
        if actions:
            log_structured(
                logger, logging.INFO,
                f"Exit monitor actions: {len(actions)}",
                correlation_id,
                actions=json.dumps(actions),
            )
    except Exception as e:
        log_structured(logger, logging.ERROR, f"Exit monitor error: {e}", correlation_id)


# ======================================================================
# Timer Trigger: Stock order status monitoring (runs every 5 minutes)
# ======================================================================

@app.timer_trigger(schedule="0 */5 * * * *", arg_name="timer",
                   run_on_startup=False)
def check_stock_orders_timer(timer: func.TimerRequest) -> None:
    """Poll open stock orders for status changes and cancel stale orders."""
    correlation_id = generate_correlation_id()
    log_structured(logger, logging.INFO, "Stock order monitor tick", correlation_id)
    try:
        client = get_alpaca_trading_client()
        events = check_stock_orders(client, correlation_id)
        if events:
            log_structured(
                logger, logging.INFO,
                f"Order status events: {len(events)}",
                correlation_id,
            )

        # Cancel unfilled orders older than configured minutes (default 120)
        stale_minutes = int(os.environ.get("STALE_ORDER_MINUTES", "120"))
        cancelled = cancel_stale_orders(client, stale_minutes, correlation_id)
        if cancelled:
            log_structured(
                logger, logging.INFO,
                f"Cancelled {len(cancelled)} stale orders",
                correlation_id,
            )
    except Exception as e:
        log_structured(logger, logging.ERROR, f"Order monitor error: {e}", correlation_id)


# ======================================================================
# Internal route handlers
# ======================================================================

def _response_body_status(response: func.HttpResponse) -> tuple[dict, int]:
    try:
        return json.loads(response.get_body()), response.status_code
    except Exception:
        return {"error": response.get_body().decode("utf-8", errors="replace")}, response.status_code


def _route_stock_order(signal, strategy_config, correlation_id: str) -> tuple[dict, int]:
    broker = get_stock_broker()
    if broker == "tastytrade":
        body, status_code = _response_body_status(
            _handle_tastytrade_stock_order(signal, strategy_config, correlation_id)
        )
    else:
        body, status_code = _response_body_status(
            _handle_alpaca_stock_order(signal, strategy_config, correlation_id)
        )
    body.setdefault("broker", broker)
    body.setdefault("route", "trade-stock")
    return body, status_code


def _route_options_order(signal, strategy_config, correlation_id: str) -> tuple[dict, int]:
    broker = get_options_broker()
    if broker == "tastytrade":
        if not _env_bool("ENABLE_TASTYTRADE_OPTIONS", False):
            return {
                "error": "Tastytrade options routing is configured but disabled until contract-symbol routing is verified.",
                "broker": "tastytrade",
                "route": "trade-options",
                "enabled": False,
                "correlation_id": correlation_id,
            }, 501
        if not _env_bool("OPTIONS_ALLOW_FALLBACK_TO_ALPACA", False):
            return {
                "error": "TastyTrade options routing is not implemented until contract-symbol routing is verified.",
                "broker": "tastytrade",
                "route": "trade-options",
                "enabled": True,
                "correlation_id": correlation_id,
            }, 501

    body, status_code = _response_body_status(_handle_options_order(signal, strategy_config, correlation_id))
    body.setdefault("broker", "alpaca")
    body.setdefault("route", "trade-options")
    if broker == "tastytrade":
        body["fallback_broker"] = "alpaca"
    return body, status_code


def _handle_stock_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Compatibility wrapper for older callers."""
    body, status_code = _route_stock_order(signal, strategy_config, correlation_id)
    return _json_response(body, status_code)


def _handle_alpaca_stock_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Process an Alpaca stock bracket order with full production safety checks."""
    client = get_alpaca_trading_client()

    # -- Pre-flight: position limit check --
    try:
        validate_position_limit(client, correlation_id)
    except MaxPositionsExceededError as e:
        log_structured(logger, logging.WARNING, str(e), correlation_id)
        return _json_response({"error": str(e), "correlation_id": correlation_id}, 429)

    # Compute bracket prices from strategy config
    tp, stop, stop_limit = compute_stock_bracket_prices(
        entry_price=signal.price,
        side=signal.side,
        config=strategy_config,
    )

    # Equity-based sizing: fetch account equity for risk_pct mode
    try:
        equity = get_account_equity(client)
    except Exception as e:
        log_structured(logger, logging.ERROR, f"Failed to fetch equity: {e}", correlation_id)
        equity = None

    qty = compute_stock_qty(
        entry_price=signal.price,
        stop_loss_pct=strategy_config.stock_sl_pct,
        account_equity=equity,
    )

    # -- Pre-flight: buying power check --
    required_dollars = qty * signal.price
    try:
        validate_buying_power(client, required_dollars, correlation_id)
    except InsufficientBuyingPowerError as e:
        return _json_response({"error": str(e), "correlation_id": correlation_id}, 422)

    params = StockBracketParams(
        symbol=signal.ticker,
        side=signal.side,
        qty=qty,
        entry_price=signal.price,
        take_profit_price=tp,
        stop_price=stop,
        stop_limit_price=stop_limit,
    )

    # Submit with retry on transient failures
    order_id = submit_with_retry(
        lambda: submit_stock_order(client, params, correlation_id),
        correlation_id,
    )

    log_structured(
        logger, logging.INFO, "Stock order completed", correlation_id,
        order_id=order_id, symbol=signal.ticker,
        side=signal.side, strategy=signal.strategy,
    )

    return _json_response({
        "status": "ok",
        "mode": "stock",
        "order_id": order_id,
        "symbol": signal.ticker,
        "side": signal.side,
        "qty": qty,
        "entry_price": round_stock_price(signal.price),
        "take_profit": round_stock_price(tp),
        "stop_loss": round_stock_price(stop),
        "stop_limit": round_stock_price(stop_limit),
        "strategy": signal.strategy,
        "correlation_id": correlation_id,
    }, 200)


def _handle_tastytrade_stock_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Process a Tastytrade stock OTOCO order with broker-specific checks."""
    client = get_tastytrade_client()

    try:
        validate_tastytrade_position_limit(client, correlation_id)
    except MaxPositionsExceededError as e:
        log_structured(logger, logging.WARNING, str(e), correlation_id)
        return _json_response({"error": str(e), "broker": "tastytrade", "correlation_id": correlation_id}, 429)

    tp, stop, stop_limit = compute_stock_bracket_prices(
        entry_price=signal.price,
        side=signal.side,
        config=strategy_config,
    )

    try:
        equity = get_tastytrade_account_equity(client)
    except Exception as e:
        log_structured(logger, logging.ERROR, f"Failed to fetch Tastytrade equity: {e}", correlation_id)
        equity = None

    qty = compute_stock_qty(
        entry_price=signal.price,
        stop_loss_pct=strategy_config.stock_sl_pct,
        account_equity=equity,
    )

    required_dollars = qty * signal.price
    try:
        validate_tastytrade_buying_power(client, required_dollars, correlation_id)
    except InsufficientBuyingPowerError as e:
        return _json_response({"error": str(e), "broker": "tastytrade", "correlation_id": correlation_id}, 422)

    params = TastytradeBracketParams(
        symbol=signal.ticker,
        side=signal.side,
        qty=qty,
        entry_price=signal.price,
        take_profit_price=tp,
        stop_price=stop,
        stop_limit_price=stop_limit,
    )
    order_id = submit_tastytrade_stock_order(client, params, correlation_id)

    log_structured(
        logger, logging.INFO, "Tastytrade stock order completed", correlation_id,
        order_id=order_id, symbol=signal.ticker,
        side=signal.side, strategy=signal.strategy,
        dry_run=tastytrade_dry_run_enabled(),
    )

    return _json_response({
        "status": "ok",
        "broker": "tastytrade",
        "dry_run": tastytrade_dry_run_enabled(),
        "mode": "stock",
        "order_id": order_id,
        "symbol": signal.ticker,
        "side": signal.side,
        "qty": qty,
        "entry_price": round_stock_price(signal.price),
        "take_profit": round_stock_price(tp),
        "stop_loss": round_stock_price(stop),
        "stop_limit": round_stock_price(stop_limit),
        "strategy": signal.strategy,
        "correlation_id": correlation_id,
    }, 200)


def _handle_options_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Process an options order with contract screening and risk sizing."""
    client = get_alpaca_trading_client()

    # -- Pre-flight: position limit check --
    try:
        validate_position_limit(client, correlation_id)
    except MaxPositionsExceededError as e:
        log_structured(logger, logging.WARNING, str(e), correlation_id)
        return _json_response({"error": str(e), "correlation_id": correlation_id}, 429)

    # 1. Screen for the best options contract
    try:
        contract = screen_option_contracts(
            client=client,
            underlying=signal.ticker,
            side=signal.side,
            entry_price=signal.price,
            correlation_id=correlation_id,
        )
    except NoContractFoundError as e:
        log_structured(
            logger, logging.WARNING, f"No contract found: {e}", correlation_id,
            symbol=signal.ticker,
        )
        return _json_response(
            {"error": f"No suitable options contract: {str(e)}", "correlation_id": correlation_id},
            400,
        )

    # 2. Compute exit targets (% of premium)
    tp_price, sl_price = compute_options_exit_prices(
        premium=contract.premium,
        side=signal.side,
        config=strategy_config,
    )

    # 3. Risk sizing
    max_risk = get_max_dollar_risk()
    qty = compute_options_qty(
        max_dollar_risk=max_risk,
        stop_loss_pct=strategy_config.options_sl_pct,
        premium_price=contract.premium,
    )

    # -- Pre-flight: buying power check --
    required_dollars = qty * contract.premium * 100.0
    try:
        validate_buying_power(client, required_dollars, correlation_id)
    except InsufficientBuyingPowerError as e:
        return _json_response({"error": str(e), "correlation_id": correlation_id}, 422)

    # 4. Submit entry order
    params = OptionsOrderParams(
        contract_symbol=contract.symbol,
        underlying=signal.ticker,
        side=signal.side,
        qty=qty,
        limit_price=contract.premium,
        take_profit_price=tp_price,
        stop_loss_price=sl_price,
    )

    order_id = submit_with_retry(
        lambda: submit_options_entry_order(client, params, correlation_id),
        correlation_id,
    )

    # Register TP/SL targets so the exit monitor can track this position
    register_exit_target(ExitTarget(
        contract_symbol=contract.symbol,
        underlying=signal.ticker,
        qty=qty,
        entry_price=contract.premium,
        take_profit_price=tp_price,
        stop_loss_price=sl_price,
        correlation_id=correlation_id,
    ))

    log_structured(
        logger, logging.INFO, "Options order completed", correlation_id,
        order_id=order_id, contract=contract.symbol,
        underlying=signal.ticker, side=signal.side, strategy=signal.strategy,
    )

    return _json_response({
        "status": "ok",
        "mode": "options",
        "order_id": order_id,
        "contract": contract.symbol,
        "underlying": signal.ticker,
        "side": signal.side,
        "qty": qty,
        "premium": round_options_price(contract.premium),
        "take_profit": round_options_price(tp_price),
        "stop_loss": round_options_price(sl_price),
        "strike": contract.strike,
        "expiration": contract.expiration.isoformat(),
        "dte": contract.dte,
        "contract_type": contract.contract_type,
        "strategy": signal.strategy,
        "max_dollar_risk": max_risk,
        "correlation_id": correlation_id,
    }, 200)


# ======================================================================
# Helpers
# ======================================================================

def _record_activity(
    payload: dict,
    correlation_id: str,
    response: func.HttpResponse,
    signal=None,
    parse_error: Optional[str] = None,
) -> None:
    """Persist a webhook snapshot for the Azure-centric dashboard."""
    try:
        body = json.loads(response.get_body())
    except Exception:
        body = {"raw_body": response.get_body().decode("utf-8", errors="replace")}

    parsed = None
    if signal is not None:
        parsed = {
            "ticker": signal.ticker,
            "side": signal.side,
            "strategy": signal.strategy,
            "price": signal.price,
            "mode": signal.mode,
            "volume": signal.volume,
            "time": signal.time,
        }

    event = {
        "id": correlation_id,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "parsed": parsed,
        "parse_error": parse_error,
        "execution": {
            "ok": response.status_code < 400,
            "status_code": response.status_code,
            "body": body,
            "message": "executed" if response.status_code < 400 else body.get("error", "error"),
        },
        "signature": build_signature(parsed),
    }
    record_webhook_event(event)
    log_structured(logger, logging.INFO, "Webhook activity recorded", correlation_id)
    return None


def _check_request_safety(correlation_id: str, mode: str = "stock") -> bool:
    broker = get_options_broker() if mode == "options" else get_stock_broker()
    if broker == "tastytrade":
        return check_tastytrade_trading_safety(correlation_id)
    return check_trading_safety(get_alpaca_trading_client(), correlation_id)


def check_tastytrade_trading_safety(correlation_id: str) -> bool:
    """Run safety gates that apply before Tastytrade order entry."""
    check_operator_halt(correlation_id)
    check_live_trading_gate(correlation_id, broker="tastytrade")
    _check_tastytrade_daily_loss_limit(correlation_id)
    return True


def _check_tastytrade_daily_loss_limit(correlation_id: str) -> bool:
    """Optional daily-loss guard for Tastytrade using an operator-provided baseline."""
    max_loss_dollars = _env_float("MAX_DAILY_LOSS_DOLLARS", 0.0)
    max_loss_pct = _env_float("MAX_DAILY_LOSS_PCT", 0.0)
    if max_loss_dollars <= 0 and max_loss_pct <= 0:
        return True

    baseline = _env_float("TASTYTRADE_PREVIOUS_NET_LIQUIDATING_VALUE", 0.0)
    if baseline <= 0:
        log_structured(
            logger, logging.WARNING,
            "Tastytrade daily loss limits configured but TASTYTRADE_PREVIOUS_NET_LIQUIDATING_VALUE is missing",
            correlation_id,
        )
        return True

    equity = get_tastytrade_account_equity(get_tastytrade_client())
    daily_loss = max(0.0, baseline - equity)
    daily_loss_pct = (daily_loss / baseline) * 100.0 if baseline else 0.0

    if max_loss_dollars > 0 and daily_loss >= max_loss_dollars:
        raise DailyLossLimitExceededError(
            f"Daily loss limit reached: ${daily_loss:.2f} loss exceeds "
            f"${max_loss_dollars:.2f} limit"
        )
    if max_loss_pct > 0 and daily_loss_pct >= max_loss_pct:
        raise DailyLossLimitExceededError(
            f"Daily loss limit reached: {daily_loss_pct:.2f}% loss exceeds "
            f"{max_loss_pct:.2f}% limit"
        )
    return True


def _env_float(name: str, default: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default

def _json_response(body: dict, status_code: int) -> func.HttpResponse:
    """Return an ``HttpResponse`` with JSON content type."""
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )
