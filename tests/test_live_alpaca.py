"""
Crassus 2.5 -- Live Alpaca Paper Trading Integration Test.

This script tests real connectivity against the Alpaca Paper Trading API.
It is NOT a unit test -- it makes actual API calls and submits real paper orders.

Tests performed:
  1. API authentication & connectivity
  2. Account info retrieval (buying power, equity, status)
  3. List current positions
  4. Submit a stock bracket order (1 share, cheap stock)
  5. Verify order appears in order list
  6. Cancel the test order
  7. Verify cancellation
  8. End-to-end webhook-style signal through Crassus parsing + order construction

Usage:
    python tests/test_live_alpaca.py

Requires:
    .env file at project root with ALPACA_API_KEY and ALPACA_SECRET_KEY
"""

import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: load .env and add function_app to path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

def load_dotenv_simple(path: Path):
    """Minimal .env loader -- no external dependency needed."""
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Remove surrounding quotes if present
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            os.environ.setdefault(key, value)

load_dotenv_simple(ENV_FILE)

# Add function_app to Python path so we can import Crassus modules
sys.path.insert(0, str(PROJECT_ROOT / "function_app"))

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    TakeProfitRequest,
    StopLossRequest,
)
from alpaca.trading.enums import (
    OrderSide,
    TimeInForce,
    OrderClass,
    QueryOrderStatus,
)
from alpaca.common.exceptions import APIError

# Import Crassus modules
from parser import parse_webhook_content
from strategy import get_strategy, compute_stock_bracket_prices
from stock_orders import StockBracketParams, build_stock_bracket_order
from risk import compute_stock_qty
from utils import round_stock_price


# ---------------------------------------------------------------------------
# Test configuration
# ---------------------------------------------------------------------------
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.environ.get("ALPACA_PAPER", "true").lower() == "true"

# Test stock: use AAPL as it's always liquid and well-known
TEST_TICKER = "AAPL"
# We'll use a far-from-market limit price so it won't actually fill
TEST_PRICE_OFFSET = 0.50  # Place order $X below market for buy, won't fill


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.error = None
        self.details = {}

    def ok(self, **details):
        self.passed = True
        self.details = details
        return self

    def fail(self, error, **details):
        self.passed = False
        self.error = str(error)
        self.details = details
        return self


def header(text: str):
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def result_line(result: TestResult):
    status = "PASS" if result.passed else "FAIL"
    icon = "[+]" if result.passed else "[-]"
    print(f"  {icon} {result.name}: {status}")
    if result.details:
        for k, v in result.details.items():
            print(f"      {k}: {v}")
    if result.error:
        print(f"      ERROR: {result.error}")


# ---------------------------------------------------------------------------
# Test functions
# ---------------------------------------------------------------------------

def test_authentication(client: TradingClient) -> TestResult:
    """Test 1: Verify API keys authenticate successfully."""
    r = TestResult("API Authentication")
    try:
        account = client.get_account()
        return r.ok(
            account_id=str(account.id),
            status=str(account.status),
        )
    except APIError as e:
        return r.fail(e, hint="Check ALPACA_API_KEY and ALPACA_SECRET_KEY")
    except Exception as e:
        return r.fail(e)


def test_account_info(client: TradingClient) -> TestResult:
    """Test 2: Retrieve and display account information."""
    r = TestResult("Account Information")
    try:
        account = client.get_account()
        return r.ok(
            equity=f"${float(account.equity):,.2f}",
            buying_power=f"${float(account.buying_power):,.2f}",
            cash=f"${float(account.cash):,.2f}",
            portfolio_value=f"${float(account.portfolio_value):,.2f}",
            account_status=str(account.status),
            trading_blocked=str(account.trading_blocked),
            pattern_day_trader=str(account.pattern_day_trader),
            paper=str(ALPACA_PAPER),
        )
    except Exception as e:
        return r.fail(e)


def test_list_positions(client: TradingClient) -> TestResult:
    """Test 3: List any open positions."""
    r = TestResult("List Positions")
    try:
        positions = client.get_all_positions()
        if not positions:
            return r.ok(count=0, message="No open positions")
        pos_summary = []
        for p in positions[:5]:  # Show up to 5
            pos_summary.append(f"{p.symbol}: {p.qty} shares @ ${float(p.avg_entry_price):.2f}")
        return r.ok(
            count=len(positions),
            positions="; ".join(pos_summary),
        )
    except Exception as e:
        return r.fail(e)


