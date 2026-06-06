#!/usr/bin/env bash
set -euo pipefail

echo "===================================="
echo "  Crassus 2.5 - Azure Deployment"
echo "===================================="
echo

usage() {
    cat <<'USAGE'
Usage:
  ./deploy_azure.sh --env dev
  ./deploy_azure.sh --env prod
  ./deploy_azure.sh --env dev --branch jeremy/split-routing
  ./deploy_azure.sh --env dev --branch joe/dashboard-broker-controls
USAGE
}

DEPLOY_ENV=""
REQUESTED_BRANCH=""
while [ $# -gt 0 ]; do
    case "$1" in
        --env)
            DEPLOY_ENV="${2:-}"
            shift 2
            ;;
        --branch)
            REQUESTED_BRANCH="${2:-}"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "[ERROR] Unknown argument: $1"
            usage
            exit 1
            ;;
    esac
done

DEPLOY_ENV="${DEPLOY_ENV:-dev}"
if [ "$DEPLOY_ENV" != "dev" ] && [ "$DEPLOY_ENV" != "prod" ]; then
    echo "[ERROR] --env must be dev or prod"
    exit 1
fi

for cmd in az func python3 git curl; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "[ERROR] Required command not found: $cmd"
        exit 1
    fi
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
mkdir -p "$SCRIPT_DIR/.azure"
if [ ! -f "$ENV_FILE" ]; then
    echo "[INFO] .env file not found at $ENV_FILE"
    echo "       Creating a minimal deployment config. Broker credentials can be entered in the hosted dashboard after deployment."
    touch "$ENV_FILE"
fi

load_env_var() {
    local key="$1"
    local val
    val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2- || true)
    echo "$val"
}

upsert_env_var() {
    local key="$1"
    local value="$2"
    python3 - "$ENV_FILE" "$key" "$value" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
key = sys.argv[2]
value = sys.argv[3]
lines = env_path.read_text().splitlines() if env_path.exists() else []
updated = False
new_lines = []
for line in lines:
    if line.startswith(f"{key}="):
        new_lines.append(f"{key}={value}")
        updated = True
    else:
        new_lines.append(line)
if not updated:
    if new_lines and new_lines[-1] != "":
        new_lines.append("")
    new_lines.append(f"{key}={value}")
env_path.write_text("\n".join(new_lines) + "\n")
PY
}

env_default() {
    local value="$1"
    local fallback="$2"
    if [ -n "$value" ]; then
        printf '%s' "$value"
    else
        printf '%s' "$fallback"
    fi
}

current_utc() {
    python3 - <<'PY'
from datetime import datetime, timezone
print(datetime.now(timezone.utc).isoformat())
PY
}

create_dashboard_package() {
    local output_path="$1"
    python3 - "$SCRIPT_DIR" "$output_path" <<'PY'
from pathlib import Path
import sys
import zipfile

root = Path(sys.argv[1])
output = Path(sys.argv[2])
include_files = [
    ".env.example",
    "dashboard_wsgi.py",
    "requirements.txt",
    "requirements-dashboard.txt",
]
include_dirs = ["dashboard", "function_app"]
skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".azure"}
skip_suffixes = {".pyc", ".pyo"}
skip_names = {"local.settings.json", ".options_targets.json", ".webhook_activity.json"}

with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
    for rel in include_files:
        path = root / rel
        if path.exists():
            zf.write(path, rel)
    for rel in include_dirs:
        directory = root / rel
        for path in directory.rglob("*"):
            if any(part in skip_dirs for part in path.parts):
                continue
            if path.is_dir() or path.name in skip_names or path.suffix in skip_suffixes:
                continue
            zf.write(path, path.relative_to(root).as_posix())
PY
}

ensure_function_app() {
    local app_name="$1"
    if az functionapp show --name "$app_name" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
        echo "[OK] Function App \"$app_name\" already exists."
        return
    fi
    echo "Creating Function App \"$app_name\"..."
    az functionapp create \
        --resource-group "$RESOURCE_GROUP" \
        --consumption-plan-location "$LOCATION" \
        --runtime python \
        --runtime-version "$PYTHON_VERSION" \
        --functions-version 4 \
        --name "$app_name" \
        --storage-account "$STORAGE_ACCOUNT" \
        --os-type Linux \
        --output none
}

