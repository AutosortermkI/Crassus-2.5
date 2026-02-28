"""
Crassus 2.5 -- Dashboard Flask application.

Local web UI for configuring trading parameters and viewing
Alpaca portfolio data.

Usage:
    python dashboard/app.py
    -> Opens http://localhost:5050 in the default browser
"""

import webbrowser
import threading
import json

from flask import Flask, render_template, request, jsonify

from config_manager import get_config, save_config, save_credentials, read_env, SECRET_KEYS
from alpaca_client import (
    get_account_summary, get_positions, get_recent_orders,
    has_credentials, verify_credentials,
)

app = Flask(__name__)


# ======================================================================
# Page routes
# ======================================================================

@app.route("/")
def index():
    """Serve the single-page dashboard."""
    return render_template("index.html")


# ======================================================================
# Credential API routes
# ======================================================================

@app.route("/api/credentials/check", methods=["GET"])
def api_credentials_check():
    """Check whether credentials are configured and valid."""
    try:
        if not has_credentials():
            return jsonify({"status": "missing"})
        result = verify_credentials()
        if result["ok"]:
            return jsonify({
                "status": "ok",
                "account_id": result["account_id"],
                "paper": result["paper"],
            })
        else:
            return jsonify({"status": "invalid", "message": result["error"]})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/credentials/save", methods=["POST"])
