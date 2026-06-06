# Crassus 2.5

Azure Function app that receives TradingView webhook alerts through split stock/options routes. Stock/share routing defaults to **Alpaca**, options routing defaults to **Tastytrade** with Tastytrade options disabled until contract-symbol routing is verified. Includes a local/hosted dashboard GUI for broker routing, Tastytrade credential setup, TradingView webhook configuration, portfolio monitoring, and strategy parameter tuning. Ships with a built-in **backtesting engine** for replaying historical data through the same strategy logic used in live trading.

---

## Quick Start

```bash
git clone https://github.com/AutosortermkI/Crassus-2.5.git
cd Crassus-2.5
./run_dashboard.sh        # macOS / Linux
```

That's it. The script auto-creates a virtual environment, installs the dashboard dependencies, and launches the UI at `http://localhost:5050`. On first launch you can wire in your Tastytrade account number, OAuth client secret, and refresh token, review the split TradingView webhook URLs, and confirm webhook activity is flowing through the shared Azure function apps.

**Windows:** Use `run_dashboard.bat` instead.

### Other commands

| Command | What it does |
|---|---|
| `./setup.sh` | Full interactive setup (venv, deps, prompts for Tastytrade/webhook/dashboard access, `.env` generation) |
| `./run_dashboard.sh` | Launch the dashboard GUI (auto-installs deps if needed) |
| `./run_crassus.sh` | Run the Azure Function locally (`/api/trade-stock`, `/api/trade-options`, and legacy `/api/trade`) |
| `./run_tests.sh` | Run the automated test suite (manual live broker check excluded) |
| `./deploy_azure.sh --env dev` | Deploy the current branch to shared dev |
| `./deploy_azure.sh --env prod` | Deploy `main` to production after manual confirmation |

Most scripts have `.bat` equivalents for Windows. Split dev/prod deployment is implemented in `deploy_azure.sh`; `deploy_azure.bat --env ...` exits clearly until parity is added.

See [Development Workflow](docs/development_workflow.md) for the Jeremy/Joe branch model, shared dev coordination, PR promotion flow, and production deploy rules.

---

## Dev / Prod Deployment Model

- `main` is the last known good branch and the only production deploy source.
- `jeremy/*` and `joe/*` branches may deploy to shared dev and must PR into `main`.
- Shared dev uses separate stock, options, and dashboard Azure apps.
- Production uses separate stock, options, and dashboard Azure apps.
- DEV deployments overwrite the shared dev environment. Coordinate before deploying.

```bash
./deploy_azure.sh --env dev
./deploy_azure.sh --env prod
```

The deploy script records `DEPLOYED_GIT_BRANCH`, `DEPLOYED_GIT_SHA`, and `DEPLOYED_AT_UTC` so the dashboard can show what branch is running.

---

## Dashboard

The web dashboard (`http://localhost:5050`) provides:

- **Optional shared access password** — protect the dashboard itself when you are hosting it for partners.
- **TradingView inbox** — copy the shared webhook URL, copy the tokenized URL, generate a new webhook token, and send a test alert.
- **Latest webhook snapshot** — inspect the raw payload, parsed fields, and forward/execution result for the most recent TradingView alert.
- **Active Webhooks sidebar** — see grouped, recently active alert signatures so partners can confirm what is currently firing.
- **Recent Alerts table** — audit the latest webhook traffic without digging through Azure logs.
- **Broker panel** — add Tastytrade credentials for stock OTOCO execution plus portfolio, positions, and order history under the webhook monitor.
- **Trading parameters and Azure metadata** — edit strategy settings, webhook retention, and shared Azure naming from the same page.

---

## TradingView Setup

The dashboard's webhook widget gives you everything you need, but here's the manual process:

1. **Get your webhook URL:**
   - Stock local: `http://localhost:7071/api/trade-stock`
   - Options local: `http://localhost:7071/api/trade-options`
   - Azure: use the stock/options URLs shown by the dashboard, derived from the active dev or prod Azure app settings.

2. **In TradingView**, create or edit an alert on your indicator/strategy.

3. **Under Notifications**, enable **Webhook URL** and paste the URL.