ensure_dashboard_app() {
    if az webapp show --name "$DASHBOARD_APP_NAME" --resource-group "$DASHBOARD_RESOURCE_GROUP" --output none >/dev/null 2>&1; then
        echo "[OK] Dashboard Web App \"$DASHBOARD_APP_NAME\" already exists."
        return
    fi

    local plan_id
    local plan_arg
    plan_id="$(dashboard_plan_id)"
    if [ -n "$plan_id" ]; then
        echo "[OK] Using existing dashboard plan \"$DASHBOARD_PLAN_NAME\" in \"$DASHBOARD_PLAN_RESOURCE_GROUP\"."
    else
        echo "Creating dashboard plan \"$DASHBOARD_PLAN_NAME\" in \"$DASHBOARD_PLAN_RESOURCE_GROUP\"..."
        az appservice plan create \
            --name "$DASHBOARD_PLAN_NAME" \
            --resource-group "$DASHBOARD_PLAN_RESOURCE_GROUP" \
            --location "$DASHBOARD_LOCATION" \
            --is-linux \
            --sku "$DASHBOARD_SKU" \
            --output none
        plan_id="$(dashboard_plan_id)"
    fi
    plan_arg="$(dashboard_plan_arg)"

    echo "Creating Dashboard Web App \"$DASHBOARD_APP_NAME\"..."
    MSYS_NO_PATHCONV=1 az webapp create \
        --resource-group "$DASHBOARD_RESOURCE_GROUP" \
        --plan "$plan_arg" \
        --name "$DASHBOARD_APP_NAME" \
        --runtime "PYTHON:$PYTHON_VERSION" \
        --output none
}

dashboard_plan_id() {
    az appservice plan show \
        --name "$DASHBOARD_PLAN_NAME" \
        --resource-group "$DASHBOARD_PLAN_RESOURCE_GROUP" \
        --query id \
        --output tsv 2>/dev/null || true
}

dashboard_plan_arg() {
    if [ "$DASHBOARD_PLAN_RESOURCE_GROUP" = "$DASHBOARD_RESOURCE_GROUP" ]; then
        printf '%s' "$DASHBOARD_PLAN_NAME"
    else
        dashboard_plan_id
    fi
}

ensure_dashboard_can_start() {
    local state
    state="$(az webapp show --name "$DASHBOARD_APP_NAME" --resource-group "$DASHBOARD_RESOURCE_GROUP" --query state -o tsv 2>/dev/null || true)"
    if [ "$state" = "QuotaExceeded" ]; then
        echo "[ERROR] Dashboard Web App is in QuotaExceeded state."
        echo "        App:  $DASHBOARD_APP_NAME"
        echo "        Plan: $DASHBOARD_PLAN_NAME ($DASHBOARD_PLAN_RESOURCE_GROUP)"
        echo "        Choose an existing non-exhausted plan via AZURE_${DEPLOY_ENV^^}_DASHBOARD_PLAN_NAME"
        echo "        and AZURE_${DEPLOY_ENV^^}_DASHBOARD_PLAN_RESOURCE_GROUP, or scale the plan before deploying."
        exit 1
    fi
}

CURRENT_GIT_BRANCH="$(git -C "$SCRIPT_DIR" branch --show-current 2>/dev/null || true)"
CURRENT_GIT_SHA="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null || true)"
CURRENT_GIT_BRANCH="${CURRENT_GIT_BRANCH:-unknown}"
CURRENT_GIT_SHA="${CURRENT_GIT_SHA:-unknown}"
DEPLOYED_AT_UTC="$(current_utc)"

echo "Environment: $DEPLOY_ENV"
echo "Git branch: $CURRENT_GIT_BRANCH"
echo "Git SHA: $CURRENT_GIT_SHA"
echo

if [ -n "$REQUESTED_BRANCH" ] && [ "$CURRENT_GIT_BRANCH" != "$REQUESTED_BRANCH" ]; then
    echo "[ERROR] Requested branch \"$REQUESTED_BRANCH\" does not match current branch \"$CURRENT_GIT_BRANCH\"."
    echo "        The deployment script will not switch branches automatically."
    exit 1
fi

if [ "$DEPLOY_ENV" = "prod" ] && [ "$CURRENT_GIT_BRANCH" != "main" ]; then
    echo "[ERROR] PROD deployment may only run from main. Current branch: $CURRENT_GIT_BRANCH"
    exit 1
fi

if [ "$DEPLOY_ENV" = "prod" ]; then
    echo "Production deployment selected."
    echo "Type DEPLOY PROD to continue"
    read -r CONFIRMATION
    if [ "$CONFIRMATION" != "DEPLOY PROD" ]; then
        echo "[ERROR] Production deployment cancelled."
        exit 1
    fi
