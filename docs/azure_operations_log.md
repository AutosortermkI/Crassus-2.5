# Azure Operations Log

This file records deployment and resource-management decisions that matter for the live Crassus Azure estate. It intentionally omits secrets, account numbers, portfolio values, and broker credentials.

## 2026-07-04 - Restore Hosted Dashboard Azure Sync Permissions

Branch: `jeremy/dashboard-sync-cleanup`

### Goal

- Let the hosted dev and production dashboards sync dashboard-entered broker/config settings to the matching split Function Apps.
- Keep sync scoped to the active split apps instead of the retired combined production Function App.
- Preserve paper-mode safety and avoid enabling live trading.

### Actions

- Enabled system-assigned managed identities on the production split Function Apps:
  - `crassus-25-stock`
  - `crassus-25-options`
- Granted the production dashboard identity app-setting write access to:
  - `crassus-25-stock`
  - `crassus-25-options`
- Granted the dev dashboard identity app-setting write access to:
  - `crassus-dev-stock`
  - `crassus-dev-options`
  - `crassus-dev-dashboard`
- Granted the dev dashboard identity Key Vault secret write access so dashboard-entered broker secrets can be stored as Key Vault references.
- Granted the production stock/options Function App identities Key Vault secret read access so their existing Key Vault app-setting references can resolve.
- Restored the production dashboard app setting `ALPACA_PAPER=true` after a failed dashboard sync left only the dashboard app drifted to `false`; the production Function Apps remained paper-mode throughout.
- Restarted the affected dashboard and production Function Apps to refresh identity and app-setting state.

### Verification

- Production dashboard app settings show split sync targets:
  - `AZURE_STOCK_FUNCTION_APP_NAME=crassus-25-stock`
  - `AZURE_OPTIONS_FUNCTION_APP_NAME=crassus-25-options`
- Production stock/options Function Apps still show `ALPACA_PAPER=true`, timers disabled, and deployed SHA `819b5007d8ae42dca650e5c73a8d52b741d89382`.
- Dashboard health checks returned HTTP `200` for dev and production.
- Unauthenticated stock/options route probes returned HTTP `401` for dev and production.
- No live-trading confirmation was enabled.

## 2026-07-04 - Split Production Function Apps

Branch: `jeremy/prod-split-functions`

### Goal

- Move production from one combined Function App to separate stock and options Function Apps.
- Keep the dashboard at `crassus-25-dashboard`.
- Keep Function Apps on the existing Dynamic/Consumption plan path and keep timer monitors disabled.
- Preserve existing webhook, broker authentication, and live-trading safety settings without printing secret values.
- Delete the old combined `crassus-25` Function App only after the new production stock/options apps are deployed and verified.

### Decision

- Production stock/share route: `crassus-25-stock` at `/api/trade-stock`.
- Production options route: `crassus-25-options` at `/api/trade-options`.
- `AZURE_LEGACY_PROD_FUNCTION_APP_NAME=crassus-25` is a migration helper so deployment can read existing Azure app settings from the old combined Function App before deletion.
- Both new production Function Apps use the same source package. Route-specific app settings enforce single-purpose behavior:
  - Stock app: `ACTIVE_TRADE_ENDPOINT=stock`, `ENABLE_STOCK_TRADING=true`, `ENABLE_OPTIONS_TRADING=false`
  - Options app: `ACTIVE_TRADE_ENDPOINT=options`, `ENABLE_STOCK_TRADING=false`, `ENABLE_OPTIONS_TRADING=true`
- Timers remain disabled on both apps:
  - `AzureWebJobs.check_options_exits_timer.Disabled=true`
  - `AzureWebJobs.check_stock_orders_timer.Disabled=true`

### Cost Note

The target Function Apps should stay on `EastUSLinuxDynamicPlan` (`Y1`, Dynamic/Consumption). This does not add a new fixed App Service plan charge. Deleting the old `crassus-25` Function App after verification avoids stray executions from the retired combined app.

