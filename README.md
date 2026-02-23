# Crassus 2.5

Azure Function that receives TradingView webhook alerts and places **bracket orders** (stocks) and **risk-sized options orders** on Alpaca. Includes a local dashboard GUI for credential setup, TradingView webhook configuration, portfolio monitoring, and strategy parameter tuning.

---

## Quick Start

```bash
git clone https://github.com/AutosortermkI/Crassus-2.5.git
cd Crassus-2.5
./run_dashboard.sh        # macOS / Linux
```

That's it. The script auto-creates a virtual environment, installs all dependencies, and launches the dashboard at `http://localhost:5050`. On first launch you'll see a **credential setup screen** — enter your [Alpaca](https://app.alpaca.markets) API key and secret to get started.

**Windows:** Use `run_dashboard.bat` instead.

### Other commands

| Command | What it does |
|---|---|
| `./setup.sh` | Full interactive setup (venv, deps, credential prompts, `.env` generation) |
| `./run_dashboard.sh` | Launch the dashboard GUI (auto-installs deps if needed) |
| `./run_crassus.sh` | Run the Azure Function locally (`http://localhost:7071/api/trade`) |
| `./run_tests.sh` | Run the test suite (123 unit tests) |
| `./deploy_azure.sh` | Deploy to Azure (creates resource group, storage, Function App, pushes code) |

All scripts have `.bat` equivalents for Windows.

---

## Dashboard

The web dashboard (`http://localhost:5050`) provides:

- **Credential setup gate** — blocks the UI until valid Alpaca API keys are entered and verified. Keys are saved to `.env` automatically.
- **Portfolio overview** — equity, buying power, cash, daily P&L (auto-refreshes every 30s).
- **Open positions** — live positions with unrealized P&L.
- **Recent orders** — last 20 orders with status, fill price, timestamps.
- **TradingView webhook widget** — copy-paste webhook URL, auth token, and alert message templates directly into TradingView. Includes a "Send Test Webhook" button.
- **Trading parameters** — edit all strategy percentages, risk limits, and screening criteria with live save to `.env`.

---

## TradingView Setup

The dashboard's webhook widget gives you everything you need, but here's the manual process:

1. **Get your webhook URL:**
   - Local: `http://localhost:7071/api/trade`
   - Azure: `https://crassus-25.azurewebsites.net/api/trade` (after deploying)

2. **In TradingView**, create or edit an alert on your indicator/strategy.

3. **Under Notifications**, enable **Webhook URL** and paste the URL.

4. **Add a custom header:** `X-Webhook-Token` with your auth token (visible in the dashboard or `.env`).

5. **Set the alert message** to one of these templates:

   **Stock buy signal:**
   ```
   **New Buy Signal:**
   {{ticker}} {{interval}} Candle
   Strategy: bollinger_mean_reversion
   Mode: stock
   Volume: {{volume}}
   Price: {{close}}
   Time: {{timenow}}
   ```

   **Options sell signal:**
   ```
   **New Sell Signal:**
   {{ticker}} {{interval}} Candle
   Strategy: lorentzian_classification
   Mode: options
   Volume: {{volume}}
   Price: {{close}}
   Time: {{timenow}}
   ```

   `{{ticker}}`, `{{close}}`, `{{volume}}`, `{{interval}}`, and `{{timenow}}` are TradingView placeholders that auto-fill when the alert fires.

### Webhook payload format

TradingView sends JSON with a `content` multi-line string:

```json
{
  "content": "**New Buy Signal:**\nAAPL 5 Min Candle\nStrategy: bollinger_mean_reversion\nMode: stock\nVolume: 2500000\nPrice: 189.50\nTime: 2024-06-15T14:30:00Z"
}
```

### Parsed fields

| Field | Required | Example |
|---|---|---|
| Side | Yes (from header line) | `"buy"` / `"sell"` |
| Ticker | Yes (first word after header) | `"AAPL"` |
| Strategy | Yes | `"bollinger_mean_reversion"` |
| Price | Yes | `189.50` |
| Mode | No (default `"stock"`) | `"stock"` / `"options"` |
| Volume | No | `2500000` |
| Time | No | `"2024-06-15T14:30:00Z"` |

---

## Architecture

```
function_app/
├── function_app.py          # HTTP trigger (POST /api/trade) + timer trigger (exit monitor)
├── parser.py                # Webhook content parsing (regex-based)
├── strategy.py              # Strategy config + TP/SL/stop-limit computation
├── stock_orders.py          # Alpaca stock bracket order submission
├── options_screener.py      # Options contract screening (Yahoo-first, Alpaca fallback)
├── options_orders.py        # Options limit entry order submission
├── exit_monitor.py          # Options exit monitoring (TP/SL target tracking + exit orders)
├── risk.py                  # Risk sizing (fixed dollar; % equity planned)
├── greeks.py                # Black-Scholes Greeks (Delta/Gamma/Theta/Vega) + IV solver
├── yahoo_client.py          # Yahoo Finance crumb/cookie client for market data
├── utils.py                 # Correlation ID, structured logging, rounding
├── host.json                # Azure Functions host config
├── requirements.txt         # Python dependencies
└── local.settings.json      # Local dev settings (gitignored)

dashboard/
├── app.py                   # Flask web UI (http://localhost:5050)
├── config_manager.py        # .env read/write with parameter metadata
├── alpaca_client.py         # Alpaca portfolio/positions/orders client
└── templates/
    └── index.html           # Single-page dashboard (dark theme)

tests/
├── conftest.py              # Adds function_app/ to sys.path
├── test_parser.py           # 26 tests: parsing, edge cases, example payloads
├── test_strategy.py         # 13 tests: bracket math, strategy lookup
├── test_risk.py             # 9 tests:  options qty sizing, edge cases
├── test_greeks.py           # 47 tests: BS pricing, Greeks, IV solver, edge cases
├── test_yahoo_client.py     # 26 tests: Yahoo client, option chains, error handling
└── test_live_alpaca.py      # Live Alpaca paper trading integration test

deploy_azure.sh / .bat       # One-command Azure deployment
setup.sh / .bat              # Interactive first-time setup
run_dashboard.sh / .bat      # Dashboard launcher (auto-installs deps)
run_crassus.sh / .bat        # Local Azure Function runner
run_tests.sh / .bat          # Test runner
```

---

## Request Flow

```
TradingView alert fires
        │
        ▼
  POST /api/trade
  Header: X-Webhook-Token
  Body: { "content": "..." }
        │
        ├─ 401  invalid / missing token
        │
        ▼
  Parse content string
        │
        ├─ 400  bad payload / missing fields
        │
        ▼
  Look up strategy config
        │
        ├─ 400  unknown strategy
        │
        ▼
  Route by mode
        │
        ├─ mode=stock ────► Stock bracket order (Alpaca BRACKET class)
        │                       TP / SL / stop-limit from strategy %
        │                       GTC time-in-force
        │
        └─ mode=options ──► Screen option contracts (Yahoo + Greeks)
                             ► Risk-size qty from MAX_DOLLAR_RISK
                             ► Submit limit entry order (DAY)
                             ► Register TP/SL targets with exit monitor
                                    │
                                    ▼
                             Timer trigger (every 60s)
                             ► Check open positions vs targets
                             ► Price >= TP → limit sell at TP
                             ► Price <= SL → market sell (fast exit)
```

---

## Supported Strategies

| Strategy | Stock TP % | Stock SL % | Options TP % (premium) | Options SL % (premium) |
|---|---|---|---|---|
| `bollinger_mean_reversion` | 0.2 % | 0.1 % | 20 % | 10 % |
| `lorentzian_classification` | 1.0 % | 0.8 % | 50 % | 40 % |

All percentages are configurable via environment variables or the dashboard UI.

---

## Options: Design Decisions

### Data source vs execution venue

| Concern | Source | Why |
|---|---|---|
| **Market data** (bid/ask/IV/volume/OI) | Yahoo Finance | Richer options data than Alpaca's trading API |
| **Greeks computation** | `greeks.py` (local) | Black-Scholes from Yahoo's IV or solved from market prices |
| **Contract screening** | `options_screener.py` | Uses Yahoo data + Greeks for delta-based selection |
| **Order execution** | Alpaca | Execution venue; contract symbols map directly from Yahoo OCC format |

### Why no bracket orders for options?

Alpaca does **not** support bracket orders (`BRACKET` / `OCO` / `OTO`) for options contracts — the API returns an error. Therefore:

- **Entry:** Simple limit order with `TimeInForce.DAY`.
- **Exit monitoring:** The `exit_monitor.py` module tracks TP/SL targets for every options entry. A **timer trigger** (`check_options_exits_timer`) runs every 60 seconds, checks current prices against targets, and submits exit orders automatically:
  - **Take profit hit** → limit sell at TP price
  - **Stop loss hit** → market sell (fast exit)
  - **Position closed externally** → auto-cleans up stale targets

Target persistence uses a local JSON file (`.options_targets.json`). For multi-instance Azure scaling, swap for Azure Table Storage or Cosmos DB.

### Risk sizing

```
qty = max_dollar_risk / (stop_distance × 100)
```

Where `stop_distance = (stop_loss_pct / 100) × premium_price` and `× 100` is the options multiplier.

### Contract selection

When Yahoo Finance is enabled (default), the screener:

1. **Fetches** option chains from Yahoo Finance (bid/ask/IV/volume/OI)
2. **Computes** Greeks via `greeks.py` using Yahoo's IV data (or solves for IV from market prices)
3. **Filters** candidates by:
   - **DTE window:** 14–45 days (configurable)
   - **Delta range:** 0.30–0.70 absolute delta (real Black-Scholes Greeks)
   - **Volume:** minimum daily volume
   - **Bid-ask spread:** max spread as % of mid price
   - **Open interest:** minimum OI threshold
   - **Price:** within configured min/max premium range
4. **Scores** using composite ranking: delta proximity (40%), OI (30%), spread tightness (20%), IV (10%)
5. **Maps** selected contract symbol to Alpaca for order submission

**Fallback:** If Yahoo is unavailable or disabled (`YAHOO_ENABLED=false`), the screener falls back to Alpaca-only data with IV solved from close prices.

### Greeks computation

`greeks.py` implements the Black-Scholes model for European-style options:

- **Pricing:** Call and put theoretical prices
- **Delta:** Rate of change of price w.r.t. underlying
- **Gamma:** Rate of change of delta w.r.t. underlying
- **Theta:** Time decay per calendar day
- **Vega:** Price sensitivity per 1% IV move
- **IV solver:** Brent's method (`scipy.optimize.brentq`) to solve for implied volatility from observed market prices

### Yahoo Finance integration

`yahoo_client.py` provides authenticated access to Yahoo Finance's options API:

- Cookie/crumb authentication with automatic refresh on 401
- Exponential backoff on rate limiting (429) and transient errors (5xx)
- Returns Crassus-compatible dataclasses (`YahooOptionContract`, `YahooOptionChain`)

---

## Azure Deployment

### One-command deploy

```bash
./deploy_azure.sh
```

This script:
1. Validates Azure CLI and Functions Core Tools are installed
2. Reads all credentials and settings from `.env` (no hardcoded secrets)
3. Creates or updates: resource group, storage account, Function App
4. Pushes all `.env` settings as Azure Application Settings
5. Deploys the function code with `func azure functionapp publish`
6. Prints the live webhook URL and auth token

**Prerequisites:**
- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) (`brew install azure-cli`)
- [Azure Functions Core Tools v4](https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local) (`brew tap azure/functions && brew install azure-functions-core-tools@4`)
- An active Azure subscription (`az login`)
- A configured `.env` file (run the dashboard or `./setup.sh` first)