else
    echo "Deploying current branch to DEV. This will overwrite the shared dev environment."
    echo "Shared DEV warning: this deployment replaces whatever branch was previously running in dev."
fi
echo

DEFAULT_RESOURCE_GROUP="CRG"
DEFAULT_LOCATION="eastus"
DEFAULT_STORAGE_ACCOUNT="crassusstorage25"
PYTHON_VERSION="3.11"
DEFAULT_DASHBOARD_SKU="F1"
DASHBOARD_STARTUP_COMMAND='gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app'

RESOURCE_GROUP="$(env_default "$(load_env_var "AZURE_RESOURCE_GROUP")" "$DEFAULT_RESOURCE_GROUP")"
LOCATION="$(env_default "$(load_env_var "AZURE_LOCATION")" "$DEFAULT_LOCATION")"
STORAGE_ACCOUNT="$(env_default "$(load_env_var "AZURE_STORAGE_ACCOUNT")" "$DEFAULT_STORAGE_ACCOUNT")"
DASHBOARD_SKU="$(env_default "$(load_env_var "AZURE_DASHBOARD_SKU")" "$DEFAULT_DASHBOARD_SKU")"
STOCK_BROKER="$(env_default "$(load_env_var "STOCK_BROKER")" "alpaca")"
OPTIONS_BROKER="$(env_default "$(load_env_var "OPTIONS_BROKER")" "tastytrade")"
ORDER_BROKER="$(load_env_var "ORDER_BROKER")"

ALPACA_API_KEY="$(load_env_var "ALPACA_API_KEY")"
ALPACA_SECRET_KEY="$(load_env_var "ALPACA_SECRET_KEY")"
ALPACA_PAPER="$(load_env_var "ALPACA_PAPER")"
TASTYTRADE_ACCOUNT_NUMBER="$(load_env_var "TASTYTRADE_ACCOUNT_NUMBER")"
TASTYTRADE_CLIENT_SECRET="$(load_env_var "TASTYTRADE_CLIENT_SECRET")"
TASTYTRADE_REFRESH_TOKEN="$(load_env_var "TASTYTRADE_REFRESH_TOKEN")"
TASTYTRADE_IS_TEST="$(load_env_var "TASTYTRADE_IS_TEST")"
TASTYTRADE_DRY_RUN="$(load_env_var "TASTYTRADE_DRY_RUN")"
ENABLE_TASTYTRADE_OPTIONS="$(load_env_var "ENABLE_TASTYTRADE_OPTIONS")"
OPTIONS_ALLOW_FALLBACK_TO_ALPACA="$(load_env_var "OPTIONS_ALLOW_FALLBACK_TO_ALPACA")"
LIVE_TRADING_CONFIRMED="$(load_env_var "LIVE_TRADING_CONFIRMED")"
WEBHOOK_AUTH_TOKEN="$(load_env_var "WEBHOOK_AUTH_TOKEN")"
STOCK_WEBHOOK_AUTH_TOKEN="$(load_env_var "STOCK_WEBHOOK_AUTH_TOKEN")"
OPTIONS_WEBHOOK_AUTH_TOKEN="$(load_env_var "OPTIONS_WEBHOOK_AUTH_TOKEN")"
DASHBOARD_ACCESS_PASSWORD="$(load_env_var "DASHBOARD_ACCESS_PASSWORD")"
DASHBOARD_ACCESS_PASSWORD_HASH="$(load_env_var "DASHBOARD_ACCESS_PASSWORD_HASH")"
DASHBOARD_SESSION_SECRET="$(load_env_var "DASHBOARD_SESSION_SECRET")"
AZURE_SUBSCRIPTION_ID="$(load_env_var "AZURE_SUBSCRIPTION_ID")"

if [ -z "$WEBHOOK_AUTH_TOKEN" ]; then
    WEBHOOK_AUTH_TOKEN="$(python3 -c "import secrets; print(secrets.token_hex(16))")"
    echo "[INFO] Auto-generated WEBHOOK_AUTH_TOKEN: $WEBHOOK_AUTH_TOKEN"
    upsert_env_var "WEBHOOK_AUTH_TOKEN" "$WEBHOOK_AUTH_TOKEN"
fi
STOCK_WEBHOOK_AUTH_TOKEN="${STOCK_WEBHOOK_AUTH_TOKEN:-$WEBHOOK_AUTH_TOKEN}"
OPTIONS_WEBHOOK_AUTH_TOKEN="${OPTIONS_WEBHOOK_AUTH_TOKEN:-$WEBHOOK_AUTH_TOKEN}"