## 2026-07-03 - Disable Timer Monitors And Standardize Broker-Native Exits

Branch: `jeremy/current-prod-dev-no-timers`

### Goal

- Reduce Azure Functions Consumption-plan cost by disabling idle timer-triggered monitor Functions.
- Keep `crassus-25` as the production Function App.
- Keep shared dev split across `crassus-dev-stock` and `crassus-dev-options`.
- Use Alpaca for stock/share bracket orders.
- Use Tastytrade for explicit-contract options OTOCO orders.
- Preserve webhook authentication, dashboard authentication, and deployed-branch metadata.

### Decision

- Deploy app settings that disable both timer-triggered monitor Functions:
  - `AzureWebJobs.check_options_exits_timer.Disabled=true`
  - `AzureWebJobs.check_stock_orders_timer.Disabled=true`
- Rely on broker-native order structures for TP/SL exits:
  - Alpaca stock/share bracket orders on the stock route.
  - Tastytrade explicit-contract option OTOCO orders on the options route.
- Do not create new production Function Apps during this cleanup; production remains `crassus-25` with split routes.
- Do not recreate deleted staging resources.
- Keep webhook authentication enabled, preserve existing Azure webhook tokens during redeploys, and redact webhook tokens from deployment output.

### Cost Finding

June 2026 Cost Management data showed the main `CRG` charges were:

- Azure Functions: about `$35.38`
- Dashboard App Service B1 plan: about `$12.65`
- Storage: about `$5.09`
- Log Analytics: about `$0.47`

The Function charges were consistent with idle timers running in three Function Apps. Each app was executing roughly 1,700 timer invocations per day.

### Verification Targets

After deployment, verify non-secret app settings on `crassus-25`, `crassus-dev-stock`, and `crassus-dev-options` show both timer disable flags as `true`, and verify `DEPLOYED_GIT_BRANCH`, `DEPLOYED_GIT_SHA`, and `DEPLOYED_AT_UTC` match the deployed source.

## 2026-05-30 - Preserve Old Dashboard URLs On B1

Branch: `jeremy/split-stock-options-routing`

### Goal

- Keep the exact existing dashboard hostnames:
  - `https://crassus-25-dashboard.azurewebsites.net`
  - `https://crassus-dev-dashboard.azurewebsites.net`
- Move the existing dashboard capacity off the exhausted Free plan path.
- Remove temporary staging resources that were created while debugging the quota/startup issue.
- Keep active production/dev resources and the shared Key Vault/storage references intact.

### Actions

- Scaled the existing App Service plan `crassus-25-dashboard-plan` in resource group `CRG` to `B1`.
- Kept both exact dashboard Web Apps on the existing `CRG` plan:
  - `crassus-25-dashboard`
  - `crassus-dev-dashboard`
- Confirmed the exact dev dashboard was using app settings from the working B1 test app, including broker routing and Oryx build settings.
- Repointed local ignored `.env` deploy overrides back to:
  - `AZURE_DEV_DASHBOARD_APP_NAME=crassus-dev-dashboard`
  - `AZURE_DEV_DASHBOARD_RESOURCE_GROUP=CRG`
  - `AZURE_DEV_DASHBOARD_PLAN_RESOURCE_GROUP=CRG`
  - `AZURE_DEV_DASHBOARD_PLAN_NAME=crassus-25-dashboard-plan`
  - `AZURE_DEV_DASHBOARD_BASE_URL=https://crassus-dev-dashboard.azurewebsites.net`
- Deleted staging resource group `CRG-staging-03121938`.

### Removed With Staging Cleanup

The deleted staging resource group contained temporary/debug resources only, including:

- Temporary dashboard app `crassus-dev-dashboard-b1`
- Staging dashboard app `crassus-25-dashboard-stg-03121938`
- Staging Function App `crassus-25-stg-03121938`
- Staging storage account `crassusstg03121938`
- Staging Key Vault `crassusstg03121938kv`
- Staging App Service plans in that resource group

