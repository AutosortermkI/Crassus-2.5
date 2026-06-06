# Crassus Broker Dashboard, Paper Ledger, and Tastytrade Market Data Design

## Objective

Crassus needs a shared Azure-centric dashboard that clearly represents the current broker configuration, preserves paper-trading continuity across broker resets, and uses Tastytrade market data as the primary quote source for position marking and future strategy workflows.

The immediate problem is that the current dashboard presents broker status as a single selected path. In practice Crassus now has multiple related modes:

- Alpaca paper brokerage may still exist as a historical or fallback surface.
- Tastytrade sandbox/cert brokerage is used for broker-side integration tests and resets daily.
- Tastytrade production brokerage will eventually be used for real accounts.
- Tastytrade dry-run is an order preflight mode and can be used with either sandbox or production credentials.
- Crassus itself needs a durable paper-trading view that is not wiped by broker sandbox resets.

The dashboard must make those distinctions visible and operationally safe for non-local users.

## Scope

This design covers four deliverables:

1. Broker-mode dashboard representation.
2. Crassus-owned paper-trading ledger for continuity.
3. Combined broker and Crassus portfolio dashboard.
4. Tastytrade DXLink market-data ingestion and quote cache.

This design does not enable live trading by default. Live trading remains behind the existing `TASTYTRADE_DRY_RUN=false`, `TASTYTRADE_IS_TEST=false`, and `LIVE_TRADING_CONFIRMED=yes` controls.

## Current-State Findings

The dashboard currently has a `Broker Connection` panel that is Tastytrade-focused, but `/api/portfolio`, `/api/positions`, and `/api/orders` select one broker using stock routing. That can make the app look Alpaca-centric or single-broker even when Tastytrade sandbox is configured for execution.

The webhook activity store persists recent alert snapshots, but it is not a trading ledger. It records alerts and forward outcomes, not durable positions, fills, order lifecycle state, or equity curves.

Tastytrade sandbox/cert is useful for broker-side validation, but it resets daily. It cannot be the source of truth for multi-day paper-trading continuity.

Tastytrade streaming market data requires an API quote token and DXLink websocket connection. Quote streamer tokens expire after 24 hours. DXLink requires websocket setup, authorization, channel setup, feed subscription, and keepalive handling. A continuously connected websocket is a poor fit for Azure Functions Consumption, so this should be a separate worker-style component.

References:

- Tastytrade sandbox: `https://developer.tastytrade.com/sandbox/`
- Tastytrade streaming market data: `https://developer.tastytrade.com/streaming-market-data/`
- Tastytrade API usage and P/L marking guidance: `https://developer.tastytrade.com/basic-api-usage/`

## Design Principles

- Crassus state must be durable even when an external sandbox resets.
- Broker data and Crassus simulated state must be shown separately, not blended silently.
- The dashboard must show environment and safety mode explicitly: sandbox/cert, production, dry-run, and live gate.
- Tastytrade market data should be the quote authority when available, but stale or unavailable quote data must be visibly labeled.
- The app should not fabricate fills or market prices. If a broker dry-run validates an order but does not fill it, the ledger records a preflight result until an explicit simulated-fill rule is enabled.
- All partner-facing workflows should be hosted in Azure and usable from the dashboard without local environment navigation.

## Deliverable 1: Broker-Mode Dashboard Representation

Add a `Broker Control Center` section to the dashboard, visible near the top of the Webhooks or Portfolio tab.

It should include four cards:

- `Routing`: stock broker, options broker, stock endpoint, options endpoint, deployed branch, deployed commit.
- `Tastytrade`: account number, credential status, cert/sandbox state, dry-run state, options enabled state, last verification time, last verification result.
- `Alpaca`: credential status, paper/live state, last verification time, and whether Alpaca is active, inactive, or fallback-only.
- `Safety`: live-trading gate, trading halt state, max positions, max dollar risk, and whether the current mode can place real orders.

The top badge should become a composed mode label instead of a single generic badge. Examples of label components are `PROD`, `TASTYTRADE SANDBOX`, `DRY RUN`, `LIVE BLOCKED`, and `OPTIONS ENABLED`.

Backend changes:

- Add `/api/broker/status`.
- Return separate `routing`, `tastytrade`, `alpaca`, and `safety` objects.
- Do not include credential secrets or refresh tokens.
- Include boolean fields for `is_test`, `dry_run`, `live_confirmed`, and `can_place_live_orders`.
- Include clear `message` fields for mismatched modes, such as sandbox mode with production credentials or production mode with sandbox credentials when detectable from API response errors.

Frontend changes:

