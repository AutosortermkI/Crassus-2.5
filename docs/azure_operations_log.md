# Azure Operations Log

This file records deployment and resource-management decisions that matter for the live Crassus Azure estate. It intentionally omits secrets, account numbers, portfolio values, and broker credentials.

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