### What gets deployed

| Azure Resource | Name | Purpose |
|---|---|---|
| Resource Group | `CRG` | Container for all resources |
| Storage Account | `crassusstorage25` | Required by Azure Functions |
| Function App | `crassus-25` | Hosts the trading function (Linux, Python 3.11, Consumption plan) |
| Application Insights | `crassus-25` | Logging and monitoring |

### Functions

| Function | Trigger | Schedule | Purpose |
|---|---|---|---|
| `trade` | HTTP POST | On-demand | Receives TradingView webhooks, places orders |
| `check_options_exits_timer` | Timer | Every 60 seconds | Monitors options positions for TP/SL exits |

### Customizing resource names

Edit the top of `deploy_azure.sh`:
```bash
RESOURCE_GROUP="CRG"
LOCATION="eastus"
STORAGE_ACCOUNT="crassusstorage25"
FUNCTION_APP_NAME="crassus-25"
```

---

## Environment Variables

All variables are configurable via the dashboard UI or directly in `.env`.

### Required

| Variable | Description |
|---|---|
| `ALPACA_API_KEY` | Alpaca API key |
| `ALPACA_SECRET_KEY` | Alpaca secret key |
| `WEBHOOK_AUTH_TOKEN` | Shared secret for `X-Webhook-Token` header |