if [ "$STOCK_BROKER" = "tastytrade" ] || [ "$OPTIONS_BROKER" = "tastytrade" ] || [ "$ORDER_BROKER" = "tastytrade" ]; then
    if [ -z "$TASTYTRADE_ACCOUNT_NUMBER" ] || [ -z "$TASTYTRADE_CLIENT_SECRET" ] || [ -z "$TASTYTRADE_REFRESH_TOKEN" ]; then
        echo "[WARN] Tastytrade credentials are not set locally."
        echo "       Deployment will continue with Tastytrade execution unconfigured."
        echo "       Open the hosted dashboard after deployment to enter and sync Tastytrade credentials."
    fi
elif [ -z "$ALPACA_API_KEY" ] || [ -z "$ALPACA_SECRET_KEY" ]; then
    echo "[WARN] Alpaca credentials are not set locally. Missing credentials fail safe at order time."
fi

upsert_env_var "AZURE_RESOURCE_GROUP" "$RESOURCE_GROUP"
upsert_env_var "AZURE_LOCATION" "$LOCATION"
upsert_env_var "AZURE_STORAGE_ACCOUNT" "$STORAGE_ACCOUNT"
upsert_env_var "ENVIRONMENT_NAME" "$DEPLOY_ENV"
upsert_env_var "STOCK_BROKER" "$STOCK_BROKER"
upsert_env_var "OPTIONS_BROKER" "$OPTIONS_BROKER"
TASTYTRADE_IS_TEST="${TASTYTRADE_IS_TEST:-false}"
TASTYTRADE_DRY_RUN="${TASTYTRADE_DRY_RUN:-true}"
upsert_env_var "TASTYTRADE_IS_TEST" "$TASTYTRADE_IS_TEST"
upsert_env_var "TASTYTRADE_DRY_RUN" "$TASTYTRADE_DRY_RUN"

if [ "$DEPLOY_ENV" = "prod" ]; then
    STOCK_FUNCTION_APP_NAME="$(env_default "$(load_env_var "AZURE_PROD_STOCK_FUNCTION_APP_NAME")" "crassus-25")"
    OPTIONS_FUNCTION_APP_NAME="$(env_default "$(load_env_var "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME")" "crassus-25")"
    DASHBOARD_APP_NAME="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_APP_NAME")" "crassus-25-dashboard")"
    DASHBOARD_RESOURCE_GROUP="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_RESOURCE_GROUP")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_RESOURCE_GROUP")" "$RESOURCE_GROUP")")"
    DASHBOARD_PLAN_RESOURCE_GROUP="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_PLAN_RESOURCE_GROUP")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_PLAN_RESOURCE_GROUP")" "$DASHBOARD_RESOURCE_GROUP")")"
    DASHBOARD_PLAN_NAME="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_PLAN_NAME")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_PLAN_NAME")" "${DASHBOARD_APP_NAME}-plan")")"
    DASHBOARD_LOCATION="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_LOCATION")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_LOCATION")" "$LOCATION")")"
    STOCK_FUNCTION_BASE_URL="$(env_default "$(load_env_var "AZURE_PROD_STOCK_FUNCTION_BASE_URL")" "https://${STOCK_FUNCTION_APP_NAME}.azurewebsites.net")"
    OPTIONS_FUNCTION_BASE_URL="$(env_default "$(load_env_var "AZURE_PROD_OPTIONS_FUNCTION_BASE_URL")" "https://${OPTIONS_FUNCTION_APP_NAME}.azurewebsites.net")"
    DASHBOARD_BASE_URL="$(env_default "$(load_env_var "AZURE_PROD_DASHBOARD_BASE_URL")" "https://${DASHBOARD_APP_NAME}.azurewebsites.net")"