- Render the new cards.
- Keep existing credential-entry form, but label it as `Tastytrade Credentials`.
- Add a short mode explanation near the Cert/Sandbox and Dry Run toggles.
- Avoid implying that Alpaca is the active broker when stock/options routes are Tastytrade.

## Deliverable 2: Crassus Paper-Trading Ledger

Create an app-owned paper ledger that records Crassus decisions and simulated portfolio continuity independent of any broker sandbox.

Storage:

- Use Azure Blob Storage or Azure Table Storage within the existing resource group.
- Prefer Azure Table Storage for queryable entities if available through the existing `azure-storage-blob` dependency gap can be closed with `azure-data-tables`.
- If adding Azure Table Storage is too large for the first implementation, use append-only JSONL blobs with daily partition files and a compact materialized state blob.

Ledger records:

- `signal_received`: normalized alert payload and routing decision.
- `broker_preflight`: Tastytrade or Alpaca dry-run response.
- `broker_order`: non-dry-run order submission response when applicable.
- `paper_fill`: simulated fill generated by a configured paper-fill policy.
- `paper_position_opened`, `paper_position_adjusted`, `paper_position_closed`.
- `mark_update`: quote-derived mark price update.
- `equity_snapshot`: computed cash, open exposure, realized P/L, unrealized P/L, and total equity.

Materialized state:

- `paper_account`: starting cash, current cash, realized P/L, unrealized P/L, total equity, last mark timestamp.
- `paper_positions`: symbol or option contract, side, quantity, average entry, current mark, strategy, source signal, realized and unrealized P/L.
- `paper_orders`: alert/order lifecycle view with broker response correlation IDs.

Paper-fill policy:

- Default policy: record broker dry-run/preflight as validated but do not claim a fill.
- Optional policy: `PAPER_FILL_MODE=immediate_at_limit` for sandbox demonstrations. This should be clearly labeled as simulated and should not be confused with broker fills.
- Optional policy: `PAPER_FILL_MODE=market_data_cross` uses quote data to fill only when the market crosses the requested limit.

Continuity:

- The Crassus paper ledger is the continuity source of truth.
- Tastytrade sandbox broker positions are shown as a broker snapshot only.
- If Tastytrade sandbox resets, the dashboard should show `Broker sandbox reset detected` while preserving Crassus paper positions and equity history.

## Deliverable 3: Combined Broker and Crassus Dashboard

The Portfolio tab should be redesigned into three sections:

1. `Crassus Paper Account`
2. `Broker Snapshots`
3. `Market Data`

`Crassus Paper Account` shows durable account state:

- Total equity.
- Cash.
- Realized P/L.
- Unrealized P/L.
- Open positions.
- Recent ledger events.
- Equity curve over time once enough snapshots exist.

`Broker Snapshots` shows external broker state:

- Tastytrade sandbox/production balance, positions, and recent orders.
- Alpaca paper balance, positions, and recent orders if credentials exist.
- Last successful refresh for each broker.
- Clear missing/invalid credential messages for each broker separately.

`Market Data` shows:

- Quote source status.
- DXLink connection state.
- Quote token expiry time.
- Subscribed symbols.
- Last quote or trade timestamp.
- Staleness warnings.

API changes:

- Keep existing `/api/portfolio`, `/api/positions`, and `/api/orders` for backward compatibility.
- Add `/api/dashboard/combined` returning paper account, broker snapshots, and market-data summary.
- Add `/api/paper-ledger/events` for recent ledger history.
- Add `/api/paper-ledger/account` for materialized paper account state.

Frontend changes:

- Render combined sections without hiding unavailable brokers.
- Label Tastytrade sandbox data as sandbox data.
- Label Crassus paper ledger as persistent Crassus paper state.
- Do not show any broker reset as a Crassus paper loss.

## Deliverable 4: Tastytrade Market Data Stream

Create a separate Azure-hosted market-data worker that manages Tastytrade quote streaming.

Recommended hosting:

- Azure Container App or App Service WebJob.
- Do not host the long-lived websocket inside an Azure Functions Consumption HTTP trigger.

Responsibilities:

- Use stored Tastytrade credentials to obtain an access token.
- Fetch `/api-quote-tokens`.
- Store quote token metadata, including expiry time.
- Connect to DXLink websocket using `dxlink-url`.
- Send SETUP, AUTH, CHANNEL_REQUEST, FEED_SETUP, FEED_SUBSCRIPTION, and KEEPALIVE messages.
- Subscribe to symbols from active paper positions, recent alerts, configured watchlist, and explicit option contracts.
- Write latest quotes/trades/greeks/summaries to Azure storage.
- Reconnect with backoff on websocket failure.
- Refresh quote token before 24-hour expiry.
- Mark quote data stale when no current event is available.