### Optional (with defaults)

| Variable | Default | Description |
|---|---|---|
| `ALPACA_PAPER` | `true` | `true` = paper trading, `false` = live |
| `DEFAULT_STOCK_QTY` | `1` | Shares per stock trade |

### Strategy: bollinger_mean_reversion (prefix `BMR_`)

| Variable | Default | Description |
|---|---|---|
| `BMR_STOCK_TP_PCT` | `0.2` | Stock take-profit % |
| `BMR_STOCK_SL_PCT` | `0.1` | Stock stop-loss % |
| `BMR_STOCK_STOP_LIMIT_PCT` | `0.15` | Stock stop-limit % |
| `BMR_OPTIONS_TP_PCT` | `20.0` | Options TP as % of premium |
| `BMR_OPTIONS_SL_PCT` | `10.0` | Options SL as % of premium |

### Strategy: lorentzian_classification (prefix `LC_`)

| Variable | Default | Description |
|---|---|---|
| `LC_STOCK_TP_PCT` | `1.0` | Stock take-profit % |
| `LC_STOCK_SL_PCT` | `0.8` | Stock stop-loss % |
| `LC_STOCK_STOP_LIMIT_PCT` | `0.9` | Stock stop-limit % |
| `LC_OPTIONS_TP_PCT` | `50.0` | Options TP as % of premium |
| `LC_OPTIONS_SL_PCT` | `40.0` | Options SL as % of premium |