4. **Authenticate the webhook** — choose one method:
   - **Option A (query parameter):** Append `?token=YOUR_TOKEN` to the webhook URL, e.g.
     `https://your-function.azurewebsites.net/api/trade-stock?token=your-secret-token`
   - **Option B (custom header):** If your TradingView plan supports custom headers, add `X-Webhook-Token: YOUR_TOKEN` as a header.

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
├── function_app.py          # HTTP triggers (/api/trade-stock, /api/trade-options, legacy /api/trade) + timer trigger
├── parser.py                # Webhook content parsing (regex-based)
├── strategy.py              # Strategy config + TP/SL/stop-limit computation
├── stock_orders.py          # Alpaca fallback stock bracket order submission
├── tastytrade_orders.py     # Tastytrade OAuth, account checks, and stock OTOCO submission
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
├── alpaca_client.py         # Alpaca fallback portfolio/positions/orders client
├── tastytrade_client.py     # Tastytrade portfolio/positions/orders client
└── templates/
    └── index.html           # Single-page dashboard (dark theme)

dashboard_wsgi.py            # Gunicorn/App Service entrypoint for hosted dashboard

backtesting/
├── __init__.py              # Package init with convenience imports
├── models.py                # Bar, Signal, Order, Position, Trade, Config, Result
├── data.py                  # CSV loading for OHLCV bars and trade signals
├── broker.py                # Simulated order execution, bracket lifecycle, cash accounting
├── engine.py                # Bar-by-bar replay engine with strategy integration
├── metrics.py               # Sharpe, Sortino, Calmar, drawdown, win rate, profit factor
├── report.py                # Human-readable text report generation
└── yahoo_fetch.py           # Yahoo Finance historical OHLCV data fetcher

tests/
├── conftest.py              # Adds function_app/ and project root to sys.path
├── test_parser.py           # 26 tests: parsing, edge cases, example payloads
├── test_strategy.py         # 13 tests: bracket math, strategy lookup
├── test_risk.py             # 9 tests:  options qty sizing, edge cases
├── test_greeks.py           # 47 tests: BS pricing, Greeks, IV solver, edge cases
├── test_yahoo_client.py     # 26 tests: Yahoo client, option chains, error handling
├── test_live_alpaca.py      # Live Alpaca paper trading integration test
├── test_backtest_models.py  # 16 tests: data model creation, P&L, config defaults
├── test_backtest_data.py    # 17 tests: CSV loading, timestamp parsing, sorting
├── test_backtest_broker.py  # 17 tests: fills, brackets, cash, slippage, mark-to-market
├── test_backtest_engine.py  # 12 tests: end-to-end runs, stock/options, position limits
├── test_backtest_metrics.py # 17 tests: returns, drawdown, Sharpe, strategy breakdown
├── test_backtest_report.py  # 5 tests:  report content, formatting, edge cases
└── test_backtest_yahoo_fetch.py # 19 tests: Yahoo chart API, parsing, validation

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
  POST /api/trade-stock or /api/trade-options
  token=... query param or Header: X-Webhook-Token
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
        ├─ mode=stock ────► Broker selected by STOCK_BROKER
        │                       TP / SL / stop-limit from strategy %
        │                       Day entry, GTC exits unless configured otherwise
        │
        └─ mode=options ──► Broker selected by OPTIONS_BROKER
                             Tastytrade options are blocked with HTTP 501 by
                             default until contract-symbol routing is verified
                             Screen option contracts (Yahoo + Greeks)
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

## Backtesting Engine

The backtesting engine replays historical price data through the **exact same strategy logic** used in live trading — same `get_strategy()` lookup, same `compute_stock_bracket_prices()` math, same `compute_options_exit_prices()` targets, same `compute_options_qty()` risk sizing. The only difference is that orders go to a simulated broker instead of Alpaca.

### Dashboard status

The dashboard is now focused on the live webhook workflow rather than browser-based backtesting. Use the Python and CLI entry points below for historical replay work, and use the dashboard for shared webhook monitoring, routing, and broker visibility.

### Quick start (Python)