def test_webhook_parsing() -> TestResult:
    """Test 4: Parse a simulated TradingView webhook through Crassus parser."""
    r = TestResult("Webhook Parsing (Crassus)")
    try:
        content = (
            f"**New Buy Signal:**\n"
            f"{TEST_TICKER} 5 Min Candle\n"
            f"Strategy: bollinger_mean_reversion\n"
            f"Mode: stock\n"
            f"Volume: 5000000\n"
            f"Price: 230.00\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}"
        )
        signal = parse_webhook_content(content)
        strategy_config = get_strategy(signal.strategy)
        tp, stop, stop_limit = compute_stock_bracket_prices(
            entry_price=signal.price,
            side=signal.side,
            config=strategy_config,
        )
        return r.ok(
            ticker=signal.ticker,
            side=signal.side,
            strategy=signal.strategy,
            mode=signal.mode,
            price=signal.price,
            take_profit=round_stock_price(tp),
            stop_loss=round_stock_price(stop),
            stop_limit=round_stock_price(stop_limit),
        )
    except Exception as e:
        return r.fail(e)


def test_order_construction() -> TestResult:
    """Test 5: Verify Crassus builds valid Alpaca order objects (no submission)."""
    r = TestResult("Order Construction (dry run)")
    try:
        entry_price = 230.00
        strategy_config = get_strategy("bollinger_mean_reversion")
        tp, stop, stop_limit = compute_stock_bracket_prices(
            entry_price=entry_price,
            side="buy",
            config=strategy_config,
        )

        params = StockBracketParams(
            symbol=TEST_TICKER,
            side="buy",
            qty=1,
            entry_price=entry_price,
            take_profit_price=tp,
            stop_price=stop,
            stop_limit_price=stop_limit,
        )

        order_request = build_stock_bracket_order(params)

        # Validate the order request object is correctly formed
        assert order_request.symbol == TEST_TICKER
        assert order_request.qty == 1
        assert order_request.side == OrderSide.BUY
        assert order_request.order_class == OrderClass.BRACKET
        assert order_request.time_in_force == TimeInForce.GTC
        assert order_request.limit_price == round_stock_price(entry_price)
        assert order_request.take_profit is not None
        assert order_request.stop_loss is not None

        return r.ok(
            symbol=order_request.symbol,
            side=str(order_request.side),
            qty=str(order_request.qty),
            limit_price=str(order_request.limit_price),
            take_profit=str(order_request.take_profit.limit_price),
            stop_price=str(order_request.stop_loss.stop_price),
            stop_limit=str(order_request.stop_loss.limit_price),
            order_class=str(order_request.order_class),
        )
    except Exception as e:
        return r.fail(e)


def test_submit_stock_order(client: TradingClient, buying_power: float) -> TestResult:
    """Test 6: Submit a real stock bracket order to Alpaca paper.

    Uses a limit price well below market so it will NOT fill.
    Skips if account has no buying power.
    """
    r = TestResult("Submit Stock Bracket Order")

    if buying_power < 150:
        return r.fail(
            f"Insufficient buying power (${buying_power:.2f}). "
            "Need to close positions or reset paper account to free up capital.",
            buying_power=f"${buying_power:.2f}",
            hint="This is an account state issue, not a code bug",
        )

    try:
        entry_price = 100.00  # Far below AAPL market price, won't fill
        strategy_config = get_strategy("bollinger_mean_reversion")
        tp, stop, stop_limit = compute_stock_bracket_prices(
            entry_price=entry_price,
            side="buy",
            config=strategy_config,
        )

        params = StockBracketParams(
            symbol=TEST_TICKER,
            side="buy",
            qty=1,
            entry_price=entry_price,
            take_profit_price=tp,
            stop_price=stop,
            stop_limit_price=stop_limit,
        )

        order_request = build_stock_bracket_order(params)
        order = client.submit_order(order_data=order_request)

        return r.ok(
            order_id=str(order.id),
            symbol=order.symbol,
            side=str(order.side),
            qty=str(order.qty),
            limit_price=str(order.limit_price),
            order_class=str(order.order_class),
            status=str(order.status),
            time_in_force=str(order.time_in_force),
        )
    except APIError as e:
        return r.fail(e, hint="Alpaca API rejected the order")
    except Exception as e:
        return r.fail(e)


def test_verify_order(client: TradingClient, order_id: str) -> TestResult:
    """Test 6: Verify the submitted order appears in the order list."""
    r = TestResult("Verify Order in List")
    try:
        order = client.get_order_by_id(order_id)
        return r.ok(
            order_id=str(order.id),
            status=str(order.status),
            symbol=order.symbol,
            created_at=str(order.created_at),
        )
    except Exception as e:
        return r.fail(e)