### Intentionally Kept

These resources are active or referenced by the exact old URLs and should not be treated as abandoned:

- Resource group `CRG`
- Dashboard plan `crassus-25-dashboard-plan` on `B1`
- Exact dashboard apps `crassus-25-dashboard` and `crassus-dev-dashboard`
- Function Apps `crassus-25`, `crassus-dev-stock`, and `crassus-dev-options`
- Storage account `crassusstorage25`
- Key Vault `crassusstorage25kv`

### Verification

After the cleanup:

- `https://crassus-25-dashboard.azurewebsites.net/` returned HTTP `200`.
- `https://crassus-dev-dashboard.azurewebsites.net/` returned HTTP `200`.
- Both dashboard `/api/credentials/check` endpoints returned `status=ok`, `broker=alpaca`, and `paper=true`.
- Both dashboard `/api/portfolio` endpoints returned HTTP `200`.
- `az group exists --name CRG-staging-03121938` returned `false`.

### Notes

- No Azure quota increase was required. The workaround was to keep the original hostnames and move their shared dashboard plan to `B1`.
- Do not recreate the `CRG-staging-03121938` resources for normal dev deployment. Use the exact dev dashboard app in `CRG`.

## 2026-05-30 - Restore Dev Dashboard Login

Branch: `jeremy/split-stock-options-routing`

### Goal

- Restore the shared password gate on the dev dashboard at `https://crassus-dev-dashboard.azurewebsites.net`.
- Keep the plaintext dashboard password out of Git and out of tracked documentation.

### Actions

- Confirmed the dashboard login code was already present and controlled by Azure App Settings:
  - `DASHBOARD_ACCESS_PASSWORD`
  - `DASHBOARD_ACCESS_PASSWORD_HASH`
  - `DASHBOARD_SESSION_SECRET`
- Set `DASHBOARD_ACCESS_PASSWORD_HASH` on Web App `crassus-dev-dashboard` in resource group `CRG`.
- Set a stable `DASHBOARD_SESSION_SECRET` on the same Web App.
- Left `DASHBOARD_ACCESS_PASSWORD` blank so the plaintext password is not stored as an App Setting.
- Restarted `crassus-dev-dashboard`.

### Verification

After the restart:

- Anonymous `GET /` returned HTTP `302` to `/login?next=/`.
- Anonymous `GET /api/credentials/check` returned HTTP `401`.
- `GET /login` rendered the dashboard password form.
- Login with the configured password created a `crassus_dashboard_session` cookie.
- Authenticated `GET /` returned HTTP `200`.
- Authenticated `GET /api/credentials/check` returned `status=ok`, `broker=alpaca`, and `paper=true`.

### Notes

- The deployed password value is intentionally not recorded here.
- The change is configuration-only for dev; no dashboard source-code change was required.

## 2026-05-30 - Mirror Password Dashboard To Original Production URL

Branch: `jeremy/split-stock-options-routing`

### Goal

- Temporarily mirror the dev dashboard password gate to the original production dashboard URL at `https://crassus-25-dashboard.azurewebsites.net`.
- Keep the exact old production URL.
- Do not use this as the normal forward deployment path; future production deployments should go through `main`.
- Keep the plaintext dashboard password out of Git and out of tracked documentation.

### Actions