```python
from backtesting import Engine, generate_report
from backtesting.yahoo_fetch import fetch_bars
from backtesting.metrics import compute_metrics

# Fetch historical bars directly from Yahoo Finance
bars = fetch_bars("AAPL", start="2024-01-01", end="2024-06-30", interval="1d")
signals = load_signals_csv("data/signals.csv")

# Run backtest
result = Engine(initial_capital=100_000, default_stock_qty=10).run(bars, signals)

# Analyze results
metrics = compute_metrics(result)
print(generate_report(result, metrics))
```

Or load bars from a CSV file instead:

```python
from backtesting import Engine, load_bars_csv, load_signals_csv, generate_report

bars = load_bars_csv("data/AAPL_daily.csv", ticker="AAPL")
signals = load_signals_csv("data/signals.csv")
result = Engine(initial_capital=100_000).run(bars, signals)
print(generate_report(result))
```

### Yahoo Finance data fetcher

`fetch_bars()` downloads historical OHLCV data directly from Yahoo Finance with no API key required:

```python
from backtesting.yahoo_fetch import fetch_bars

# Daily bars
bars = fetch_bars("AAPL", start="2024-01-01", end="2024-06-30", interval="1d")

# Intraday (5-minute bars)
bars = fetch_bars("TSLA", start="2024-06-01", end="2024-06-07", interval="5m")

# Weekly
bars = fetch_bars("SPY", start="2023-01-01", end="2024-01-01", interval="1wk")
```

Supported intervals: `1m`, `2m`, `5m`, `15m`, `30m`, `60m`, `90m`, `1h`, `1d`, `5d`, `1wk`, `1mo`, `3mo`.

### CSV formats

**Bars** (one row per OHLCV bar):

```csv
timestamp,open,high,low,close,volume
2024-01-02 09:30:00,150.00,151.25,149.80,150.50,1234567
2024-01-03 09:30:00,150.50,152.00,150.00,151.75,987654
```

An optional `ticker` column overrides the `ticker` argument to `load_bars_csv()`.

**Signals** (one row per trade signal):

```csv
timestamp,ticker,side,price,strategy,mode
2024-01-05 10:00:00,AAPL,buy,150.25,bollinger_mean_reversion,stock
2024-01-10 14:30:00,AAPL,sell,155.00,lorentzian_classification,options
```

The `mode` column defaults to `"stock"` if omitted. Timestamps are parsed flexibly (ISO-8601, `YYYY-MM-DD`, `MM/DD/YYYY`, with or without time).

### Configuration

```python
from backtesting import Engine, BacktestConfig

config = BacktestConfig(
    initial_capital=100_000,   # Starting cash ($)
    commission_per_trade=1.0,  # Flat fee per order fill ($)
    slippage_pct=0.05,         # Simulated slippage (% of fill price)
    default_stock_qty=10,      # Shares per stock signal
    max_dollar_risk=50.0,      # Max $ risk per options trade
    max_open_positions=5,      # Concurrent position cap (0 = unlimited)
)

engine = Engine(config=config)
result = engine.run(bars, signals)
```

### How fills work

The simulated broker checks each OHLCV bar against pending orders:

| Order type | Buy fills when | Sell fills when |
|---|---|---|
| **Limit** | `bar.low <= limit_price` | `bar.high >= limit_price` |
| **Stop** | `bar.high >= stop_price` | `bar.low <= stop_price` |
| **Market** | Immediately at `bar.open` | Immediately at `bar.open` |

**Stock bracket orders** model the same bracket lifecycle used by the live Tastytrade OTOCO path:
1. Entry limit order fills first
2. TP and SL legs activate after entry fills
3. When one exit leg fills, the other is automatically cancelled

**Options orders** use the same exit monitor pattern as the live system: limit entry + monitored TP/SL targets with the 100x options multiplier.

### Metrics

The engine computes standard quantitative trading metrics:

| Metric | Description |
|---|---|
| Total / annualised return | Equity growth over the test period |
| Sharpe ratio | Risk-adjusted return (annualised, 252 trading days) |
| Sortino ratio | Downside-deviation variant of Sharpe |
| Calmar ratio | Annualised return / max drawdown |
| Max drawdown | Largest peak-to-trough equity decline (% and $) |
| Win rate | Percentage of profitable trades |
| Profit factor | Gross profit / gross loss |
| Expectancy | Average P&L per trade |
| Exposure | Percentage of bars with open positions |
| Per-strategy breakdown | All trade stats grouped by strategy name |

