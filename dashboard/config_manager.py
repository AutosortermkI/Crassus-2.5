"""
Crassus 2.5 -- Dashboard configuration manager.

Reads and writes the root .env file with full parameter metadata
for the dashboard UI.
"""

import os
from pathlib import Path
from collections import OrderedDict

# Path to the .env file (repo root)
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

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
SECRET_KEYS = {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "WEBHOOK_AUTH_TOKEN"}


def read_env() -> dict:
    """Read the .env file and return a dict of all key=value pairs."""
    values = {}
    if not ENV_PATH.exists():
        return values
    with open(ENV_PATH, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
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
        save_config(cred_map)


def save_config(updates: dict) -> None:
    """Update .env file with new values, preserving comments and structure.

    Args:
        updates: dict of {key: new_value} to write.
    """
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
        if key not in updated_keys and key not in SECRET_KEYS:
            new_lines.append(f"{key}={value}\n")

    with open(ENV_PATH, "w") as f:
        f.writelines(new_lines)
