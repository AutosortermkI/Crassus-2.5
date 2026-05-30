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
- If the default dashboard App Service plan has no quota, set `AZURE_DEV_DASHBOARD_RESOURCE_GROUP`, `AZURE_DEV_DASHBOARD_PLAN_RESOURCE_GROUP`, and `AZURE_DEV_DASHBOARD_PLAN_NAME` to an existing non-exhausted plan before deploying.

Production:

- Stock Function App: `crassus-prod-stock`
- Options Function App: `crassus-prod-options`
- Dashboard Web App: `crassus-prod-dashboard`

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

## Broker Routing Controls

The dashboard Broker Routing section can switch:

- Stock / Share Broker: `alpaca` or `tastytrade`
- Options Broker: `alpaca` or `tastytrade`

These dropdowns change routing only. They do not alter `ALPACA_PAPER`, `TASTYTRADE_IS_TEST`, `TASTYTRADE_DRY_RUN`, `ENABLE_TASTYTRADE_OPTIONS`, `LIVE_TRADING_CONFIRMED`, trading halts, daily loss settings, or max position settings.

## Safety Rules

- Broker selection never enables live trading by itself.
- Alpaca live trading requires `ALPACA_PAPER=false` and `LIVE_TRADING_CONFIRMED=yes`.
- TastyTrade production trading requires `TASTYTRADE_IS_TEST=false`, `TASTYTRADE_DRY_RUN=false`, and `LIVE_TRADING_CONFIRMED=yes`.
- TastyTrade options remain disabled by default with `ENABLE_TASTYTRADE_OPTIONS=false`.
- Production deploy requires `main` and typing `DEPLOY PROD`.

The deployment script sets `DEPLOYED_GIT_BRANCH`, `DEPLOYED_GIT_SHA`, and `DEPLOYED_AT_UTC` in Azure app settings so the dashboard can show what is currently deployed.