### Example report output

```
============================================================
CRASSUS 2.5 -- BACKTEST REPORT
============================================================
Period:           2024-01-02 to 2024-06-28
Bars processed:   125
Signals processed:    12
Signals skipped:       0

------------------------------------------------------------
RETURNS
------------------------------------------------------------
Initial capital:  $    100,000.00
Final equity:     $    102,450.00
Total P&L:        $      2,450.00
Total return:              2.45%
Annualised return:         5.02%

------------------------------------------------------------
RISK METRICS
------------------------------------------------------------
Sharpe ratio:              1.234
Sortino ratio:             1.876
Max drawdown:              1.25%

------------------------------------------------------------
TRADE STATISTICS
------------------------------------------------------------
Total trades:                 12
Winning trades:                8
Losing trades:                 4
Win rate:                  66.7%
Profit factor:              2.15
Expectancy:       $        204.17

------------------------------------------------------------
PER-STRATEGY BREAKDOWN
------------------------------------------------------------
  bollinger_mean_reversion
    Trades:              7
    Win rate:         71.4%
    Total P&L:    $ 1,850.00

  lorentzian_classification
    Trades:              5
    Win rate:         60.0%
    Total P&L:    $   600.00
============================================================
END OF REPORT
============================================================
```

### Programmatic access

All results are available as Python objects for further analysis:

```python
result = engine.run(bars, signals)

# Equity curve (list of dicts with timestamp, equity, cash, open_positions)
for point in result.equity_curve:
    print(point["timestamp"], point["equity"])

# Completed trades
for trade in result.trades:
    print(trade.position.ticker, trade.position.pnl, trade.strategy)

# Open positions at end of backtest
for pos in result.open_positions:
    print(pos.ticker, pos.entry_price, pos.mode)

# Metrics as a dataclass
metrics = compute_metrics(result)
print(f"Sharpe: {metrics.sharpe_ratio:.3f}")
print(f"Max DD: {metrics.drawdown.max_drawdown_pct:.2f}%")
for name, sm in metrics.by_strategy.items():
    print(f"{name}: {sm.win_rate:.1f}% win rate, ${sm.total_pnl:.2f} P&L")
```

---

## Options: Design Decisions

### Data source vs execution venue

Current status: options execution remains safe by default. If `OPTIONS_BROKER=tastytrade` and `ENABLE_TASTYTRADE_OPTIONS=false`, the Function returns HTTP 501 instead of sending an unverified option order.

| Concern | Source | Why |
|---|---|---|
| **Market data** (bid/ask/IV/volume/OI) | Yahoo Finance | Richer options data than Alpaca's trading API |
| **Greeks computation** | `greeks.py` (local) | Black-Scholes from Yahoo's IV or solved from market prices |
| **Contract screening** | `options_screener.py` | Uses Yahoo data + Greeks for delta-based selection |
| **Order execution** | Alpaca fallback only | Tastytrade option symbol routing still needs end-to-end verification |

### Why no bracket orders for options?

Alpaca does **not** support bracket orders (`BRACKET` / `OCO` / `OTO`) for options contracts — the API returns an error. Therefore:

- **Entry:** Simple limit order with `TimeInForce.DAY`.
- **Exit monitoring:** The `exit_monitor.py` module tracks TP/SL targets for every options entry. A **timer trigger** (`check_options_exits_timer`) runs every 60 seconds, checks current prices against targets, and submits exit orders automatically:
  - **Take profit hit** → limit sell at TP price
  - **Stop loss hit** → market sell (fast exit)
  - **Position closed externally** → auto-cleans up stale targets

Target persistence uses Azure Blob Storage in hosted deployments, with a local JSON fallback (`.options_targets.json`) for development.

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
5. **Maps** selected contract symbol to the Alpaca fallback path for order submission

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

### Profile deploy

```bash
./deploy_azure.sh --env dev
./deploy_azure.sh --env prod
```

