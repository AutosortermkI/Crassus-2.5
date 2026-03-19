"""
Crassus 2.5 -- Dashboard Flask application.

Web UI for configuring webhook routing, reviewing shared TradingView
activity, and optionally monitoring Alpaca portfolio data.

Usage:
    python dashboard/app.py
    -> Opens http://localhost:5050 in the default browser
"""

import os
import webbrowser
import threading
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests as http_requests
from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash

ROOT_DIR = Path(__file__).resolve().parent.parent
FUNCTION_APP_DIR = ROOT_DIR / "function_app"
if str(FUNCTION_APP_DIR) not in sys.path:
    sys.path.insert(0, str(FUNCTION_APP_DIR))

from config_manager import (
    get_config, save_config, save_credentials, read_env, SECRET_KEYS,
    ensure_dashboard_session_secret, ensure_webhook_token, get_azure_function_activity_url,
    get_azure_function_trade_url, sync_settings_to_azure,
)
from alpaca_client import (
    get_account_summary, get_positions, get_recent_orders,
    has_credentials, verify_credentials,
)
from webhook_store import build_signature, clear_events, get_activity_snapshot, record_event
from parser import ParseError, parse_webhook_payload

app = Flask(__name__)
app.secret_key = ensure_dashboard_session_secret()
_HOSTED_DASHBOARD = bool(os.environ.get("WEBSITE_SITE_NAME"))
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=_HOSTED_DASHBOARD,
    SESSION_COOKIE_NAME="crassus_dashboard_session",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dashboard_receive_url() -> str:
    return request.host_url.rstrip("/") + "/api/webhook/receive"


def _dashboard_access_passwords() -> tuple[str, str]:
    env = read_env()
    return (
        (env.get("DASHBOARD_ACCESS_PASSWORD") or "").strip(),
        (env.get("DASHBOARD_ACCESS_PASSWORD_HASH") or "").strip(),
    )


def _dashboard_auth_enabled() -> bool:
    password, password_hash = _dashboard_access_passwords()
    return bool(password or password_hash)


def _dashboard_is_authenticated() -> bool:
    return session.get("dashboard_authenticated") is True


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


def _verify_dashboard_password(candidate: str) -> bool:
    password, password_hash = _dashboard_access_passwords()
    if password_hash:
        return check_password_hash(password_hash, candidate)
    return bool(password) and candidate == password


def _login_redirect_target() -> str:
    next_url = (request.args.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _forwarding_target() -> tuple[str, str]:
    env = read_env()
    target = (env.get("WEBHOOK_FORWARD_TARGET") or "local").strip().lower() or "local"
    custom_url = (env.get("WEBHOOK_FORWARD_URL") or "").strip()

    if target == "none":
        return target, ""
    if target == "custom":
        return target, custom_url
    if target == "azure":
        return target, get_azure_function_trade_url(env)
    return "local", "http://localhost:7071/api/trade"


def _trade_endpoint_url() -> str:
    target, url = _forwarding_target()
    if target == "none":
        return _dashboard_receive_url()
    return url


def _activity_endpoint_url() -> str:
    target, url = _forwarding_target()
    if target == "none":
        return ""
    if target == "azure":
        return get_azure_function_activity_url(read_env())
    if url.endswith("/trade"):
        return url[:-6] + "/webhook-activity"
    return ""


def _webhook_store_limits() -> tuple[int, int]:
    env = read_env()
    try:
        active_minutes = int(env.get("WEBHOOK_ACTIVE_MINUTES", "60"))
    except ValueError:
        active_minutes = 60
    try:
        max_snapshots = int(env.get("WEBHOOK_MAX_SNAPSHOTS", "50"))
    except ValueError:
        max_snapshots = 50
    return max(1, active_minutes), max(1, max_snapshots)


def _forward_webhook(payload: dict, token: str, parsed: Optional[dict]) -> dict:
    """Forward a stored webhook to the configured execution endpoint."""
    target, url = _forwarding_target()
    result = {
        "target": target,
        "url": url,
    }

    if target == "none":
        result["ok"] = True
        result["message"] = "Stored in dashboard only"
        return result

    if not url:
        result["ok"] = False
        result["error"] = "Forward target is custom but WEBHOOK_FORWARD_URL is empty."
        return result

    if parsed is None:
        result["ok"] = False
        result["error"] = "Webhook was stored but not forwarded because parsing failed."
        return result

    try:
        resp = http_requests.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Token": token,
            },
            timeout=10,
        )
        body = resp.text
        if resp.headers.get("content-type", "").startswith("application/json"):
            try:
                body = resp.json()
            except ValueError:
                body = resp.text
        result.update({
            "ok": 200 <= resp.status_code < 300,
            "status_code": resp.status_code,
            "response_body": body,
        })
        return result
    except http_requests.ConnectionError:
        result["ok"] = False
        result["error"] = f"Cannot connect to {url}"
        return result
    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        return result


