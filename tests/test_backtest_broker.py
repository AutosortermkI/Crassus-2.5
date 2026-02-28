"""
Tests for the simulated broker.
"""

import pytest
from datetime import datetime

from backtesting.models import (
    Bar,
    Signal,
    Order,
    OrderType,
    OrderStatus,
    Position,
    PositionStatus,
    BacktestConfig,
)
from backtesting.broker import SimulatedBroker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return BacktestConfig(initial_capital=100_000.0)


@pytest.fixture
def broker(config):
    return SimulatedBroker(config)


def make_bar(
    ts="2024-01-02 09:30:00",
    o=100.0, h=102.0, l=98.0, c=101.0,
    vol=10000, ticker="AAPL",
):
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=vol, ticker=ticker)


def make_signal(
    ts="2024-01-02 09:30:00",
    ticker="AAPL", side="buy", price=100.0,
    strategy="bollinger_mean_reversion", mode="stock",
):
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    return Signal(timestamp=ts, ticker=ticker, side=side, price=price,
                  strategy=strategy, mode=mode)


# ---------------------------------------------------------------------------
# Basic order submission
# ---------------------------------------------------------------------------

class TestOrderSubmission:
    def test_submit_order(self, broker):
        order = Order(ticker="AAPL", side="buy", qty=10, limit_price=100.0)
        oid = broker.submit_order(order)
        assert oid == order.id
        assert len(broker.pending_orders) == 1

    def test_pending_status(self, broker):
        order = Order(ticker="AAPL", side="buy", qty=10, limit_price=100.0)
        broker.submit_order(order)
        assert order.status == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Limit order fills
# ---------------------------------------------------------------------------