This script:
1. Validates Azure CLI, Functions Core Tools, Python, Git, and curl are installed.
2. Creates a minimal `.env` when one does not exist, then reads deployment settings from it.
3. Resolves the dev or prod stock Function App, options Function App, and dashboard Web App.
4. Aborts prod deploys unless the current branch is `main`.
5. Requires typing `DEPLOY PROD` before deploying production.
6. Warns that dev deploys overwrite the shared dev environment.
7. Pushes stock-specific, options-specific, dashboard, and deployed-branch metadata app settings.
8. Deploys the same `function_app` package to both Function Apps.
9. Deploys the dashboard package and prints stock/options/legacy webhook URLs.

**Prerequisites:**
- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) (`brew install azure-cli`)
- [Azure Functions Core Tools v4](https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local) (`brew tap azure/functions && brew install azure-functions-core-tools@4`)
- An active Azure subscription (`az login`)
- Optional: a `.env` file for custom Azure resource names or dashboard password settings. The deploy script creates a minimal one automatically if it is missing.

### What gets deployed

| Azure Resource | Name | Purpose |
|---|---|---|
| Resource Group | `CRG` by default | Container for all resources |
| Storage Account | `crassusstorage25` by default | Required by Azure Functions |
| DEV Stock Function App | `crassus-dev-stock` by default | Hosts `/api/trade-stock` in shared dev |
| DEV Options Function App | `crassus-dev-options` by default | Hosts `/api/trade-options` in shared dev |
| DEV Dashboard Web App | `crassus-dev-dashboard` by default | Shared dev dashboard |
| PROD Stock Function App | `crassus-prod-stock` by default | Hosts `/api/trade-stock` in production |
| PROD Options Function App | `crassus-prod-options` by default | Hosts `/api/trade-options` in production |
| PROD Dashboard Web App | `crassus-prod-dashboard` by default | Production dashboard |
| Dashboard App Service Plan | Derived from the dashboard app name by default | Hosts the shared Flask dashboard |

### Functions

| Function | Trigger | Schedule | Purpose |
|---|---|---|---|
| `trade_stock` | HTTP POST `/api/trade-stock` | On-demand | Receives stock/share TradingView webhooks |
| `trade_options` | HTTP POST `/api/trade-options` | On-demand | Receives options TradingView webhooks |
| `trade` | HTTP POST `/api/trade` | On-demand | Legacy route that warns and routes by `mode` |
| `check_options_exits_timer` | Timer | Every 60 seconds | Monitors options positions for TP/SL exits |

### Customizing resource names

Set these values in `.env` before running `./deploy_azure.sh` only if you want to override the defaults:
```bash
AZURE_RESOURCE_GROUP="CRG"
AZURE_LOCATION="eastus"
AZURE_STORAGE_ACCOUNT="crassusstorage25"
AZURE_SUBSCRIPTION_ID=""
AZURE_DEV_STOCK_FUNCTION_APP_NAME="crassus-dev-stock"
AZURE_DEV_OPTIONS_FUNCTION_APP_NAME="crassus-dev-options"
AZURE_DEV_DASHBOARD_APP_NAME="crassus-dev-dashboard"
AZURE_DEV_DASHBOARD_RESOURCE_GROUP=""
AZURE_DEV_DASHBOARD_PLAN_RESOURCE_GROUP=""
AZURE_DEV_DASHBOARD_PLAN_NAME=""
AZURE_PROD_STOCK_FUNCTION_APP_NAME="crassus-prod-stock"
AZURE_PROD_OPTIONS_FUNCTION_APP_NAME="crassus-prod-options"
AZURE_PROD_DASHBOARD_APP_NAME="crassus-prod-dashboard"
AZURE_PROD_DASHBOARD_RESOURCE_GROUP=""
AZURE_PROD_DASHBOARD_PLAN_RESOURCE_GROUP=""
AZURE_PROD_DASHBOARD_PLAN_NAME=""
AZURE_DASHBOARD_PLAN_NAME=""
AZURE_DASHBOARD_SKU="F1"
```