def test_cancel_order(client: TradingClient, order_id: str) -> TestResult:
    """Test 7: Cancel the test order."""
    r = TestResult("Cancel Test Order")
    try:
        client.cancel_order_by_id(order_id)
        # Give Alpaca a moment to process
        time.sleep(1)

        # Verify it's cancelled
        order = client.get_order_by_id(order_id)
        return r.ok(
            order_id=str(order.id),
            final_status=str(order.status),
        )
    except Exception as e:
        return r.fail(e)


def test_list_open_orders(client: TradingClient) -> TestResult:
    """Test 8: List open orders to confirm cleanup."""
    r = TestResult("List Open Orders (post-cleanup)")
    try:
        request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        orders = client.get_orders(filter=request)
        if not orders:
            return r.ok(open_orders=0, message="No open orders -- clean state")
        order_summary = []
        for o in orders[:5]:
            order_summary.append(f"{o.symbol} {o.side} {o.qty}x @ {o.limit_price}")
        return r.ok(
            open_orders=len(orders),
            orders="; ".join(order_summary),
        )
    except Exception as e:
        return r.fail(e)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def main():
    header("Crassus 2.5 -- Live Alpaca Paper Trading Test")
    print(f"  Timestamp : {datetime.now(timezone.utc).isoformat()}")
    print(f"  Paper mode: {ALPACA_PAPER}")
    print(f"  API key   : {ALPACA_API_KEY[:6]}...{ALPACA_API_KEY[-4:]}")
    print(f"  Test stock: {TEST_TICKER}")

    if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
        print("\n  ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        sys.exit(1)

    if not ALPACA_PAPER:
        print("\n  SAFETY CHECK: ALPACA_PAPER is NOT true. Refusing to run live test.")
        print("  Set ALPACA_PAPER=true in .env for testing.")
        sys.exit(1)

    # Initialize Alpaca client
    client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=True)

    results = []
    submitted_order_id = None
    buying_power = 0.0

    # ---- Test 1: Authentication ----
    header("Test 1: API Authentication")
    r1 = test_authentication(client)
    results.append(r1)
    result_line(r1)
    if not r1.passed:
        print("\n  FATAL: Authentication failed. Cannot proceed with remaining tests.")
        print_summary(results)
        sys.exit(1)

    # ---- Test 2: Account Info ----
    header("Test 2: Account Information")
    r2 = test_account_info(client)
    results.append(r2)
    result_line(r2)
    # Extract buying power for later use
    try:
        account = client.get_account()
        buying_power = float(account.buying_power)
    except Exception:
        pass

    # ---- Test 3: List Positions ----
    header("Test 3: Current Positions")
    r3 = test_list_positions(client)
    results.append(r3)
    result_line(r3)

    # ---- Test 4: Webhook Parsing ----
    header("Test 4: Crassus Webhook Parsing + Strategy")
    r4 = test_webhook_parsing()
    results.append(r4)
    result_line(r4)

    # ---- Test 5: Order Construction (dry run, no submission) ----
    header("Test 5: Order Construction (dry run)")
    r5 = test_order_construction()
    results.append(r5)
    result_line(r5)

    # ---- Test 6: Submit Order ----
    header("Test 6: Submit Stock Bracket Order")
    r6 = test_submit_stock_order(client, buying_power)
    results.append(r6)
    result_line(r6)
    if r6.passed:
        submitted_order_id = r6.details.get("order_id")

    # ---- Test 7 & 8: Verify + Cancel Order ----
    if submitted_order_id:
        header("Test 7: Verify Order")
        r7 = test_verify_order(client, submitted_order_id)
        results.append(r7)
        result_line(r7)

        header("Test 8: Cancel Test Order")
        r8 = test_cancel_order(client, submitted_order_id)
        results.append(r8)
        result_line(r8)
    else:
        print("\n  SKIP: Tests 7-8 skipped (no order submitted)")

    # ---- Test 9: Verify Clean State ----
    header("Test 9: Open Orders (post-cleanup)")
    r9 = test_list_open_orders(client)
    results.append(r9)
    result_line(r9)

    # ---- Summary ----
    print_summary(results)


def print_summary(results):
    header("TEST SUMMARY")
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        icon = "[+]" if r.passed else "[-]"
        print(f"  {icon} {r.name}: {status}")

    print(f"\n  Total: {total}  |  Passed: {passed}  |  Failed: {failed}")

    if failed == 0:
        print("\n  ALL TESTS PASSED -- Alpaca paper trading integration verified.")
    else:
        print(f"\n  {failed} TEST(S) FAILED -- review errors above.")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