def _capture_webhook(payload: dict, source: str) -> dict:
    """Normalize, store, and optionally forward a webhook payload."""
    token = ensure_webhook_token()
    parse_error = None
    parsed_signal = None

    try:
        parsed_signal = parse_webhook_payload(payload)
    except ParseError as e:
        parse_error = str(e)

    parsed_dict = vars(parsed_signal) if parsed_signal else None
    forward = _forward_webhook(payload, token, parsed_dict)
    _, max_snapshots = _webhook_store_limits()

    event = {
        "id": uuid.uuid4().hex[:12],
        "received_at": _utcnow_iso(),
        "source": source,
        "payload": payload,
        "parsed": parsed_dict,
        "parse_error": parse_error,
        "forward": forward,
        "signature": build_signature(parsed_dict),
    }
    return record_event(event, max_snapshots=max_snapshots)


@app.before_request
def require_dashboard_login():
    """Gate dashboard routes behind a shared access password when configured."""
    if not _dashboard_auth_enabled():
        return None

    if request.endpoint in {"login", "logout", "static", "api_webhook_receive"}:
        return None

    if _dashboard_is_authenticated():
        return None

    if _is_api_request():
        return jsonify({
            "status": "unauthorized",
            "message": "Dashboard login required.",
        }), 401

    return redirect(url_for("login", next=request.full_path if request.query_string else request.path))


# ======================================================================
# Page routes
# ======================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    """Authenticate a shared partner session for the dashboard."""
    if not _dashboard_auth_enabled():
        return redirect("/")

    error = None
    if request.method == "POST":
        password = (request.form.get("password") or "").strip()
        next_url = (request.form.get("next") or "").strip()
        if _verify_dashboard_password(password):
            session.permanent = True
            session["dashboard_authenticated"] = True
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect("/")
        error = "Incorrect dashboard password."

    return render_template(
        "login.html",
        error=error,
        next_url=_login_redirect_target(),
    )