If the subscription cannot create a new Linux App Service plan, point the dashboard at an existing non-exhausted plan with the dashboard resource-group and plan variables above. The Function Apps still deploy to `AZURE_RESOURCE_GROUP`.

---

## Environment Variables

All variables are configurable via the dashboard UI or directly in `.env`.

### Required for live broker execution

| Variable | Description |
|---|---|
| `STOCK_BROKER` | Stock/share route broker: `alpaca` or `tastytrade` |
| `OPTIONS_BROKER` | Options route broker: `alpaca` or `tastytrade` |
| `ORDER_BROKER` | Legacy fallback only when split broker settings are missing |
| `TASTYTRADE_ACCOUNT_NUMBER` | Tastytrade account number; can be entered in the hosted dashboard after deployment |
| `TASTYTRADE_CLIENT_SECRET` | Tastytrade OAuth client secret; can be entered in the hosted dashboard after deployment and stored in Key Vault |
| `TASTYTRADE_REFRESH_TOKEN` | Tastytrade OAuth refresh token; can be entered in the hosted dashboard after deployment and stored in Key Vault |
| `WEBHOOK_AUTH_TOKEN` | Shared secret (via `X-Webhook-Token` header or `?token=` query param) |
| `STOCK_WEBHOOK_AUTH_TOKEN` | Optional stock route token; falls back to `WEBHOOK_AUTH_TOKEN` |
| `OPTIONS_WEBHOOK_AUTH_TOKEN` | Optional options route token; falls back to `WEBHOOK_AUTH_TOKEN` |

The Azure deployment itself does not require Tastytrade credentials in local `.env`. If they are missing, deployment continues with safe test/dry-run defaults; the hosted dashboard will show broker credentials as missing until you enter them there.
When credentials or webhook tokens are entered in the hosted dashboard, the dashboard syncs those settings to the current environment's stock Function App, options Function App, and dashboard Web App. Dev sync targets `crassus-dev-stock`, `crassus-dev-options`, and `crassus-dev-dashboard` by default; production keeps the original `crassus-25` Function App URL with split routes unless explicitly reconfigured.

### Optional (with defaults)