else
    STOCK_FUNCTION_APP_NAME="$(env_default "$(load_env_var "AZURE_DEV_STOCK_FUNCTION_APP_NAME")" "crassus-dev-stock")"
    OPTIONS_FUNCTION_APP_NAME="$(env_default "$(load_env_var "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME")" "crassus-dev-options")"
    DASHBOARD_APP_NAME="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_APP_NAME")" "crassus-dev-dashboard")"
    DASHBOARD_RESOURCE_GROUP="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_RESOURCE_GROUP")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_RESOURCE_GROUP")" "$RESOURCE_GROUP")")"
    DASHBOARD_PLAN_RESOURCE_GROUP="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_PLAN_RESOURCE_GROUP")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_PLAN_RESOURCE_GROUP")" "$DASHBOARD_RESOURCE_GROUP")")"
    DASHBOARD_PLAN_NAME="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_PLAN_NAME")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_PLAN_NAME")" "${DASHBOARD_APP_NAME}-plan")")"
    DASHBOARD_LOCATION="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_LOCATION")" "$(env_default "$(load_env_var "AZURE_DASHBOARD_LOCATION")" "$LOCATION")")"
    STOCK_FUNCTION_BASE_URL="$(env_default "$(load_env_var "AZURE_DEV_STOCK_FUNCTION_BASE_URL")" "https://${STOCK_FUNCTION_APP_NAME}.azurewebsites.net")"
    OPTIONS_FUNCTION_BASE_URL="$(env_default "$(load_env_var "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL")" "https://${OPTIONS_FUNCTION_APP_NAME}.azurewebsites.net")"
    DASHBOARD_BASE_URL="$(env_default "$(load_env_var "AZURE_DEV_DASHBOARD_BASE_URL")" "https://${DASHBOARD_APP_NAME}.azurewebsites.net")"
fi

echo "Target Azure apps:"
echo "  Stock Function App:   $STOCK_FUNCTION_APP_NAME"
echo "  Options Function App: $OPTIONS_FUNCTION_APP_NAME"
echo "  Dashboard Web App:    $DASHBOARD_APP_NAME ($DASHBOARD_RESOURCE_GROUP)"
echo "  Dashboard Plan:       $DASHBOARD_PLAN_NAME ($DASHBOARD_PLAN_RESOURCE_GROUP)"
echo

echo "Checking Azure login..."
az account show >/dev/null 2>&1 || az login
echo "[OK] Azure login ready."

echo "Ensuring resource group \"$RESOURCE_GROUP\" exists..."
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none
if [ "$DASHBOARD_RESOURCE_GROUP" != "$RESOURCE_GROUP" ]; then
    echo "Ensuring dashboard resource group \"$DASHBOARD_RESOURCE_GROUP\" exists..."
    az group create --name "$DASHBOARD_RESOURCE_GROUP" --location "$DASHBOARD_LOCATION" --output none
fi
if [ "$DASHBOARD_PLAN_RESOURCE_GROUP" != "$RESOURCE_GROUP" ] && [ "$DASHBOARD_PLAN_RESOURCE_GROUP" != "$DASHBOARD_RESOURCE_GROUP" ]; then
    echo "Ensuring dashboard plan resource group \"$DASHBOARD_PLAN_RESOURCE_GROUP\" exists..."
    az group create --name "$DASHBOARD_PLAN_RESOURCE_GROUP" --location "$DASHBOARD_LOCATION" --output none
fi

if az storage account show --name "$STORAGE_ACCOUNT" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    echo "[OK] Storage account \"$STORAGE_ACCOUNT\" already exists."
else
    echo "Creating storage account \"$STORAGE_ACCOUNT\"..."
    az storage account create \
        --name "$STORAGE_ACCOUNT" \
        --location "$LOCATION" \
        --resource-group "$RESOURCE_GROUP" \
        --sku Standard_LRS \
        --output none
fi

if [ "$STOCK_FUNCTION_APP_NAME" = "$OPTIONS_FUNCTION_APP_NAME" ]; then
    ensure_function_app "$STOCK_FUNCTION_APP_NAME"
else
    ensure_function_app "$STOCK_FUNCTION_APP_NAME"
    ensure_function_app "$OPTIONS_FUNCTION_APP_NAME"
fi
ensure_dashboard_app
ensure_dashboard_can_start

COMMON_FUNCTION_SETTINGS=(
    "ENVIRONMENT_NAME=$DEPLOY_ENV"
    "STOCK_BROKER=$STOCK_BROKER"
    "OPTIONS_BROKER=$OPTIONS_BROKER"
    "ORDER_BROKER=$ORDER_BROKER"
    "WEBHOOK_AUTH_TOKEN=$WEBHOOK_AUTH_TOKEN"
    "STOCK_WEBHOOK_AUTH_TOKEN=$STOCK_WEBHOOK_AUTH_TOKEN"
    "OPTIONS_WEBHOOK_AUTH_TOKEN=$OPTIONS_WEBHOOK_AUTH_TOKEN"
    "ENABLE_TASTYTRADE_OPTIONS=${ENABLE_TASTYTRADE_OPTIONS:-false}"
    "OPTIONS_ALLOW_FALLBACK_TO_ALPACA=${OPTIONS_ALLOW_FALLBACK_TO_ALPACA:-false}"
    "AzureWebJobsFeatureFlags=EnableWorkerIndexing"
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
    "ENABLE_ORYX_BUILD=true"
    "DEPLOYED_GIT_BRANCH=$CURRENT_GIT_BRANCH"
    "DEPLOYED_GIT_SHA=$CURRENT_GIT_SHA"
    "DEPLOYED_AT_UTC=$DEPLOYED_AT_UTC"
)
if [ -n "$ALPACA_API_KEY" ]; then
    COMMON_FUNCTION_SETTINGS+=("ALPACA_API_KEY=$ALPACA_API_KEY")
