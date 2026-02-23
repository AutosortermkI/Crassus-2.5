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

from config_manager import get_config, save_config, save_credentials, SECRET_KEYS
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