def api_credentials_save():
    """Save Alpaca credentials to .env and verify they work."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        api_key = (data.get("api_key") or "").strip()
        secret_key = (data.get("secret_key") or "").strip()
        paper = data.get("paper", True)

        if not api_key or not secret_key:
            return jsonify({
                "status": "error",
                "message": "API Key and Secret Key are required.",
            }), 400

        # Save first so verify_credentials() picks them up
        save_credentials(api_key, secret_key, paper=paper)

        # Verify they actually work
        result = verify_credentials()
        if result["ok"]:
            return jsonify({
                "status": "ok",
                "message": "Credentials saved and verified.",
                "account_id": result["account_id"],
                "paper": result["paper"],
            })
        else:
            return jsonify({
                "status": "invalid",
                "message": "Saved, but authentication failed: " + result["error"],
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ======================================================================
# API routes
# ======================================================================

@app.route("/api/config", methods=["GET"])
def api_get_config():
    """Return current .env configuration as JSON."""
    try:
        config = get_config()
        return jsonify({"status": "ok", "config": config})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/config", methods=["POST"])
def api_save_config():
    """Save updated configuration values to .env."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        # Filter out secret keys -- those should not be changed via dashboard
        updates = {k: v for k, v in data.items() if k not in SECRET_KEYS}
        save_config(updates)
        return jsonify({"status": "ok", "message": "Configuration saved"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/portfolio", methods=["GET"])
def api_portfolio():
    """Return Alpaca account summary as JSON."""
    try:
        summary = get_account_summary()
        return jsonify({"status": "ok", "portfolio": summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/positions", methods=["GET"])
def api_positions():
    """Return open positions as JSON."""
    try:
        positions = get_positions()
        return jsonify({"status": "ok", "positions": positions})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/orders", methods=["GET"])
def api_orders():
    """Return recent orders as JSON."""
    try:
        orders = get_recent_orders(limit=20)
        return jsonify({"status": "ok", "orders": orders})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ======================================================================
# Webhook API routes
# ======================================================================

@app.route("/api/webhook/info", methods=["GET"])
def api_webhook_info():
    """Return webhook URL and auth token for TradingView setup."""
    try:
        env = read_env()
        token = env.get("WEBHOOK_AUTH_TOKEN", "")
        # Default endpoint assumes Azure deployment name
        return jsonify({
            "status": "ok",
            "local_url": "http://localhost:7071/api/trade",
            "azure_url": "https://crassus-25.azurewebsites.net/api/trade",
            "auth_token": token,
            "has_token": bool(token),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/token", methods=["POST"])
def api_webhook_token_save():
    """Generate or save a webhook auth token."""
    try:
        import secrets as _secrets
        from config_manager import ENV_PATH

        data = request.get_json() or {}
        token = (data.get("token") or "").strip()

        if not token:
            token = _secrets.token_hex(16)

        # save_config updates existing keys; for new keys we write directly
        env = read_env()
        if "WEBHOOK_AUTH_TOKEN" in env:
            save_config({"WEBHOOK_AUTH_TOKEN": token})
        else:
            with open(ENV_PATH, "a") as f:
                f.write(f"\nWEBHOOK_AUTH_TOKEN={token}\n")

        return jsonify({"status": "ok", "token": token})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/test", methods=["POST"])
def api_webhook_test():
    """Send a test webhook to the local or Azure function endpoint."""
    try:
        import requests as http_requests

        data = request.get_json() or {}
        target = data.get("target", "local")
        env = read_env()
        token = env.get("WEBHOOK_AUTH_TOKEN", "")

        if target == "azure":
            url = "https://crassus-25.azurewebsites.net/api/trade"
        else:
            url = "http://localhost:7071/api/trade"

        test_payload = {
            "content": (
                "**New Buy Signal:**\n"
                "AAPL 5 Min Candle\n"
                "Strategy: bollinger_mean_reversion\n"
                "Mode: stock\n"
                "Price: 189.50"
            )
        }

        resp = http_requests.post(
            url,
            json=test_payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Token": token,
            },
            timeout=10,
        )

        return jsonify({
            "status": "ok",
            "response_code": resp.status_code,
            "response_body": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text,
        })
    except http_requests.ConnectionError:
        return jsonify({
            "status": "error",
            "message": f"Cannot connect to {url}. Is the function app running?",
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ======================================================================
# Backtesting API routes
# ======================================================================

@app.route("/api/backtest/strategies", methods=["GET"])
def api_backtest_strategies():
    """Return available strategies for the backtest dropdown."""
    try:
        from strategy import STRATEGY_REGISTRY
        strategies = []
        for name, cfg in sorted(STRATEGY_REGISTRY.items()):
            strategies.append({
                "name": name,
                "stock_tp_pct": cfg.stock_tp_pct,
                "stock_sl_pct": cfg.stock_sl_pct,
                "options_tp_pct": cfg.options_tp_pct,
                "options_sl_pct": cfg.options_sl_pct,
            })
        return jsonify({"status": "ok", "strategies": strategies})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    """Run a backtest and return results as JSON.

    Expected JSON body::

        {
            "ticker": "AAPL",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "interval": "1d",
            "strategy": "bollinger_mean_reversion",
            "mode": "stock",
            "side": "buy",
            "initial_capital": 100000,
            "stock_qty": 10,
            "max_dollar_risk": 50,
            "slippage_pct": 0,
            "commission": 0,
            "signals": [...]   // optional: explicit signal list
        }

    If ``signals`` is not provided, the engine generates entry signals
    at every bar (useful for testing bracket TP/SL behaviour across
    the whole date range), or at specific bars if ``signal_frequency``
    is set (e.g. ``"weekly"`` places one signal per week).
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        ticker = (data.get("ticker") or "").strip().upper()
        start = (data.get("start") or "").strip()
        end = (data.get("end") or "").strip()
        interval = data.get("interval", "1d")
        strategy = data.get("strategy", "bollinger_mean_reversion")
        mode = data.get("mode", "stock")
        side = data.get("side", "buy")
        initial_capital = float(data.get("initial_capital", 100000))
        stock_qty = int(data.get("stock_qty", 10))
        max_dollar_risk = float(data.get("max_dollar_risk", 50))
        slippage_pct = float(data.get("slippage_pct", 0))
        commission = float(data.get("commission", 0))
        signal_frequency = data.get("signal_frequency", "first")
        explicit_signals = data.get("signals")

        if not ticker:
            return jsonify({"status": "error", "message": "Ticker is required."}), 400
        if not start:
            return jsonify({"status": "error", "message": "Start date is required."}), 400
        if not end:
            return jsonify({"status": "error", "message": "End date is required."}), 400

        # 1. Fetch historical bars from Yahoo Finance
        from backtesting.yahoo_fetch import fetch_bars, YahooFetchError
        from backtesting.models import Signal, BacktestConfig
        from backtesting.engine import Engine
        from backtesting.metrics import compute_metrics
        from backtesting.report import generate_report
        from datetime import datetime

        try:
            bars = fetch_bars(ticker, start=start, end=end, interval=interval)
        except YahooFetchError as e:
            return jsonify({"status": "error", "message": f"Data fetch failed: {e}"}), 400
        except ValueError as e:
            return jsonify({"status": "error", "message": str(e)}), 400

        if not bars:
            return jsonify({"status": "error", "message": f"No price data for {ticker} in that range."}), 400

        # 2. Build signals
        if explicit_signals:
            signals = []
            for s in explicit_signals:
                signals.append(Signal(
                    timestamp=datetime.fromisoformat(s["timestamp"]),
                    ticker=s.get("ticker", ticker).upper(),
                    side=s.get("side", side),
                    price=float(s["price"]),
                    strategy=s.get("strategy", strategy),
                    mode=s.get("mode", mode),
                ))
        else:
            signals = _generate_signals(bars, side, strategy, mode, signal_frequency)

        # 3. Run backtest
        config = BacktestConfig(
            initial_capital=initial_capital,
            commission_per_trade=commission,
            slippage_pct=slippage_pct,
            default_stock_qty=stock_qty,
            max_dollar_risk=max_dollar_risk,
        )
        engine = Engine(config=config)
        result = engine.run(bars, signals)
        metrics = compute_metrics(result)
        report = generate_report(result, metrics)

        # 4. Build JSON response
        trades_json = []
        for t in result.trades:
            pos = t.position
            trades_json.append({
                "ticker": pos.ticker,
                "side": pos.side,
                "qty": pos.qty,
                "entry_price": round(pos.entry_price, 4),
                "exit_price": round(pos.exit_price, 4) if pos.exit_price else None,
                "pnl": round(pos.pnl, 2) if pos.pnl is not None else None,
                "pnl_pct": round(pos.pnl_pct, 2) if pos.pnl_pct is not None else None,
                "entry_time": pos.entry_timestamp.isoformat() if pos.entry_timestamp else None,
                "exit_time": pos.exit_timestamp.isoformat() if pos.exit_timestamp else None,
                "strategy": t.strategy,
                "mode": t.mode,
                "exit_type": "tp" if pos.pnl and pos.pnl > 0 else "sl",
            })

        equity_curve = []
        for pt in result.equity_curve:
            equity_curve.append({
                "timestamp": pt["timestamp"],
                "equity": round(pt["equity"], 2),
            })

        pf = metrics.profit_factor
        pf_display = f"{pf:.2f}" if pf != float("inf") else "inf"

        return jsonify({
            "status": "ok",
            "summary": {
                "ticker": ticker,
                "period": f"{result.start_time:%Y-%m-%d} to {result.end_time:%Y-%m-%d}" if result.start_time else "",
                "bars": result.bars_processed,
                "signals_processed": result.signals_processed,
                "signals_skipped": result.signals_skipped,
                "initial_capital": initial_capital,
                "final_equity": round(metrics.final_equity, 2),
                "total_return_pct": round(metrics.total_return_pct, 2),
                "total_pnl": round(metrics.total_pnl, 2),
                "sharpe_ratio": round(metrics.sharpe_ratio, 3),
                "sortino_ratio": round(metrics.sortino_ratio, 3),
                "max_drawdown_pct": round(metrics.drawdown.max_drawdown_pct, 2),
                "max_drawdown_dollar": round(metrics.drawdown.max_drawdown_dollar, 2),
                "total_trades": metrics.total_trades,
                "winning_trades": metrics.winning_trades,
                "losing_trades": metrics.losing_trades,
                "win_rate": round(metrics.win_rate, 1),
                "profit_factor": pf_display,
                "avg_pnl": round(metrics.avg_pnl, 2),
                "avg_win": round(metrics.avg_win, 2),
                "avg_loss": round(metrics.avg_loss, 2),
                "largest_win": round(metrics.largest_win, 2),
                "largest_loss": round(metrics.largest_loss, 2),
                "exposure_pct": round(metrics.exposure_pct, 1),
            },
            "trades": trades_json,
            "equity_curve": equity_curve,
            "report": report,
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


def _generate_signals(bars, side, strategy, mode, frequency):
    """Generate signals from bars based on the chosen frequency.

    Frequencies:
      - ``"first"``: One signal at the first bar only.
      - ``"weekly"``: One signal per week (every 5 bars for daily data).
      - ``"daily"``: One signal every bar (aggressive, for stress testing).
    """
    from backtesting.models import Signal

    if not bars:
        return []

    signals = []

    if frequency == "daily":
        step = 1
    elif frequency == "weekly":
        step = 5
    else:
        # "first" — single signal at the first bar
        bar = bars[0]
        return [Signal(
            timestamp=bar.timestamp,
            ticker=bar.ticker,
            side=side,
            price=bar.close,
            strategy=strategy,
            mode=mode,
        )]

    for i in range(0, len(bars), step):
        bar = bars[i]
        signals.append(Signal(
            timestamp=bar.timestamp,
            ticker=bar.ticker,
            side=side,
            price=bar.close,
            strategy=strategy,
            mode=mode,
        ))

    return signals


# ======================================================================
# Entry point
# ======================================================================

def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    webbrowser.open("http://localhost:5050")


if __name__ == "__main__":
    # Open browser after Flask starts
    threading.Timer(1.5, open_browser).start()
    print("=" * 50)
    print("  Crassus 2.5 Dashboard")
    print("  http://localhost:5050")
    print("=" * 50)
    app.run(host="127.0.0.1", port=5050, debug=False)