Quote cache:

- `quote_latest` records per symbol/streamer-symbol.
- Fields include source, event type, bid, ask, last trade price, sizes, volume, greeks when available, timestamp, and staleness flag.
- The cache should store both raw event payload excerpts and normalized fields.

Symbology:

- Equity symbols can use normal ticker symbols where supported.
- Option contracts must use Tastytrade/DXLink streamer symbols when required.
- Add an instrument lookup helper that fetches `streamer-symbol` from Tastytrade instruments endpoints for option chains and equities.

Fallback:

- If DXLink is unavailable, continue using broker account endpoints and existing webhook activity.
- Mark quote-dependent P/L as stale or unavailable instead of inventing prices.

## Data Flow

1. TradingView sends alert to `/api/trade-stock` or `/api/trade-options`.
2. Function App parses and validates the signal.
3. Function App records a `signal_received` ledger event.
4. Function App routes to broker dry-run or order submit.
5. Function App records broker preflight/order response.
6. Paper ledger applies configured paper-fill policy.
7. Market-data worker updates quote cache.
8. Paper ledger marks positions using quote cache.
9. Dashboard reads combined status from dashboard APIs.

## Error Handling

Credential mismatch:

- If sandbox mode uses production credentials or production mode uses sandbox credentials, surface a mode-mismatch warning when the broker error indicates invalid credentials, revoked grant, customer not found, or wrong environment.

Sandbox reset:

- Detect by comparing broker snapshot history to previous broker state.
- Show a warning but preserve Crassus paper ledger.

DXLink token expiry:

- Refresh quote token before expiry.
- If refresh fails, keep last quote cache and mark all affected symbols stale.

Websocket disconnect:

- Retry with exponential backoff.
- Preserve last known quote values with stale indicators.

Storage failure:

- Do not claim a simulated fill if ledger write fails.
- Return an execution response that says broker preflight/order status and ledger persistence status separately.

## Testing Plan

Unit tests:

- Broker status API returns separate Tastytrade, Alpaca, routing, and safety objects.
- Tastytrade sandbox/dry-run labels render correctly.
- Paper ledger records events append-only.
- Materialized paper account updates from ledger events.
- Broker sandbox reset does not erase paper account state.
- Quote token expiry calculation and refresh scheduling.
- DXLink compact event normalization.
- Stale quote labeling.

Integration tests:

- Dashboard combined API returns stable data when Alpaca is missing and Tastytrade is present.
- Dashboard combined API returns stable data when both brokers are missing.
- Function App records ledger events for stock and options dry-runs.
- Tastytrade quote-token fetch uses cert/prod base URL according to `TASTYTRADE_IS_TEST`.

Live smoke tests:

- Verify dashboard shows Tastytrade sandbox and dry-run explicitly.
- Send a stock dry-run alert and confirm paper ledger event.
- Send an explicit-contract options dry-run alert and confirm paper ledger event.
- Fetch quote token with sandbox credentials if sandbox quote access is available.
- Confirm DXLink worker reports connected or a clear broker-side reason why streaming is unavailable.

## Implementation Phases

Phase 1: Broker control center and combined status API.

- Add `/api/broker/status`.
- Render broker cards.
- Add mode-mismatch warnings.
- Keep existing broker execution behavior unchanged.

Phase 2: Crassus paper ledger.

- Add durable ledger storage.
- Record alert and broker execution events.
- Add paper account materialization.
- Render persistent Crassus paper account section.

Phase 3: Combined broker dashboard.

- Add `/api/dashboard/combined`.
- Show Crassus paper state and broker snapshots side-by-side.
- Preserve old endpoints for compatibility.

Phase 4: Tastytrade market data worker.

- Add quote-token fetch.
- Add DXLink websocket worker.
- Add quote cache.
- Wire quote cache into paper P/L marking.

Phase 5: Production hardening.

- Add Azure deployment support for the worker.
- Add monitoring and stale-data warnings.
- Add live-mode readiness checklist.

## Acceptance Criteria

- The dashboard clearly shows whether Crassus is using Tastytrade sandbox, Tastytrade production, Alpaca paper, dry-run, or live mode.
- Tastytrade sandbox resets do not erase Crassus paper-trading history.
- The dashboard can show Tastytrade and Alpaca broker snapshots separately.
- Paper-trading P/L is computed from Crassus-owned ledger state and real quote cache data when available.
- Market-data streaming token refresh is automated and visible.
- DXLink connection state and quote staleness are visible in the dashboard.
- No route claims live execution unless broker order submission actually occurred.
- No route claims paper fills unless the configured paper-fill policy generated and persisted them.