@app.route("/logout", methods=["POST"])
def logout():
    """Clear the dashboard login session."""
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
def index():
    """Serve the single-page dashboard."""
    return render_template("index.html", dashboard_auth_enabled=_dashboard_auth_enabled())


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
            # Also sync credentials to Azure
            azure_result = sync_settings_to_azure({
                "ALPACA_API_KEY": api_key,
                "ALPACA_SECRET_KEY": secret_key,
                "ALPACA_PAPER": "true" if paper else "false",
            })
            msg = "Credentials saved and verified."
            if not azure_result["ok"]:
                msg += f" (Azure sync failed: {azure_result['error']})"
            return jsonify({
                "status": "ok",
                "message": msg,
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
    """Save updated configuration values to .env and sync to Azure."""
    try:
        data = request.get_json()
        if not data:
            return jsonify({"status": "error", "message": "No data provided"}), 400

        # Filter out secret keys -- those should not be changed via dashboard
        updates = {k: v for k, v in data.items() if k not in SECRET_KEYS}
        save_config(updates)

        # Sync to Azure Function App so live environment stays in sync
        azure_result = sync_settings_to_azure(updates)
        if azure_result["ok"]:
            return jsonify({"status": "ok", "message": "Configuration saved and synced to Azure"})
        else:
            return jsonify({
                "status": "ok",
                "message": f"Configuration saved locally. Azure sync failed: {azure_result['error']}",
                "azure_error": azure_result["error"],
            })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/portfolio", methods=["GET"])
def api_portfolio():
    """Return Alpaca account summary as JSON."""
    try:
        if not has_credentials():
            return jsonify({
                "status": "missing",
                "message": "Add Alpaca credentials to enable the broker snapshot.",
            })
        summary = get_account_summary()
        return jsonify({"status": "ok", "portfolio": summary})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/positions", methods=["GET"])
def api_positions():
    """Return open positions as JSON."""
    try:
        if not has_credentials():
            return jsonify({
                "status": "missing",
                "message": "Add Alpaca credentials to enable positions.",
            })
        positions = get_positions()
        return jsonify({"status": "ok", "positions": positions})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/orders", methods=["GET"])
def api_orders():
    """Return recent orders as JSON."""
    try:
        if not has_credentials():
            return jsonify({
                "status": "missing",
                "message": "Add Alpaca credentials to enable recent orders.",
            })
        orders = get_recent_orders(limit=20)
        return jsonify({"status": "ok", "orders": orders})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ======================================================================
# Webhook API routes
# ======================================================================

@app.route("/api/webhook/info", methods=["GET"])
def api_webhook_info():
    """Return dashboard webhook URL, auth token, and forwarding metadata."""
    try:
        token = ensure_webhook_token()
        forward_target, forward_url = _forwarding_target()
        receive_url = _trade_endpoint_url()
        return jsonify({
            "status": "ok",
            "local_url": receive_url,
            "full_url": f"{receive_url}?token={token}",
            "auth_token": token,
            "has_token": True,
            "forward_target": forward_target,
            "forward_url": forward_url,
            "activity_url": _activity_endpoint_url(),
            "dashboard_url": request.host_url.rstrip("/"),
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/token", methods=["POST"])
def api_webhook_token_save():
    """Generate or save a webhook auth token."""
    try:
        import secrets as _secrets

        data = request.get_json() or {}
        token = (data.get("token") or "").strip()

        if not token:
            token = _secrets.token_hex(16)

        save_config({"WEBHOOK_AUTH_TOKEN": token}, allow_secret_keys=True)

        # Sync token to Azure
        sync_settings_to_azure({"WEBHOOK_AUTH_TOKEN": token})

        return jsonify({"status": "ok", "token": token})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/activity", methods=["GET"])
def api_webhook_activity():
    """Return the recent webhook snapshot plus grouped active webhooks."""
    try:
        active_minutes, _ = _webhook_store_limits()
        activity_url = _activity_endpoint_url()
        if activity_url:
            token = ensure_webhook_token()
            response = http_requests.get(
                activity_url,
                params={"token": token, "active_minutes": active_minutes, "limit": 20},
                timeout=10,
            )
            if response.headers.get("content-type", "").startswith("application/json"):
                body = response.json()
            else:
                body = {"error": response.text}
            if response.status_code >= 400:
                return jsonify({
                    "status": "error",
                    "message": body.get("error") or f"Activity endpoint returned {response.status_code}",
                }), response.status_code
            return jsonify({"status": "ok", **body})

        snapshot = get_activity_snapshot(active_window_minutes=active_minutes)
        return jsonify({"status": "ok", **snapshot})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/clear", methods=["POST"])
def api_webhook_clear():
    """Clear stored webhook snapshots from disk."""
    try:
        clear_events()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/receive", methods=["POST"])
def api_webhook_receive():
    """TradingView-facing webhook inbox used by the dashboard."""
    try:
        expected_token = ensure_webhook_token()
        token = request.headers.get("X-Webhook-Token", "") or request.args.get("token", "")
        if not token or token != expected_token:
            return jsonify({"status": "error", "message": "Unauthorized"}), 401

        payload = request.get_json(silent=True)
        if payload is None:
            raw_text = request.get_data(as_text=True).strip()
            if not raw_text:
                return jsonify({"status": "error", "message": "No payload provided"}), 400
            payload = {"content": raw_text}
        if not isinstance(payload, dict):
            return jsonify({"status": "error", "message": "Webhook payload must be a JSON object"}), 400

        event = _capture_webhook(payload, source="tradingview")
        return jsonify({
            "status": "ok",
            "event_id": event["id"],
            "parsed": event["parsed"],
            "parse_error": event["parse_error"],
            "forward": event["forward"],
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/webhook/test", methods=["POST"])
def api_webhook_test():
    """Create a synthetic webhook snapshot and forward it using dashboard rules."""
    try:
        test_payload = {
            "content": (
                "**New Buy Signal:**\n"
                "AAPL 5 Min Candle\n"
                "Strategy: bollinger_mean_reversion\n"
                "Mode: stock\n"
                "Price: 189.50"
            )
        }
        trade_url = _trade_endpoint_url()
        if trade_url == _dashboard_receive_url():
            event = _capture_webhook(test_payload, source="dashboard_test")
            return jsonify({
                "status": "ok",
                "event_id": event["id"],
                "parsed": event["parsed"],
                "parse_error": event["parse_error"],
                "forward": event["forward"],
            })

        token = ensure_webhook_token()
        response = http_requests.post(
            trade_url,
            params={"token": token},
            json=test_payload,
            timeout=10,
        )
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text
        return jsonify({
            "status": "ok" if response.status_code < 400 else "error",
            "response_code": response.status_code,
            "response_body": body,
            "trade_url": trade_url,
        }), (200 if response.status_code < 400 else response.status_code)
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


# ======================================================================
# Entry point
# ======================================================================

def open_browser():
    """Open the dashboard in the default browser after a short delay."""
    port = int(os.environ.get("PORT", "5050"))
    webbrowser.open(f"http://localhost:{port}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5050"))

    # Open browser after Flask starts
    threading.Timer(1.5, open_browser).start()
    print("=" * 50)
    print("  Crassus 2.5 Dashboard")
    print(f"  http://localhost:{port}")
    print("=" * 50)
    app.run(host="127.0.0.1", port=port, debug=False)