fi
if [ -n "$ALPACA_SECRET_KEY" ]; then
    COMMON_FUNCTION_SETTINGS+=("ALPACA_SECRET_KEY=$ALPACA_SECRET_KEY")
fi
if [ -n "$TASTYTRADE_ACCOUNT_NUMBER" ]; then
    COMMON_FUNCTION_SETTINGS+=("TASTYTRADE_ACCOUNT_NUMBER=$TASTYTRADE_ACCOUNT_NUMBER")
fi
if [ -n "$TASTYTRADE_CLIENT_SECRET" ]; then
    COMMON_FUNCTION_SETTINGS+=("TASTYTRADE_CLIENT_SECRET=$TASTYTRADE_CLIENT_SECRET")
fi
if [ -n "$TASTYTRADE_REFRESH_TOKEN" ]; then
    COMMON_FUNCTION_SETTINGS+=("TASTYTRADE_REFRESH_TOKEN=$TASTYTRADE_REFRESH_TOKEN")
fi
if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
    COMMON_FUNCTION_SETTINGS+=("AZURE_SUBSCRIPTION_ID=$AZURE_SUBSCRIPTION_ID")
fi

if [ "$DEPLOY_ENV" = "dev" ]; then
    COMMON_FUNCTION_SETTINGS+=(
        "ALPACA_PAPER=${ALPACA_PAPER:-true}"
        "TASTYTRADE_IS_TEST=${TASTYTRADE_IS_TEST:-false}"
        "TASTYTRADE_DRY_RUN=${TASTYTRADE_DRY_RUN:-true}"
        "LIVE_TRADING_CONFIRMED=${LIVE_TRADING_CONFIRMED:-no}"
    )
else
    if [ -n "$ALPACA_PAPER" ]; then
        COMMON_FUNCTION_SETTINGS+=("ALPACA_PAPER=$ALPACA_PAPER")
    fi
    if [ -n "$TASTYTRADE_IS_TEST" ]; then
        COMMON_FUNCTION_SETTINGS+=("TASTYTRADE_IS_TEST=$TASTYTRADE_IS_TEST")
    fi
    if [ -n "$TASTYTRADE_DRY_RUN" ]; then
        COMMON_FUNCTION_SETTINGS+=("TASTYTRADE_DRY_RUN=$TASTYTRADE_DRY_RUN")
    fi
    if [ -n "$LIVE_TRADING_CONFIRMED" ]; then
        COMMON_FUNCTION_SETTINGS+=("LIVE_TRADING_CONFIRMED=$LIVE_TRADING_CONFIRMED")
    fi
fi

STOCK_FUNCTION_SETTINGS=(
    "${COMMON_FUNCTION_SETTINGS[@]}"
    "ACTIVE_TRADE_ENDPOINT=stock"
    "ENABLE_STOCK_TRADING=true"
    "ENABLE_OPTIONS_TRADING=false"
)
OPTIONS_FUNCTION_SETTINGS=(
    "${COMMON_FUNCTION_SETTINGS[@]}"
    "ACTIVE_TRADE_ENDPOINT=options"
    "ENABLE_STOCK_TRADING=false"
    "ENABLE_OPTIONS_TRADING=true"
)
COMBINED_FUNCTION_SETTINGS=(
    "${COMMON_FUNCTION_SETTINGS[@]}"
    "ACTIVE_TRADE_ENDPOINT=both"
    "ENABLE_STOCK_TRADING=true"
    "ENABLE_OPTIONS_TRADING=true"
)