- Confirmed there is no separate `crassus-prod-dashboard` Web App in `CRG`; the original production dashboard Web App is `crassus-25-dashboard`.
- Copied the dev dashboard password hash into `DASHBOARD_ACCESS_PASSWORD_HASH` on `crassus-25-dashboard`.
- Left `DASHBOARD_ACCESS_PASSWORD` blank so the plaintext password is not stored as an App Setting.
- Kept `DASHBOARD_SESSION_SECRET` on its existing Key Vault reference.
- Attempted to store the hash in Key Vault first, but the current Azure identity did not have `secrets/setSecret` permission, so the hash was stored directly as an App Setting for this mirror.
- Redeployed the known password-auth dashboard snapshot from commit `97fac0cfdb57a69dfd4bb6e0b15047df72dfa9c6`.
- The first production redeploy was interrupted by an App Service/SCM restart and left `wwwroot` incomplete. A clean redeploy restored the package.
- Refreshed deployed metadata:
  - `DEPLOYED_GIT_BRANCH=jeremy/split-stock-options-routing`
  - `DEPLOYED_GIT_SHA=97fac0cfdb57a69dfd4bb6e0b15047df72dfa9c6`

### Verification

After the clean redeploy and metadata refresh:

- Anonymous `GET /` returned HTTP `302` to `/login?next=/`.
- `GET /login` returned HTTP `200`.
- Anonymous `GET /api/credentials/check` returned HTTP `401`.
- Login with the configured password returned the dashboard.
- Authenticated `GET /` returned HTTP `200`.
- Authenticated `GET /api/credentials/check` returned `status=ok`, `broker=alpaca`, and `paper=true`.

### Notes

- The deployed password value and hash are intentionally not recorded here.
- Azure CLI's startup tracker timed out on one clean redeploy attempt, but Kudu recorded the deployment as successful and the site passed the external HTTP/auth checks afterward.

## 2026-05-31 - Deploy Split Stock And Options Webhook URLs

Branch: `jeremy/split-stock-options-routing`

### Goal

- Replace the dashboard's legacy single TradingView webhook URL with separate stock/share and options URLs.
- Deploy the same branch commit to dev and the original production URLs.
- Keep production on the existing `crassus-25` Function App and `crassus-25-dashboard` dashboard URL.

### Code Deployed

- Commit `aa90700498cf2eb23ecb345c8f46c1e3297dd335`.
- Added production defaults that map both split production routes to `https://crassus-25.azurewebsites.net`.
- Updated `deploy_azure.sh` to support co-hosted stock/options routes in one Function App.
- Fixed the dashboard test-webhook endpoint so it uses the stock split route instead of the removed single-route helper.

### Azure Targets

- Dev dashboard: `crassus-dev-dashboard`
- Dev stock Function App: `crassus-dev-stock`
- Dev options Function App: `crassus-dev-options`
- Production dashboard: `crassus-25-dashboard`
- Production Function App: `crassus-25`

### Verification

- Local `python -m pytest` equivalent through `.venv\Scripts\python.exe -m pytest` passed: `349 passed`.
- Dev dashboard `/api/webhook/info` returned:
  - Stock: `https://crassus-dev-stock.azurewebsites.net/api/trade-stock`
  - Options: `https://crassus-dev-options.azurewebsites.net/api/trade-options`
- Production dashboard `/api/webhook/info` returned:
  - Stock: `https://crassus-25.azurewebsites.net/api/trade-stock`
  - Options: `https://crassus-25.azurewebsites.net/api/trade-options`
- Both dashboards still require login:
  - Anonymous `GET /` returned HTTP `302` to `/login?next=/`.
  - Anonymous `GET /api/webhook/info` returned HTTP `401`.
- Both authenticated dashboard `/api/credentials/check` endpoints returned `status=ok`, `broker=alpaca`, and `paper=true`.
- Unauthenticated route probes returned HTTP `401` for:
  - `https://crassus-dev-stock.azurewebsites.net/api/trade-stock`
  - `https://crassus-dev-options.azurewebsites.net/api/trade-options`
  - `https://crassus-25.azurewebsites.net/api/trade-stock`
  - `https://crassus-25.azurewebsites.net/api/trade-options`

### Notes

- The production dashboard deploy initially hung in Kudu with `dashboard_wsgi.py` missing from `wwwroot`; restarting the Web App and rerunning the same clean zip deploy restored the package.
- No webhook tokens, dashboard passwords, or password hashes are recorded here.
