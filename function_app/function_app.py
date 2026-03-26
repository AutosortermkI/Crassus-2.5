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
from safety import check_live_trading_gate, LiveTradingNotConfirmedError
from order_monitor import submit_with_retry, check_stock_orders, cancel_stale_orders
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
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER      = os.environ.get("ALPACA_PAPER", "true").lower() == "true"
WEBHOOK_AUTH_TOKEN = os.environ.get("WEBHOOK_AUTH_TOKEN", "")

# Alpaca client -- reused across requests for connection pooling.
# Module-level init runs once per Azure Functions cold start.
trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=ALPACA_PAPER)

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


# ======================================================================
# HTTP Trigger
# ======================================================================

@app.route(route="trade", methods=["POST"])
def trade(req: func.HttpRequest) -> func.HttpResponse:
    """Main webhook handler: authenticate -> parse -> route -> order."""

    correlation_id = generate_correlation_id()
    log_structured(logger, logging.INFO, "Received trade request", correlation_id)

    # ----------------------------------------------------------------
    # 0. Live trading safety gate
    # ----------------------------------------------------------------
    try:
        check_live_trading_gate(correlation_id)
    except LiveTradingNotConfirmedError as e:
        return _json_response({"error": str(e), "correlation_id": correlation_id}, 403)

    # ----------------------------------------------------------------
    # 1. Authentication: validate token (header or query parameter)
    # ----------------------------------------------------------------
    token = req.headers.get("X-Webhook-Token", "") or req.params.get("token", "")
    if not token or not hmac.compare_digest(token, WEBHOOK_AUTH_TOKEN):
        log_structured(logger, logging.WARNING, "Unauthorized request", correlation_id)
        return _json_response({"error": "Unauthorized", "correlation_id": correlation_id}, 401)

    # ----------------------------------------------------------------
    # 2. Parse webhook body
    # ----------------------------------------------------------------
    try:
        data = req.get_json()
    except ValueError:
        log_structured(logger, logging.WARNING, "Invalid JSON body", correlation_id)
        return _json_response({"error": "Invalid JSON", "correlation_id": correlation_id}, 400)

    signal = None

    try:
        signal = parse_webhook_payload(data)
    except ParseError as e:
        log_structured(logger, logging.WARNING, f"Parse error: {e}", correlation_id)
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 400)
        _record_activity(data, correlation_id, response, signal=None, parse_error=str(e))
        return response

    log_structured(
        logger, logging.INFO, "Signal parsed", correlation_id,
        ticker=signal.ticker, side=signal.side,
        strategy=signal.strategy, price=signal.price, mode=signal.mode,
    )

    # ----------------------------------------------------------------
    # 2b. Signal deduplication
    # ----------------------------------------------------------------
    if is_duplicate_signal(
        ticker=signal.ticker,
        side=signal.side,
        strategy=signal.strategy,
        mode=signal.mode,
        price=signal.price,
    ):
        log_structured(
            logger, logging.WARNING, "Duplicate signal rejected", correlation_id,
            ticker=signal.ticker, side=signal.side, strategy=signal.strategy,
        )
        return _json_response({
            "error": "Duplicate signal",
            "detail": "This signal was already processed recently",
            "correlation_id": correlation_id,
        }, 409)

    # ----------------------------------------------------------------
    # 3. Strategy lookup
    # ----------------------------------------------------------------
    try:
        strategy_config = get_strategy(signal.strategy)
    except UnknownStrategyError as e:
        log_structured(logger, logging.WARNING, f"Unknown strategy: {e}", correlation_id)
        response = _json_response({"error": str(e), "correlation_id": correlation_id}, 400)
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response

    # ----------------------------------------------------------------
    # 4. Route: stock or options
    # ----------------------------------------------------------------
    try:
        if signal.mode == "stock":
            response = _handle_stock_order(signal, strategy_config, correlation_id)
        elif signal.mode == "options":
            response = _handle_options_order(signal, strategy_config, correlation_id)
        else:
            response = _json_response(
                {"error": f"Unsupported mode: {signal.mode}", "correlation_id": correlation_id},
                400,
            )
        _record_activity(data, correlation_id, response, signal=signal)
        return response

    except APIError as e:
        log_structured(
            logger, logging.ERROR, f"Alpaca API error: {e}", correlation_id,
            symbol=signal.ticker, mode=signal.mode,
        )
        response = _json_response(
            {"error": f"Alpaca API error: {str(e)}", "correlation_id": correlation_id},
            502,
        )
        _record_activity(data, correlation_id, response, signal=signal, parse_error=str(e))
        return response

    except Exception as e:
        log_structured(
            logger, logging.ERROR, f"Internal error: {e}", correlation_id,
            symbol=signal.ticker, mode=signal.mode,
        )
        response = _json_response(
            {"error": f"Internal error: {str(e)}", "correlation_id": correlation_id},
            500,
        )
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
        actions = check_options_exits(trading_client)
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
        events = check_stock_orders(trading_client, correlation_id)
        if events:
            log_structured(
                logger, logging.INFO,
                f"Order status events: {len(events)}",
                correlation_id,
            )

        # Cancel unfilled orders older than configured minutes (default 120)
        stale_minutes = int(os.environ.get("STALE_ORDER_MINUTES", "120"))
        cancelled = cancel_stale_orders(trading_client, stale_minutes, correlation_id)
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

def _handle_stock_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Process a stock bracket order with full production safety checks."""

    # -- Pre-flight: position limit check --
    try:
        validate_position_limit(trading_client, correlation_id)
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
        equity = get_account_equity(trading_client)
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
        validate_buying_power(trading_client, required_dollars, correlation_id)
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
        lambda: submit_stock_order(trading_client, params, correlation_id),
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


def _handle_options_order(signal, strategy_config, correlation_id: str) -> func.HttpResponse:
    """Process an options order with contract screening and risk sizing."""

    # 1. Screen for the best options contract
    try:
        contract = screen_option_contracts(
            client=trading_client,
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

    order_id = submit_options_entry_order(trading_client, params, correlation_id)

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

def _json_response(body: dict, status_code: int) -> func.HttpResponse:
    """Return an ``HttpResponse`` with JSON content type."""
    return func.HttpResponse(
        json.dumps(body),
        status_code=status_code,
        mimetype="application/json",
    )