DASHBOARD_SETTINGS=(
    "ENVIRONMENT_NAME=$DEPLOY_ENV"
    "STOCK_BROKER=$STOCK_BROKER"
    "OPTIONS_BROKER=$OPTIONS_BROKER"
    "ORDER_BROKER=$ORDER_BROKER"
    "TASTYTRADE_IS_TEST=$TASTYTRADE_IS_TEST"
    "TASTYTRADE_DRY_RUN=$TASTYTRADE_DRY_RUN"
    "ENABLE_TASTYTRADE_OPTIONS=${ENABLE_TASTYTRADE_OPTIONS:-false}"
    "OPTIONS_ALLOW_FALLBACK_TO_ALPACA=${OPTIONS_ALLOW_FALLBACK_TO_ALPACA:-false}"
    "WEBHOOK_AUTH_TOKEN=$WEBHOOK_AUTH_TOKEN"
    "STOCK_WEBHOOK_AUTH_TOKEN=$STOCK_WEBHOOK_AUTH_TOKEN"
    "OPTIONS_WEBHOOK_AUTH_TOKEN=$OPTIONS_WEBHOOK_AUTH_TOKEN"
    "AZURE_RESOURCE_GROUP=$RESOURCE_GROUP"
    "AZURE_DASHBOARD_RESOURCE_GROUP=$DASHBOARD_RESOURCE_GROUP"
    "AZURE_DASHBOARD_PLAN_RESOURCE_GROUP=$DASHBOARD_PLAN_RESOURCE_GROUP"
    "AZURE_DASHBOARD_PLAN_NAME=$DASHBOARD_PLAN_NAME"
    "AZURE_STOCK_FUNCTION_APP_NAME=$STOCK_FUNCTION_APP_NAME"
    "AZURE_OPTIONS_FUNCTION_APP_NAME=$OPTIONS_FUNCTION_APP_NAME"
    "AZURE_STOCK_FUNCTION_BASE_URL=$STOCK_FUNCTION_BASE_URL"
    "AZURE_OPTIONS_FUNCTION_BASE_URL=$OPTIONS_FUNCTION_BASE_URL"
    "AZURE_DASHBOARD_APP_NAME=$DASHBOARD_APP_NAME"
    "DEPLOYED_GIT_BRANCH=$CURRENT_GIT_BRANCH"
    "DEPLOYED_GIT_SHA=$CURRENT_GIT_SHA"
    "DEPLOYED_AT_UTC=$DEPLOYED_AT_UTC"
    "SCM_DO_BUILD_DURING_DEPLOYMENT=true"
    "ENABLE_ORYX_BUILD=true"
)
if [ -n "$DASHBOARD_ACCESS_PASSWORD" ]; then
    DASHBOARD_SETTINGS+=("DASHBOARD_ACCESS_PASSWORD=$DASHBOARD_ACCESS_PASSWORD")
fi
if [ -n "$DASHBOARD_ACCESS_PASSWORD_HASH" ]; then
    DASHBOARD_SETTINGS+=("DASHBOARD_ACCESS_PASSWORD_HASH=$DASHBOARD_ACCESS_PASSWORD_HASH")
fi
if [ -n "$DASHBOARD_SESSION_SECRET" ]; then
    DASHBOARD_SETTINGS+=("DASHBOARD_SESSION_SECRET=$DASHBOARD_SESSION_SECRET")
fi
if [ -n "$AZURE_SUBSCRIPTION_ID" ]; then
    DASHBOARD_SETTINGS+=("AZURE_SUBSCRIPTION_ID=$AZURE_SUBSCRIPTION_ID")
fi

if [ "$DEPLOY_ENV" = "dev" ]; then
    DASHBOARD_SETTINGS+=(
        "AZURE_DEV_STOCK_FUNCTION_APP_NAME=$STOCK_FUNCTION_APP_NAME"
        "AZURE_DEV_OPTIONS_FUNCTION_APP_NAME=$OPTIONS_FUNCTION_APP_NAME"
        "AZURE_DEV_DASHBOARD_APP_NAME=$DASHBOARD_APP_NAME"
        "AZURE_DEV_DASHBOARD_RESOURCE_GROUP=$DASHBOARD_RESOURCE_GROUP"
        "AZURE_DEV_DASHBOARD_PLAN_RESOURCE_GROUP=$DASHBOARD_PLAN_RESOURCE_GROUP"
        "AZURE_DEV_DASHBOARD_PLAN_NAME=$DASHBOARD_PLAN_NAME"
        "AZURE_DEV_STOCK_FUNCTION_BASE_URL=$STOCK_FUNCTION_BASE_URL"
        "AZURE_DEV_OPTIONS_FUNCTION_BASE_URL=$OPTIONS_FUNCTION_BASE_URL"
        "AZURE_DEV_DASHBOARD_BASE_URL=$DASHBOARD_BASE_URL"
    )
