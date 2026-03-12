"""
Crassus 2.5 -- Dashboard configuration manager.

Reads and writes the root .env file with full parameter metadata
for the dashboard UI.
"""

import os
import shutil
import subprocess
import logging
import secrets
from pathlib import Path
from collections import OrderedDict
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from azure.identity import DefaultAzureCredential
    from azure.mgmt.web import WebSiteManagementClient
    _AZURE_MANAGEMENT_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on optional dashboard deps
    DefaultAzureCredential = None
    WebSiteManagementClient = None
    _AZURE_MANAGEMENT_AVAILABLE = False

# Path to the .env file (repo root)
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

AZURE_DEFAULTS = {
    "AZURE_FUNCTION_APP_NAME": "crassus-25",
    "AZURE_FUNCTION_BASE_URL": "",
    "AZURE_SUBSCRIPTION_ID": "",
    "AZURE_RESOURCE_GROUP": "CRG",
    "AZURE_LOCATION": "eastus",
    "AZURE_STORAGE_ACCOUNT": "crassusstorage25",
    "AZURE_DASHBOARD_APP_NAME": "",
    "AZURE_DASHBOARD_PLAN_NAME": "",
    "AZURE_DASHBOARD_SKU": "B1",
}

# ---------------------------------------------------------------------------
# Parameter definitions with metadata for the dashboard UI
# ---------------------------------------------------------------------------

