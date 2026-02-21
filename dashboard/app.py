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

from config_manager import get_config, save_config, SECRET_KEYS
from alpaca_client import get_account_summary, get_positions, get_recent_orders

app = Flask(__name__)


# ======================================================================
# Page routes
# ======================================================================

@app.route("/")
def index():
    """Serve the single-page dashboard."""
    return render_template("index.html")


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