else
    DASHBOARD_SETTINGS+=(
        "AZURE_PROD_STOCK_FUNCTION_APP_NAME=$STOCK_FUNCTION_APP_NAME"
        "AZURE_PROD_OPTIONS_FUNCTION_APP_NAME=$OPTIONS_FUNCTION_APP_NAME"
        "AZURE_PROD_DASHBOARD_APP_NAME=$DASHBOARD_APP_NAME"
        "AZURE_PROD_DASHBOARD_RESOURCE_GROUP=$DASHBOARD_RESOURCE_GROUP"
        "AZURE_PROD_DASHBOARD_PLAN_RESOURCE_GROUP=$DASHBOARD_PLAN_RESOURCE_GROUP"
        "AZURE_PROD_DASHBOARD_PLAN_NAME=$DASHBOARD_PLAN_NAME"
        "AZURE_PROD_STOCK_FUNCTION_BASE_URL=$STOCK_FUNCTION_BASE_URL"
        "AZURE_PROD_OPTIONS_FUNCTION_BASE_URL=$OPTIONS_FUNCTION_BASE_URL"
        "AZURE_PROD_DASHBOARD_BASE_URL=$DASHBOARD_BASE_URL"
    )
fi

if [ "$STOCK_FUNCTION_APP_NAME" = "$OPTIONS_FUNCTION_APP_NAME" ]; then
    echo "Pushing combined stock/options Function App settings..."
    az functionapp config appsettings set \
        --name "$STOCK_FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --settings "${COMBINED_FUNCTION_SETTINGS[@]}" \
        --output none
else
    echo "Pushing stock Function App settings..."
    az functionapp config appsettings set \
        --name "$STOCK_FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --settings "${STOCK_FUNCTION_SETTINGS[@]}" \
        --output none

    echo "Pushing options Function App settings..."
    az functionapp config appsettings set \
        --name "$OPTIONS_FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --settings "${OPTIONS_FUNCTION_SETTINGS[@]}" \
        --output none
fi

echo "Pushing dashboard Web App settings..."
az webapp config appsettings set \
    --name "$DASHBOARD_APP_NAME" \
    --resource-group "$DASHBOARD_RESOURCE_GROUP" \
    --settings "${DASHBOARD_SETTINGS[@]}" \
    --output none

az webapp config set \
    --name "$DASHBOARD_APP_NAME" \
    --resource-group "$DASHBOARD_RESOURCE_GROUP" \
    --linux-fx-version "PYTHON|$PYTHON_VERSION" \
    --startup-file "$DASHBOARD_STARTUP_COMMAND" \
    --output none

if [ "$STOCK_FUNCTION_APP_NAME" = "$OPTIONS_FUNCTION_APP_NAME" ]; then
    echo "Deploying function_app package to combined stock/options Function App..."
    (cd "$SCRIPT_DIR/function_app" && func azure functionapp publish "$STOCK_FUNCTION_APP_NAME" --python)
else
    echo "Deploying function_app package to stock Function App..."
    (cd "$SCRIPT_DIR/function_app" && func azure functionapp publish "$STOCK_FUNCTION_APP_NAME" --python)

    echo "Deploying function_app package to options Function App..."
    (cd "$SCRIPT_DIR/function_app" && func azure functionapp publish "$OPTIONS_FUNCTION_APP_NAME" --python)
fi

DASHBOARD_ZIP="$SCRIPT_DIR/.azure/dashboard-${DEPLOY_ENV}.zip"
create_dashboard_package "$DASHBOARD_ZIP"
echo "Deploying dashboard package..."
az webapp deploy \
    --resource-group "$DASHBOARD_RESOURCE_GROUP" \
    --name "$DASHBOARD_APP_NAME" \
    --src-path "$DASHBOARD_ZIP" \
    --type zip \
    --output none

echo
echo "Deployment complete."
echo
echo "Environment:"
echo "$DEPLOY_ENV"
echo
echo "Git branch:"
echo "$CURRENT_GIT_BRANCH"
echo
echo "Dashboard:"
echo "$DASHBOARD_BASE_URL"
echo
echo "Stock/share TradingView webhook:"
echo "${STOCK_FUNCTION_BASE_URL}/api/trade-stock?token=${STOCK_WEBHOOK_AUTH_TOKEN}"
echo
echo "Options TradingView webhook:"
echo "${OPTIONS_FUNCTION_BASE_URL}/api/trade-options?token=${OPTIONS_WEBHOOK_AUTH_TOKEN}"
echo
echo "Legacy webhook, if retained:"
echo "${STOCK_FUNCTION_BASE_URL}/api/trade?token=${WEBHOOK_AUTH_TOKEN}"