PARAM_DEFINITIONS = OrderedDict([
    # --- General Settings ---
    ("ALPACA_PAPER", {
        "label": "Paper Trading Mode",
        "group": "General Settings",
        "type": "bool",
        "default": "true",
        "description": "true = paper trading (no real money), false = live trading",
    }),
    ("DEFAULT_STOCK_QTY", {
        "label": "Default Stock Quantity",
        "group": "General Settings",
        "type": "int",
        "default": "1",
        "description": "Number of shares per stock trade",
    }),
    ("WEBHOOK_FORWARD_TARGET", {
        "label": "Webhook Forward Target",
        "group": "Webhook Routing",
        "type": "text",
        "default": "azure",
        "description": "Primary webhook destination: azure, local, custom, or none",
    }),
    ("WEBHOOK_FORWARD_URL", {
        "label": "Custom Forward URL",
        "group": "Webhook Routing",
        "type": "text",
        "default": "",
        "description": "Optional custom destination when Webhook Forward Target is custom",
    }),
    ("WEBHOOK_ACTIVE_MINUTES", {
        "label": "Active Window (min)",
        "group": "Webhook Routing",
        "type": "int",
        "default": "60",
        "description": "How long a webhook stays in the Active Webhooks snapshot",
    }),
    ("WEBHOOK_MAX_SNAPSHOTS", {
        "label": "Stored Snapshots",
        "group": "Webhook Routing",
        "type": "int",
        "default": "50",
        "description": "Maximum number of webhook snapshots to retain on disk",
    }),
    ("AZURE_FUNCTION_APP_NAME", {
        "label": "Function App Name",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_FUNCTION_APP_NAME"],
        "description": "Azure Function App name used for deployment and settings sync",
    }),
    ("AZURE_FUNCTION_BASE_URL", {
        "label": "Function Base URL",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_FUNCTION_BASE_URL"],
        "description": "Optional override for the deployed Function base URL",
    }),
    ("AZURE_RESOURCE_GROUP", {
        "label": "Resource Group",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_RESOURCE_GROUP"],
        "description": "Azure resource group that hosts the shared platform",
    }),
    ("AZURE_SUBSCRIPTION_ID", {
        "label": "Subscription ID",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_SUBSCRIPTION_ID"],
        "description": "Azure subscription ID used by the hosted dashboard to sync app settings",
    }),
    ("AZURE_LOCATION", {
        "label": "Azure Region",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_LOCATION"],
        "description": "Azure region used by deployment scripts",
    }),
    ("AZURE_STORAGE_ACCOUNT", {
        "label": "Storage Account",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_STORAGE_ACCOUNT"],
        "description": "Storage account name used by the Function App deployment",
    }),
    ("AZURE_DASHBOARD_APP_NAME", {
        "label": "Dashboard App Name",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_DASHBOARD_APP_NAME"],
        "description": "Optional Azure Web App name for a hosted dashboard",
    }),
    ("AZURE_DASHBOARD_PLAN_NAME", {
        "label": "Dashboard Plan Name",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_DASHBOARD_PLAN_NAME"],
        "description": "Optional App Service plan name for the hosted dashboard",
    }),
    ("AZURE_DASHBOARD_SKU", {
        "label": "Dashboard SKU",
        "group": "Azure Deployment",
        "type": "text",
        "default": AZURE_DEFAULTS["AZURE_DASHBOARD_SKU"],
        "description": "App Service SKU used when deploying the hosted dashboard",
    }),

    # --- Bollinger Mean Reversion ---
    ("BMR_STOCK_TP_PCT", {
        "label": "Stock Take-Profit %",
        "group": "Strategy: Bollinger Mean Reversion",
        "type": "float",
        "default": "0.2",
        "description": "Take-profit as % of entry price",
    }),
    ("BMR_STOCK_SL_PCT", {
        "label": "Stock Stop-Loss %",
        "group": "Strategy: Bollinger Mean Reversion",
        "type": "float",
        "default": "0.1",
        "description": "Stop-loss as % of entry price",
    }),
    ("BMR_STOCK_STOP_LIMIT_PCT", {
        "label": "Stock Stop-Limit %",
        "group": "Strategy: Bollinger Mean Reversion",
        "type": "float",
        "default": "0.15",
        "description": "Stop-limit as % of entry price",
    }),
    ("BMR_OPTIONS_TP_PCT", {
        "label": "Options Take-Profit %",
        "group": "Strategy: Bollinger Mean Reversion",
        "type": "float",
        "default": "20.0",
        "description": "Take-profit as % of premium paid",
    }),
    ("BMR_OPTIONS_SL_PCT", {
        "label": "Options Stop-Loss %",
        "group": "Strategy: Bollinger Mean Reversion",
        "type": "float",
        "default": "10.0",
        "description": "Stop-loss as % of premium paid",
    }),

    # --- Lorentzian Classification ---
    ("LC_STOCK_TP_PCT", {
        "label": "Stock Take-Profit %",
        "group": "Strategy: Lorentzian Classification",
        "type": "float",
        "default": "1.0",
        "description": "Take-profit as % of entry price",
    }),
    ("LC_STOCK_SL_PCT", {
        "label": "Stock Stop-Loss %",
        "group": "Strategy: Lorentzian Classification",
        "type": "float",
        "default": "0.8",
        "description": "Stop-loss as % of entry price",
    }),
    ("LC_STOCK_STOP_LIMIT_PCT", {
        "label": "Stock Stop-Limit %",
        "group": "Strategy: Lorentzian Classification",
        "type": "float",
        "default": "0.9",
        "description": "Stop-limit as % of entry price",
    }),
    ("LC_OPTIONS_TP_PCT", {
        "label": "Options Take-Profit %",
        "group": "Strategy: Lorentzian Classification",
        "type": "float",
        "default": "50.0",
        "description": "Take-profit as % of premium paid",
    }),
    ("LC_OPTIONS_SL_PCT", {
        "label": "Options Stop-Loss %",
        "group": "Strategy: Lorentzian Classification",
        "type": "float",
        "default": "40.0",
        "description": "Stop-loss as % of premium paid",
    }),

    # --- Options Screening ---
    ("OPTIONS_DTE_MIN", {
        "label": "Min Days to Expiration",
        "group": "Options Screening",
        "type": "int",
        "default": "14",
        "description": "Minimum DTE for contract screening",
    }),
    ("OPTIONS_DTE_MAX", {
        "label": "Max Days to Expiration",
        "group": "Options Screening",
        "type": "int",
        "default": "45",
        "description": "Maximum DTE for contract screening",
    }),
    ("OPTIONS_DELTA_MIN", {
        "label": "Min Absolute Delta",
        "group": "Options Screening",
        "type": "float",
        "default": "0.30",
        "description": "Minimum delta (0.0 - 1.0) for filtering",
    }),
    ("OPTIONS_DELTA_MAX", {
        "label": "Max Absolute Delta",
        "group": "Options Screening",
        "type": "float",
        "default": "0.70",
        "description": "Maximum delta (0.0 - 1.0) for filtering",
    }),
    ("OPTIONS_MIN_OI", {
        "label": "Min Open Interest",
        "group": "Options Screening",
        "type": "int",
        "default": "100",
        "description": "Minimum open interest threshold",
    }),
    ("OPTIONS_MIN_VOLUME", {
        "label": "Min Daily Volume",
        "group": "Options Screening",
        "type": "int",
        "default": "10",
        "description": "Minimum daily trading volume",
    }),
    ("OPTIONS_MAX_SPREAD_PCT", {
        "label": "Max Bid-Ask Spread %",
        "group": "Options Screening",
        "type": "float",
        "default": "5.0",
        "description": "Maximum spread as % of mid price",
    }),
    ("OPTIONS_MIN_PRICE", {
        "label": "Min Premium ($)",
        "group": "Options Screening",
        "type": "float",
        "default": "0.50",
        "description": "Minimum option premium in dollars",
    }),
    ("OPTIONS_MAX_PRICE", {
        "label": "Max Premium ($)",
        "group": "Options Screening",
        "type": "float",
        "default": "50.0",
        "description": "Maximum option premium in dollars",
    }),

    # --- Risk & Data ---
    ("MAX_DOLLAR_RISK", {
        "label": "Max Dollar Risk ($)",
        "group": "Risk & Data",
        "type": "float",
        "default": "50.0",
        "description": "Maximum dollar risk per options trade",
    }),
    ("RISK_FREE_RATE", {
        "label": "Risk-Free Rate",
        "group": "Risk & Data",
        "type": "float",
        "default": "0.05",
        "description": "Annualized rate for Black-Scholes (e.g. 0.05 = 5%)",
    }),
    ("YAHOO_ENABLED", {
        "label": "Yahoo Finance Enabled",
        "group": "Risk & Data",
        "type": "bool",
        "default": "true",
        "description": "Use Yahoo Finance for richer options market data",
    }),
    ("YAHOO_RETRY_COUNT", {
        "label": "Yahoo Retry Count",
        "group": "Risk & Data",
        "type": "int",
        "default": "5",
        "description": "Max retries for Yahoo Finance API requests",
    }),
    ("YAHOO_BACKOFF_BASE", {
        "label": "Yahoo Backoff Base (s)",
        "group": "Risk & Data",
        "type": "int",
        "default": "2",
        "description": "Exponential backoff base in seconds",
    }),
])