### Options screening

| Variable | Default | Description |
|---|---|---|
| `OPTIONS_DTE_MIN` | `14` | Min days to expiration |
| `OPTIONS_DTE_MAX` | `45` | Max days to expiration |
| `OPTIONS_DELTA_MIN` | `0.30` | Min absolute delta (real Greeks via `greeks.py`) |
| `OPTIONS_DELTA_MAX` | `0.70` | Max absolute delta (real Greeks via `greeks.py`) |
| `OPTIONS_MIN_OI` | `100` | Min open interest |
| `OPTIONS_MIN_VOLUME` | `10` | Min daily volume |
| `OPTIONS_MAX_SPREAD_PCT` | `5.0` | Max bid-ask spread as % of mid |
| `OPTIONS_MIN_PRICE` | `0.50` | Min option premium ($) |
| `OPTIONS_MAX_PRICE` | `50.0` | Max option premium ($) |

### Greeks / risk / data

| Variable | Default | Description |
|---|---|---|
| `RISK_FREE_RATE` | `0.05` | Annualized risk-free rate for Black-Scholes (5%) |
| `MAX_DOLLAR_RISK` | `50.0` | Max $ risk per options trade |
| `YAHOO_ENABLED` | `true` | Toggle Yahoo Finance as market data source |
| `YAHOO_RETRY_COUNT` | `5` | Max retries for Yahoo API requests |
| `YAHOO_BACKOFF_BASE` | `2` | Exponential backoff base (seconds) |

