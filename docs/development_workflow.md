# Development Workflow

## Branch Model

- `main` is the last known good branch and the only production deploy source.
- `jeremy/*` branches are Jeremy working branches. They may deploy to shared dev and must merge by PR.
- `joe/*` branches are Joe working branches. They may deploy to shared dev and must merge by PR.
- Protect `main`, require PR review, require tests to pass, block direct pushes, and prefer squash merge.

Example branches:

```bash
jeremy/split-routing
jeremy/tastytrade-options-safety
joe/dashboard-broker-controls
joe/deploy-script-profiles
```

## Dev And Prod Environments

Shared dev:

- Stock Function App: `crassus-dev-stock`
- Options Function App: `crassus-dev-options`
- Dashboard Web App: `crassus-dev-dashboard`
- Dashboard App Service plan: `crassus-25-dashboard-plan` in `CRG`, currently `B1`.
- Historical quota issue note: the old dashboard URLs are preserved on the `B1` plan. Do not point dev deploys back at deleted staging resources.

Production:

- Stock Function App: `crassus-25-stock`
- Options Function App: `crassus-25-options`
- Dashboard Web App: `crassus-25-dashboard`

Feature branches may deploy to DEV only. PROD may deploy from `main` only.

## Daily Workflow

Jeremy:

```bash
git checkout main
git pull origin main
git checkout -b jeremy/<task-name>
python -m pytest
./deploy_azure.sh --env dev
```

Joe:

```bash
git checkout main
git pull origin main
git checkout -b joe/<task-name>
python -m pytest
./deploy_azure.sh --env dev
```

Shared DEV warning: one shared dev environment means the last deployment wins. Tell the other person before deploying, and test only one active branch in dev at a time.

## Promotion Flow

1. Feature branch tested locally.
2. Feature branch deployed to shared dev.
3. Functionality confirmed in dev.
4. PR opened into `main`.
5. Other person reviews.
6. Tests pass.
7. PR merged into `main`.
8. Deploy production manually:

```bash
git checkout main
git pull origin main
./deploy_azure.sh --env prod
```

9. Tag the release after successful production deployment:

```bash
git tag prod-YYYY-MM-DD
git push origin prod-YYYY-MM-DD
```

## TradingView Webhooks

- Stock/share alerts use `/api/trade-stock`.
- Options alerts use `/api/trade-options`.
- `/api/trade` is retained temporarily as a legacy route and returns a deprecation warning.
- Dev and prod webhook URLs are different; copy the URL from the matching dashboard or deploy output.
- The dashboard Webhooks tab should display separate stock/share and options URLs. In Azure mode it should point stock/share alerts at the stock Function App and options alerts at the options Function App, then merge activity from both apps in the Active Webhooks view.
- Production uses separate stock and options Function Apps. The old combined `crassus-25` Function App should remain only during migration/verification and then be deleted to avoid stray executions.
- Deploy logs print webhook endpoints without secret tokens. Redeploys preserve existing Azure webhook tokens; use the dashboard Webhooks tab or configured secret store for full authenticated URLs.

## Broker Routing Controls

The dashboard Broker Routing section can switch:

- Stock / Share Broker: `alpaca` or `tastytrade`
- Options Broker: `alpaca` or `tastytrade`

These dropdowns change routing only. They do not alter `ALPACA_PAPER`, `TASTYTRADE_IS_TEST`, `TASTYTRADE_DRY_RUN`, `ENABLE_TASTYTRADE_OPTIONS`, `LIVE_TRADING_CONFIRMED`, trading halts, daily loss settings, or max position settings.

Dashboard credential and webhook-token saves follow the same environment target resolution as the split webhook URLs. A dev dashboard syncs to the dev stock Function App, dev options Function App, and dev dashboard Web App; production syncs to `crassus-25-stock`, `crassus-25-options`, and `crassus-25-dashboard`.

## Safety Rules

- Broker selection never enables live trading by itself.
- The stock/share Alpaca safety path must not inherit legacy `ORDER_BROKER=tastytrade`; split routes should use `STOCK_BROKER` and `OPTIONS_BROKER` for broker-specific live gates.
- Alpaca live trading requires `ALPACA_PAPER=false` and `LIVE_TRADING_CONFIRMED=yes`.
- TastyTrade production trading requires `TASTYTRADE_IS_TEST=false`, `TASTYTRADE_DRY_RUN=false`, and `LIVE_TRADING_CONFIRMED=yes`.
- TastyTrade options require explicit contract fields and still obey `TASTYTRADE_DRY_RUN`, `TASTYTRADE_IS_TEST`, and `LIVE_TRADING_CONFIRMED`.
- Production deploy requires `main` and typing `DEPLOY PROD`.

## Background Timers And Cost

Azure deployments disable both timer-triggered monitor functions by default:

- `AzureWebJobs.check_options_exits_timer.Disabled=true`
- `AzureWebJobs.check_stock_orders_timer.Disabled=true`

This keeps the Consumption-plan Function Apps from running idle every minute. Stock/share exits should come from Alpaca bracket orders. Options exits should come from Tastytrade OTOCO orders. Do not rely on the legacy Alpaca options exit monitor unless you intentionally re-enable the timer and accept the recurring Function execution cost.

The deployment script sets `DEPLOYED_GIT_BRANCH`, `DEPLOYED_GIT_SHA`, and `DEPLOYED_AT_UTC` in Azure app settings so the dashboard can show what is currently deployed.

## Azure Operations Trail

Operational Azure changes that are not represented directly by source code commits are recorded in [Azure Operations Log](azure_operations_log.md), including the 2026-05-30 B1 dashboard plan update and staging resource cleanup.