# Keys that are secrets and should not be exposed in the config editor
SECRET_KEYS = {
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
    "WEBHOOK_AUTH_TOKEN",
    "DASHBOARD_ACCESS_PASSWORD",
    "DASHBOARD_ACCESS_PASSWORD_HASH",
    "DASHBOARD_SESSION_SECRET",
}


def _can_persist_local_env() -> bool:
    """Return True when the dashboard can safely write the local .env file."""
    target = ENV_PATH if ENV_PATH.exists() else ENV_PATH.parent
    return os.access(target, os.W_OK)


def ensure_env_file() -> None:
    """Create a default .env file when the dashboard is used before setup."""
    if ENV_PATH.exists():
        return
    if not _can_persist_local_env():
        return

    lines = [
        "# Crassus 2.5 -- Environment Configuration\n",
        "# Generated by dashboard bootstrap\n",
        "\n",
    ]
    for key, meta in PARAM_DEFINITIONS.items():
        lines.append(f"{key}={meta['default']}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(lines)


def read_env() -> dict:
    """Read config from .env and process environment variables.

    Local development prefers the repo .env file so dashboard edits take
    effect immediately. Hosted Azure deployments prefer App Settings so the
    shared dashboard follows the live platform configuration.
    """
    values = {}
    prefer_process_env = bool(os.environ.get("WEBSITE_SITE_NAME"))

    if prefer_process_env:
        known_keys = set(PARAM_DEFINITIONS) | SECRET_KEYS | set(AZURE_DEFAULTS)
        for key in known_keys:
            value = os.environ.get(key)
            if value is not None:
                values[key] = value.strip()

    if ENV_PATH.exists():
        with open(ENV_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if prefer_process_env and key in values:
                    continue
                values[key] = value.strip()

    known_keys = set(PARAM_DEFINITIONS) | SECRET_KEYS | set(AZURE_DEFAULTS)
    for key in known_keys:
        if key in values:
            continue
        value = os.environ.get(key)
        if value is not None:
            values[key] = value.strip()
    return values


def get_config() -> dict:
    """Return config values merged with parameter metadata.

    Returns a dict of {key: {label, group, type, default, description, value}}
    for all non-secret parameters defined in PARAM_DEFINITIONS.
    """
    env_values = read_env()
    config = OrderedDict()
    for key, meta in PARAM_DEFINITIONS.items():
        config[key] = {
            **meta,
            "value": env_values.get(key, meta["default"]),
        }
    return config


def save_credentials(api_key: str, secret_key: str, webhook_token: str = "",
                     paper: bool = True) -> None:
    """Write Alpaca credentials to .env, creating the file if needed.

    Preserves existing non-credential settings. If the file doesn't exist,
    generates a full default .env.
    """
    cred_map = {
        "ALPACA_API_KEY": api_key,
        "ALPACA_SECRET_KEY": secret_key,
        "ALPACA_PAPER": "true" if paper else "false",
    }
    if webhook_token:
        cred_map["WEBHOOK_AUTH_TOKEN"] = webhook_token

    if not _can_persist_local_env():
        return

    if not ENV_PATH.exists():
        # Generate a fresh .env with credentials + all defaults
        lines = [
            "# Crassus 2.5 -- Environment Configuration\n",
            "# Generated by dashboard setup\n",
            "\n",
        ]
        # Credentials first
        for k, v in cred_map.items():
            lines.append(f"{k}={v}\n")
        lines.append("\n")
        # Then all parameter defaults
        for key, meta in PARAM_DEFINITIONS.items():
            if key not in cred_map:
                lines.append(f"{key}={meta['default']}\n")
        with open(ENV_PATH, "w") as f:
            f.writelines(lines)
    else:
        # Update existing file in-place
        save_config(cred_map, allow_secret_keys=True)


def save_config(updates: dict, allow_secret_keys: bool = False) -> None:
    """Update .env file with new values, preserving comments and structure.

    Args:
        updates: dict of {key: new_value} to write.
    """
    if not _can_persist_local_env():
        return
    ensure_env_file()
    # Read existing lines
    lines = []
    if ENV_PATH.exists():
        with open(ENV_PATH, "r") as f:
            lines = f.readlines()

    # Track which keys we've updated
    updated_keys = set()

    new_lines = []
    for line in lines:
        stripped = line.strip()
        # Preserve comments and blank lines
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        if "=" not in stripped:
            new_lines.append(line)
            continue

        key, _, old_value = stripped.partition("=")
        key = key.strip()

        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
            updated_keys.add(key)
        else:
            new_lines.append(line)

    # Append any new keys that weren't in the file
    for key, value in updates.items():
        if key not in updated_keys and (allow_secret_keys or key not in SECRET_KEYS):
            new_lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)


def ensure_webhook_token() -> str:
    """Return a webhook token, creating one on first launch if needed."""
    env = read_env()
    token = (env.get("WEBHOOK_AUTH_TOKEN") or "").strip()
    if token:
        return token

    token = secrets.token_hex(16)
    if _can_persist_local_env():
        save_config({"WEBHOOK_AUTH_TOKEN": token}, allow_secret_keys=True)
    return token


def ensure_dashboard_session_secret() -> str:
    """Return a stable Flask session secret for dashboard logins."""
    env = read_env()
    secret = (env.get("DASHBOARD_SESSION_SECRET") or "").strip()
    if secret:
        return secret

    secret = secrets.token_urlsafe(32)
    if _can_persist_local_env():
        save_config({"DASHBOARD_SESSION_SECRET": secret}, allow_secret_keys=True)
    return secret


def get_azure_settings(overrides: Optional[dict] = None) -> dict:
    """Resolve Azure resource names and URLs from config plus optional updates."""
    env = read_env()
    if overrides:
        env.update({key: str(value) for key, value in overrides.items()})

    function_app_name = (
        env.get("AZURE_FUNCTION_APP_NAME") or AZURE_DEFAULTS["AZURE_FUNCTION_APP_NAME"]
    ).strip()
    function_base_url = (env.get("AZURE_FUNCTION_BASE_URL") or "").strip()
    if not function_base_url:
        function_base_url = f"https://{function_app_name}.azurewebsites.net"

    return {
        "function_app_name": function_app_name,
        "function_base_url": function_base_url.rstrip("/"),
        "subscription_id": (env.get("AZURE_SUBSCRIPTION_ID") or "").strip(),
        "resource_group": (
            env.get("AZURE_RESOURCE_GROUP") or AZURE_DEFAULTS["AZURE_RESOURCE_GROUP"]
        ).strip(),
        "location": (env.get("AZURE_LOCATION") or AZURE_DEFAULTS["AZURE_LOCATION"]).strip(),
        "storage_account": (
            env.get("AZURE_STORAGE_ACCOUNT") or AZURE_DEFAULTS["AZURE_STORAGE_ACCOUNT"]
        ).strip(),
        "dashboard_app_name": (env.get("AZURE_DASHBOARD_APP_NAME") or "").strip(),
        "dashboard_plan_name": (env.get("AZURE_DASHBOARD_PLAN_NAME") or "").strip(),
        "dashboard_sku": (
            env.get("AZURE_DASHBOARD_SKU") or AZURE_DEFAULTS["AZURE_DASHBOARD_SKU"]
        ).strip(),
    }


def get_azure_function_trade_url(env: Optional[dict] = None) -> str:
    """Return the configured Azure trade endpoint URL."""
    settings = get_azure_settings(env)
    base_url = settings["function_base_url"]
    if base_url.endswith("/api/trade"):
        return base_url
    return f"{base_url}/api/trade"


def get_azure_function_activity_url(env: Optional[dict] = None) -> str:
    """Return the configured Azure activity endpoint URL."""
    trade_url = get_azure_function_trade_url(env)
    if trade_url.endswith("/api/trade"):
        return trade_url[:-6] + "/webhook-activity"
    return ""


# ---------------------------------------------------------------------------
# Azure Function App settings sync
# ---------------------------------------------------------------------------

def azure_cli_available() -> bool:
    """Return True if the ``az`` CLI is installed."""
    return shutil.which("az") is not None


def _resolve_subscription_id(settings: dict) -> str:
    """Resolve the Azure subscription ID from config or the active Azure CLI account."""
    subscription_id = (settings.get("subscription_id") or "").strip()
    if subscription_id:
        return subscription_id
    if not azure_cli_available():
        return ""
    try:
        result = subprocess.run(
            ["az", "account", "show", "--query", "id", "-o", "tsv"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        return ""
    return ""


def _run_azure_settings_command(cmd: List[str], target_name: str) -> dict:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"az exited with code {result.returncode}"
            logger.warning("Azure sync failed for %s: %s", target_name, error_msg)
            return {"ok": False, "error": error_msg}
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Azure CLI command timed out for {target_name}."}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _sync_app_settings_with_management_api(
    client,
    resource_group: str,
    app_name: str,
    updates: dict,
) -> dict:
    """Update one App Service's application settings via Azure management APIs."""
    try:
        current = client.web_apps.list_application_settings(resource_group, app_name)
        properties = dict(getattr(current, "properties", {}) or {})
        properties.update({k: str(v) for k, v in updates.items()})
        client.web_apps.update_application_settings(
            resource_group,
            app_name,
            {"properties": properties},
        )
        return {"ok": True}
    except Exception as e:
        logger.warning("Azure management sync failed for %s: %s", app_name, e)
        return {"ok": False, "error": str(e)}


def _sync_settings_with_management_api(settings: dict, updates: dict) -> dict:
    """Use DefaultAzureCredential + Azure management APIs to sync settings."""
    if not _AZURE_MANAGEMENT_AVAILABLE:
        return {"ok": False, "error": "Azure management SDK is not installed."}

    subscription_id = _resolve_subscription_id(settings)
    if not subscription_id:
        return {"ok": False, "error": "AZURE_SUBSCRIPTION_ID is not configured and no Azure CLI account is active."}

    credential = None
    client = None
    failures = []
    try:
        credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        client = WebSiteManagementClient(credential, subscription_id)

        function_result = _sync_app_settings_with_management_api(
            client,
            settings["resource_group"],
            settings["function_app_name"],
            updates,
        )
        if not function_result["ok"]:
            failures.append(function_result["error"])

        if settings["dashboard_app_name"]:
            dashboard_result = _sync_app_settings_with_management_api(
                client,
                settings["resource_group"],
                settings["dashboard_app_name"],
                updates,
            )
            if not dashboard_result["ok"]:
                failures.append(dashboard_result["error"])
    finally:
        if client is not None and hasattr(client, "close"):
            client.close()
        if credential is not None and hasattr(credential, "close"):
            credential.close()

    if failures:
        return {"ok": False, "error": "; ".join(failures)}
    return {"ok": True}


def _sync_settings_with_cli(settings: dict, updates: dict) -> dict:
    """Use Azure CLI to sync app settings when running locally."""
    if not azure_cli_available():
        return {"ok": False, "error": "Azure CLI (az) is not installed."}

    settings_args = [f"{k}={v}" for k, v in updates.items()]
    failures = []

    function_cmd = [
        "az", "functionapp", "config", "appsettings", "set",
        "--name", settings["function_app_name"],
        "--resource-group", settings["resource_group"],
        "--settings",
    ] + settings_args + ["--output", "none"]
    function_result = _run_azure_settings_command(
        function_cmd,
        f"Function App {settings['function_app_name']}",
    )
    if not function_result["ok"]:
        failures.append(function_result["error"])

    if settings["dashboard_app_name"]:
        dashboard_cmd = [
            "az", "webapp", "config", "appsettings", "set",
            "--name", settings["dashboard_app_name"],
            "--resource-group", settings["resource_group"],
            "--settings",
        ] + settings_args + ["--output", "none"]
        dashboard_result = _run_azure_settings_command(
            dashboard_cmd,
            f"Dashboard App {settings['dashboard_app_name']}",
        )
        if not dashboard_result["ok"]:
            failures.append(dashboard_result["error"])

    if failures:
        return {"ok": False, "error": "; ".join(failures)}
    return {"ok": True}


def sync_settings_to_azure(updates: dict) -> dict:
    """Push config key=value pairs to the Azure Function App.

    Uses ``az functionapp config appsettings set`` for the trade backend and,
    when configured, ``az webapp config appsettings set`` for the hosted
    dashboard so shared Azure settings stay aligned.

    Returns ``{"ok": True}`` on success or ``{"ok": False, "error": ...}``
    on failure.
    """
    if not updates:
        return {"ok": True}

    azure_settings = get_azure_settings(overrides=updates)
    normalized_updates = {k: str(v) for k, v in updates.items()}

    management_result = _sync_settings_with_management_api(azure_settings, normalized_updates)
    if management_result["ok"]:
        return management_result

    cli_result = _sync_settings_with_cli(azure_settings, normalized_updates)
    if cli_result["ok"]:
        return cli_result

    return {
        "ok": False,
        "error": f"{management_result['error']}; {cli_result['error']}",
    }