---

## HTTP Responses

| Code | Meaning |
|---|---|
| **200** | Order placed successfully (JSON with order details + `correlation_id`) |
| **400** | Bad payload, missing fields, unknown strategy, or no suitable contract found |
| **401** | Missing or invalid `X-Webhook-Token` header |
| **502** | Alpaca API error (upstream failure) |
| **500** | Internal / unexpected error |

All responses include a `correlation_id` for log tracing in Application Insights.

---

## Example curl

```bash
# Stock buy signal (bollinger_mean_reversion)
curl -X POST http://localhost:7071/api/trade \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your-secret-token" \
  -d '{
    "content": "**New Buy Signal:**\nAAPL 5 Min Candle\nStrategy: bollinger_mean_reversion\nMode: stock\nVolume: 2500000\nPrice: 189.50\nTime: 2024-06-15T14:30:00Z"
  }'

# Options sell signal (lorentzian_classification)
curl -X POST http://localhost:7071/api/trade \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Token: your-secret-token" \
  -d '{
    "content": "**New Sell Signal:**\nQQQ 5 Min Candle\nStrategy: lorentzian_classification\nMode: options\nPrice: 460.75"
  }'
```

---

## Dependencies

### Function app (`function_app/requirements.txt`)

| Package | Purpose |
|---|---|
| `azure-functions` | Azure Functions runtime |
| `alpaca-py` | Alpaca Trading API (stocks + options execution) |
| `scipy` | `norm.cdf`/`norm.pdf` (Greeks), `brentq` (IV solver) |
| `numpy` | Numerical computation |
| `requests` | Yahoo Finance HTTP client (direct API, not `yfinance`) |

### Dashboard (`requirements-dashboard.txt`)

| Package | Purpose |
|---|---|
| `flask` | Web framework for the dashboard UI |
| `python-dotenv` | `.env` file loading |
| `alpaca-py` | Portfolio and account data |
| `requests` | Webhook test functionality |

> **Note:** Do NOT add `yfinance`. The direct API approach via `requests` + `YahooCrumbClient` is more reliable and avoids the heavy `yfinance` dependency tree.

---

## Manual Setup

If you prefer to set things up manually instead of using the scripts:

1. **Clone and install**
   ```bash
   git clone https://github.com/AutosortermkI/Crassus-2.5.git
   cd Crassus-2.5
   python3 -m venv .venv
   source .venv/bin/activate          # Linux/Mac
   # .venv\Scripts\activate           # Windows
   pip install -r function_app/requirements.txt
   pip install -r requirements-dashboard.txt
   ```

2. **Configure credentials**
   - Copy `.env.example` to `.env` and fill in your Alpaca API keys and webhook token.
   - Copy `function_app/local.settings.json.example` to `function_app/local.settings.json` and set the `Values` section.

3. **Run the dashboard**
   ```bash
   python dashboard/app.py
   ```
   Opens at `http://localhost:5050`. Enter credentials in the setup screen if `.env` is not configured.

4. **Run the trading function locally**
   ```bash
   cd function_app
   func start
   ```
   Endpoint: `http://localhost:7071/api/trade` (POST)

5. **Run tests**
   ```bash
   pip install pytest
   python -m pytest tests/ -v
   ```

6. **Deploy to Azure**
   ```bash
   ./deploy_azure.sh
   ```
   Or manually: `cd function_app && func azure functionapp publish crassus-25 --python`

---

## License

Use and modify as needed for your own trading setup.