| Variable | Default | Description |
|---|---|---|
| `TASTYTRADE_IS_TEST` | `true` | `true` = Tastytrade cert/test API, `false` = production API |
| `TASTYTRADE_DRY_RUN` | `true` | Validate stock OTOCO payloads with Tastytrade dry-run endpoints without routing orders |
| `ENABLE_TASTYTRADE_OPTIONS` | `false` | Keep Tastytrade options disabled until contract-symbol routing is verified |
| `OPTIONS_ALLOW_FALLBACK_TO_ALPACA` | `false` | Allow explicit Alpaca fallback when options broker is Tastytrade |
| `TASTYTRADE_ENTRY_TIME_IN_FORCE` | `Day` | Time-in-force for the opening OTOCO order |
| `TASTYTRADE_EXIT_TIME_IN_FORCE` | `GTC` | Time-in-force for take-profit and stop exit orders |
| `TASTYTRADE_STOP_ORDER_TYPE` | `Stop Limit` | Stop or Stop Limit exit order |
| `ALPACA_PAPER` | `true` | Alpaca fallback only: `true` = paper trading, `false` = live |
| `LIVE_TRADING_CONFIRMED` | unset | Must be `yes` before Alpaca live or Tastytrade production trading is allowed |
| `DEFAULT_STOCK_QTY` | `1` | Shares per stock trade |
| `MAX_OPEN_POSITIONS` | `10` | Max concurrent open positions before new entries are blocked |
| `DEDUP_TTL_SECONDS` | `60` | Reject duplicate webhook fingerprints for this many seconds |
| `SIGNAL_DEDUP_CONTAINER` | `signal-dedup` | Azure Blob container used for cross-instance webhook idempotency |
| `OPTIONS_TARGETS_CONTAINER` | `options-exit-targets` | Azure Blob container used for shared options exit targets |
| `TRADING_HALTED` | `false` | Operator kill switch for blocking all new entries |
| `TRADING_HALTED_REASON` | empty | Optional reason returned in blocked responses and logs |
| `MAX_DAILY_LOSS_DOLLARS` | disabled | Block new entries after daily loss breaches this dollar amount |
| `MAX_DAILY_LOSS_PCT` | disabled | Block new entries after daily loss breaches this percentage |
| `TASTYTRADE_PREVIOUS_NET_LIQUIDATING_VALUE` | disabled | Optional baseline for Tastytrade daily-loss checks |
| `STALE_ORDER_MINUTES` | `120` | Cancel lingering unfilled stock entry orders after this many minutes |
| `AZURE_SUBSCRIPTION_ID` | Active `az login` subscription | Required for the hosted dashboard to sync Azure app settings via managed identity / SDK |
| `AZURE_DASHBOARD_APP_NAME` | Derived from function app name | Shared Azure Web App that serves the dashboard |
| `AZURE_DASHBOARD_RESOURCE_GROUP` | `AZURE_RESOURCE_GROUP` | Optional resource group override for the hosted dashboard app |
| `AZURE_DASHBOARD_PLAN_NAME` | Derived from dashboard app name | App Service plan for the hosted dashboard |
| `AZURE_DASHBOARD_PLAN_RESOURCE_GROUP` | Dashboard resource group | Optional resource group override for an existing dashboard App Service plan |
| `AZURE_DASHBOARD_SKU` | `F1` | App Service SKU for the hosted dashboard (F1=Free, B1=Basic) |
| `AZURE_USE_KEY_VAULT` | `true` | Store hosted secrets in Azure Key Vault and sync app settings as Key Vault references |
| `AZURE_KEY_VAULT_NAME` | Derived from storage account | Azure Key Vault used for hosted secrets |
| `AZURE_KEY_VAULT_SECRET_PREFIX` | Function app name | Prefix applied to Key Vault secret names |

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
| **401** | Missing or invalid auth token (header or query param) |
| **403** | Live trading not confirmed or daily loss guard blocked a new entry |
| **409** | Duplicate webhook rejected inside the dedup window |
| **422** | Buying power check failed before order submission |
| **429** | Position-limit guard blocked a new entry |
| **503** | Operator halt is active (`TRADING_HALTED=true`) |
| **502** | Broker API error (Alpaca or Tastytrade upstream failure) |
| **501** | Tastytrade options mode is intentionally blocked until symbol routing is verified |
| **500** | Internal / unexpected error |

All responses include a `correlation_id` for log tracing in Application Insights.

---

## Example curl

```bash
# Stock buy signal (bollinger_mean_reversion)
curl -X POST http://localhost:7071/api/trade-stock \
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
| `alpaca-py` | Alpaca fallback Trading API and legacy options path |
| `scipy` | `norm.cdf`/`norm.pdf` (Greeks), `brentq` (IV solver) |
| `numpy` | Numerical computation |
| `requests` | Yahoo Finance and Tastytrade HTTP client calls |

### Dashboard (`requirements-dashboard.txt`)

| Package | Purpose |
|---|---|
| `flask` | Web framework for the dashboard UI |
| `python-dotenv` | `.env` file loading |
| `alpaca-py` | Portfolio and account data |
| `requests` | Webhook test functionality |
| `azure-identity` / `azure-mgmt-web` | Hosted dashboard setting sync via Azure management APIs |
| `gunicorn` | Production WSGI server for Azure App Service |

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
   - Copy `.env.example` to `.env` and fill in your Tastytrade credentials and webhook token.
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
   Stock endpoint: `http://localhost:7071/api/trade-stock` (POST)
   Options endpoint: `http://localhost:7071/api/trade-options` (POST)
   Legacy endpoint: `http://localhost:7071/api/trade` (POST)

5. **Run tests**
   ```bash
   ./run_tests.sh
   ```
   For the real broker smoke test, run `python tests/test_live_alpaca.py` separately with paper-trading credentials.

6. **Deploy to Azure**
   ```bash
   ./deploy_azure.sh --env dev
   ```
   This deploys the stock Function App, options Function App, and hosted dashboard for the selected environment.
   Production deploys use `./deploy_azure.sh --env prod` from `main` and require typing `DEPLOY PROD`.

---

## License

Use and modify as needed for your own trading setup.