class TestLimitOrderFills:
    def test_buy_fills_when_low_touches_limit(self, broker):
        order = Order(ticker="AAPL", side="buy", qty=10, limit_price=99.0, tag="entry")
        broker.submit_order(order)

        # bar low=98 touches limit=99
        bar = make_bar(l=98.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.FILLED
        assert order.fill_price == 99.0
        assert len(broker.pending_orders) == 0

    def test_buy_does_not_fill_when_low_above_limit(self, broker):
        order = Order(ticker="AAPL", side="buy", qty=10, limit_price=97.0, tag="entry")
        broker.submit_order(order)

        bar = make_bar(l=98.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.PENDING

    def test_sell_fills_when_high_touches_limit(self, broker):
        order = Order(ticker="AAPL", side="sell", qty=10, limit_price=101.5,
                      tag="take_profit")
        broker.submit_order(order)

        bar = make_bar(h=102.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.FILLED
        assert order.fill_price == 101.5

    def test_sell_does_not_fill_when_high_below_limit(self, broker):
        order = Order(ticker="AAPL", side="sell", qty=10, limit_price=103.0,
                      tag="take_profit")
        broker.submit_order(order)

        bar = make_bar(h=102.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Stop order fills
# ---------------------------------------------------------------------------

class TestStopOrderFills:
    def test_stop_sell_fills_when_low_touches_stop(self, broker):
        order = Order(
            ticker="AAPL", side="sell", qty=5,
            order_type=OrderType.STOP, stop_price=99.0,
            tag="stop_loss",
        )
        broker.submit_order(order)

        bar = make_bar(l=98.5)
        broker.on_bar(bar)

        assert order.status == OrderStatus.FILLED
        assert order.fill_price == 99.0

    def test_stop_sell_no_fill_when_low_above_stop(self, broker):
        order = Order(
            ticker="AAPL", side="sell", qty=5,
            order_type=OrderType.STOP, stop_price=97.0,
            tag="stop_loss",
        )
        broker.submit_order(order)

        bar = make_bar(l=98.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.PENDING


# ---------------------------------------------------------------------------
# Market order fills
# ---------------------------------------------------------------------------

class TestMarketOrderFills:
    def test_market_fills_at_open(self, broker):
        order = Order(
            ticker="AAPL", side="buy", qty=5,
            order_type=OrderType.MARKET, tag="entry",
        )
        broker.submit_order(order)

        bar = make_bar(o=150.0)
        broker.on_bar(bar)

        assert order.status == OrderStatus.FILLED
        assert order.fill_price == 150.0


# ---------------------------------------------------------------------------
# Bracket order lifecycle
# ---------------------------------------------------------------------------

class TestBracketOrders:
    def test_full_bracket_tp_hit(self, broker):
        """Entry fills → TP leg activates and fills → SL leg cancelled."""
        signal = make_signal(price=100.0)

        entry = Order(id="E1", ticker="AAPL", side="buy", qty=10,
                      limit_price=100.0, tag="entry")
        tp = Order(id="TP1", ticker="AAPL", side="sell", qty=10,
                   limit_price=102.0, tag="take_profit")
        sl = Order(id="SL1", ticker="AAPL", side="sell", qty=10,
                   order_type=OrderType.STOP, stop_price=98.0, tag="stop_loss")

        broker.submit_bracket_order(
            signal=signal, entry_order=entry, tp_order=tp, sl_order=sl,
            strategy="bollinger_mean_reversion", mode="stock",
            tp_price=102.0, sl_price=98.0,
        )

        # Bar 1: entry fills (low=99 touches 100)
        bar1 = make_bar(ts="2024-01-02 09:30:00", o=100.5, h=101.0, l=99.0, c=100.5)
        broker.on_bar(bar1)

        assert entry.status == OrderStatus.FILLED
        assert len(broker.open_positions) == 1
        # TP and SL should now be pending
        assert len(broker.pending_orders) == 2

        # Bar 2: TP fills (high=103 > 102)
        bar2 = make_bar(ts="2024-01-03 09:30:00", o=101.0, h=103.0, l=100.5, c=102.5)
        broker.on_bar(bar2)

        assert tp.status == OrderStatus.FILLED
        assert sl.status == OrderStatus.CANCELLED
        assert len(broker.open_positions) == 0
        assert len(broker.trades) == 1
        assert broker.trades[0].position.pnl == pytest.approx(20.0)  # (102-100)*10

    def test_full_bracket_sl_hit(self, broker):
        """Entry fills → SL leg fills → TP leg cancelled."""
        signal = make_signal(price=100.0)

        entry = Order(id="E2", ticker="AAPL", side="buy", qty=10,
                      limit_price=100.0, tag="entry")
        tp = Order(id="TP2", ticker="AAPL", side="sell", qty=10,
                   limit_price=105.0, tag="take_profit")
        sl = Order(id="SL2", ticker="AAPL", side="sell", qty=10,
                   order_type=OrderType.STOP, stop_price=98.0, tag="stop_loss")

        broker.submit_bracket_order(
            signal=signal, entry_order=entry, tp_order=tp, sl_order=sl,
            strategy="bollinger_mean_reversion", mode="stock",
            tp_price=105.0, sl_price=98.0,
        )

        # Bar 1: entry fills
        bar1 = make_bar(ts="2024-01-02 09:30:00", o=100.5, h=101.0, l=99.0, c=100.0)
        broker.on_bar(bar1)
        assert entry.status == OrderStatus.FILLED

        # Bar 2: SL fills (low=97 < 98)
        bar2 = make_bar(ts="2024-01-03 09:30:00", o=99.0, h=99.5, l=97.0, c=97.5)
        broker.on_bar(bar2)

        assert sl.status == OrderStatus.FILLED
        assert tp.status == OrderStatus.CANCELLED
        assert len(broker.trades) == 1
        assert broker.trades[0].position.pnl == pytest.approx(-20.0)  # (98-100)*10


# ---------------------------------------------------------------------------
# Cash accounting
# ---------------------------------------------------------------------------

class TestCashAccounting:
    def test_entry_deducts_cash(self, broker):
        signal = make_signal(price=100.0)
        entry = Order(ticker="AAPL", side="buy", qty=10, limit_price=100.0, tag="entry")
        broker._order_meta[entry.id] = {"mode": "stock", "strategy": "test"}
        broker.submit_order(entry)

        bar = make_bar(l=99.0)
        broker.on_bar(bar)

        # 10 shares @ $100 = $1000 deducted
        assert broker.cash == pytest.approx(99_000.0)

    def test_exit_credits_cash(self, broker):
        signal = make_signal(price=100.0)
        entry = Order(id="E", ticker="AAPL", side="buy", qty=10,
                      limit_price=100.0, tag="entry")
        tp = Order(id="TP", ticker="AAPL", side="sell", qty=10,
                   limit_price=105.0, tag="take_profit")
        sl = Order(id="SL", ticker="AAPL", side="sell", qty=10,
                   order_type=OrderType.STOP, stop_price=95.0, tag="stop_loss")

        broker.submit_bracket_order(
            signal=signal, entry_order=entry, tp_order=tp, sl_order=sl,
            mode="stock",
        )

        # Entry fills
        bar1 = make_bar(ts="2024-01-02 09:30:00", l=99.0)
        broker.on_bar(bar1)
        assert broker.cash == pytest.approx(99_000.0)

        # TP fills at 105
        bar2 = make_bar(ts="2024-01-03 09:30:00", h=106.0, l=104.0)
        broker.on_bar(bar2)

        # Cash = 99000 + (105 * 10) = 100050
        assert broker.cash == pytest.approx(100_050.0)


# ---------------------------------------------------------------------------
# Slippage
# ---------------------------------------------------------------------------

class TestSlippage:
    def test_buy_slippage(self):
        cfg = BacktestConfig(initial_capital=100_000, slippage_pct=0.1)
        b = SimulatedBroker(cfg)

        order = Order(ticker="AAPL", side="buy", qty=10, limit_price=100.0, tag="entry")
        b._order_meta[order.id] = {"mode": "stock", "strategy": "test"}
        b.submit_order(order)

        bar = make_bar(l=99.0)
        b.on_bar(bar)

        # 0.1% slippage on $100 = $0.10 adverse → fill at $100.10
        assert order.fill_price == pytest.approx(100.10)


# ---------------------------------------------------------------------------
# Cancel all
# ---------------------------------------------------------------------------

class TestCancelAll:
    def test_cancel_clears_pending(self, broker):
        broker.submit_order(Order(ticker="AAPL", side="buy", limit_price=100.0))
        broker.submit_order(Order(ticker="MSFT", side="buy", limit_price=200.0))
        count = broker.cancel_all_pending()
        assert count == 2
        assert len(broker.pending_orders) == 0
        assert len(broker.cancelled_orders) == 2


# ---------------------------------------------------------------------------
# Mark to market
# ---------------------------------------------------------------------------

class TestMarkToMarket:
    def test_no_positions(self, broker):
        bar = make_bar(c=150.0)
        assert broker.mark_to_market(bar) == pytest.approx(100_000.0)

    def test_with_open_position(self, broker):
        signal = make_signal(price=100.0)
        entry = Order(ticker="AAPL", side="buy", qty=10, limit_price=100.0, tag="entry")
        broker._order_meta[entry.id] = {"mode": "stock", "strategy": "test"}
        broker.submit_order(entry)

        bar1 = make_bar(ts="2024-01-02 09:30:00", l=99.0, c=100.0)
        broker.on_bar(bar1)

        # Cash = 99000, position = 10 * close(105) = 1050
        bar2 = make_bar(ts="2024-01-03 09:30:00", c=105.0)
        equity = broker.mark_to_market(bar2)
        assert equity == pytest.approx(99_000.0 + 10 * 105.0)
