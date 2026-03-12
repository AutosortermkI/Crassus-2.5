#!/usr/bin/env bash
set -euo pipefail

echo "===================================="
echo "  Crassus 2.5 — Azure Deployment"
echo "===================================="
echo

# ------------------------------------------------------------------
# Pre-flight
# ------------------------------------------------------------------
if ! command -v az &>/dev/null; then
    echo "[ERROR] Azure CLI is not installed."
    echo "        macOS:  brew install azure-cli"
    echo "        Linux:  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"
    exit 1
fi
echo "[OK] Azure CLI found."

if ! command -v func &>/dev/null; then
    echo "[ERROR] Azure Functions Core Tools not installed."
    echo "        macOS:  brew tap azure/functions && brew install azure-functions-core-tools@4"
    echo "        Linux:  npm install -g azure-functions-core-tools@4"
    exit 1
fi
echo "[OK] Azure Functions Core Tools found."

if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 is required for deployment packaging."
    exit 1
fi
echo "[OK] python3 found."

# ------------------------------------------------------------------
# Configuration defaults (can be overridden in .env)
# ------------------------------------------------------------------
DEFAULT_RESOURCE_GROUP="CRG"
DEFAULT_LOCATION="eastus"
DEFAULT_STORAGE_ACCOUNT="crassusstorage25"
DEFAULT_FUNCTION_APP_NAME="crassus-25"
DEFAULT_DASHBOARD_SKU="B1"
PYTHON_VERSION="3.11"
DASHBOARD_STARTUP_COMMAND='gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app'

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

load_env_var() {
    local key="$1"
    local val
    val=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-)
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
skip_dirs = {".git", ".venv", "__pycache__", ".pytest_cache"}
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
            if path.is_dir():
                continue
            if path.name in skip_names:
                continue
            if path.suffix in skip_suffixes:
                continue
            arcname = path.relative_to(root).as_posix()
            zf.write(path, arcname)
PY
}

if [ ! -f "$ENV_FILE" ]; then
    echo "[ERROR] .env file not found at $ENV_FILE"
    echo "        Run ./setup.sh or the dashboard first to create it."
    exit 1
fi

# ------------------------------------------------------------------
# Load credentials and Azure naming from .env
# ------------------------------------------------------------------
ALPACA_API_KEY=$(load_env_var "ALPACA_API_KEY")
ALPACA_SECRET_KEY=$(load_env_var "ALPACA_SECRET_KEY")
WEBHOOK_AUTH_TOKEN=$(load_env_var "WEBHOOK_AUTH_TOKEN")
RESOURCE_GROUP=$(load_env_var "AZURE_RESOURCE_GROUP")
LOCATION=$(load_env_var "AZURE_LOCATION")
STORAGE_ACCOUNT=$(load_env_var "AZURE_STORAGE_ACCOUNT")
FUNCTION_APP_NAME=$(load_env_var "AZURE_FUNCTION_APP_NAME")
FUNCTION_BASE_URL=$(load_env_var "AZURE_FUNCTION_BASE_URL")
SUBSCRIPTION_ID=$(load_env_var "AZURE_SUBSCRIPTION_ID")
DASHBOARD_APP_NAME=$(load_env_var "AZURE_DASHBOARD_APP_NAME")
DASHBOARD_PLAN_NAME=$(load_env_var "AZURE_DASHBOARD_PLAN_NAME")
DASHBOARD_SKU=$(load_env_var "AZURE_DASHBOARD_SKU")

RESOURCE_GROUP=${RESOURCE_GROUP:-$DEFAULT_RESOURCE_GROUP}
LOCATION=${LOCATION:-$DEFAULT_LOCATION}
STORAGE_ACCOUNT=${STORAGE_ACCOUNT:-$DEFAULT_STORAGE_ACCOUNT}
FUNCTION_APP_NAME=${FUNCTION_APP_NAME:-$DEFAULT_FUNCTION_APP_NAME}
FUNCTION_BASE_URL=${FUNCTION_BASE_URL:-"https://${FUNCTION_APP_NAME}.azurewebsites.net"}
DASHBOARD_APP_NAME=${DASHBOARD_APP_NAME:-"${FUNCTION_APP_NAME}-dashboard"}
DASHBOARD_PLAN_NAME=${DASHBOARD_PLAN_NAME:-"${DASHBOARD_APP_NAME}-plan"}
DASHBOARD_SKU=${DASHBOARD_SKU:-$DEFAULT_DASHBOARD_SKU}

if [ -z "$ALPACA_API_KEY" ] || [ -z "$ALPACA_SECRET_KEY" ]; then
    echo "[ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env"
    exit 1
fi

if [ -z "$WEBHOOK_AUTH_TOKEN" ]; then
    WEBHOOK_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    echo "[INFO] Auto-generated WEBHOOK_AUTH_TOKEN: $WEBHOOK_AUTH_TOKEN"
    upsert_env_var "WEBHOOK_AUTH_TOKEN" "$WEBHOOK_AUTH_TOKEN"
    echo "[OK] Token saved to .env"
fi

upsert_env_var "AZURE_RESOURCE_GROUP" "$RESOURCE_GROUP"
upsert_env_var "AZURE_LOCATION" "$LOCATION"
upsert_env_var "AZURE_STORAGE_ACCOUNT" "$STORAGE_ACCOUNT"
upsert_env_var "AZURE_FUNCTION_APP_NAME" "$FUNCTION_APP_NAME"
upsert_env_var "AZURE_DASHBOARD_APP_NAME" "$DASHBOARD_APP_NAME"
upsert_env_var "AZURE_DASHBOARD_PLAN_NAME" "$DASHBOARD_PLAN_NAME"
upsert_env_var "AZURE_DASHBOARD_SKU" "$DASHBOARD_SKU"

if [ -z "$(load_env_var "DASHBOARD_ACCESS_PASSWORD")" ] && [ -z "$(load_env_var "DASHBOARD_ACCESS_PASSWORD_HASH")" ]; then
    echo "[WARN] No dashboard access password is configured."
    echo "       Set DASHBOARD_ACCESS_PASSWORD or DASHBOARD_ACCESS_PASSWORD_HASH in .env before sharing the hosted dashboard."
fi

echo "[OK] Credentials loaded from .env"

# ------------------------------------------------------------------
# Login check
# ------------------------------------------------------------------
echo
echo "Checking Azure login..."
if ! az account show &>/dev/null; then
    echo "You need to log in to Azure."
    az login
fi
ACTIVE_SUBSCRIPTION_ID="$(az account show --query 'id' -o tsv)"
ACTIVE_SUBSCRIPTION_NAME="$(az account show --query 'name' -o tsv)"
echo "[OK] Logged in to Azure."
echo "     Subscription: ${ACTIVE_SUBSCRIPTION_NAME}"
echo

if [ -z "$SUBSCRIPTION_ID" ]; then
    SUBSCRIPTION_ID="$ACTIVE_SUBSCRIPTION_ID"
    upsert_env_var "AZURE_SUBSCRIPTION_ID" "$SUBSCRIPTION_ID"
fi

# ------------------------------------------------------------------
# Shared Azure resources
# ------------------------------------------------------------------
echo "Ensuring resource group \"$RESOURCE_GROUP\" exists..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none >/dev/null
echo "[OK] Resource group ready."

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
    echo "[OK] Storage account created."
fi

if az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    echo "[OK] Function App \"$FUNCTION_APP_NAME\" already exists."
else
    echo "Creating Function App \"$FUNCTION_APP_NAME\"..."
    az functionapp create \
        --resource-group "$RESOURCE_GROUP" \
        --consumption-plan-location "$LOCATION" \
        --runtime python \
        --runtime-version "$PYTHON_VERSION" \
        --functions-version 4 \
        --name "$FUNCTION_APP_NAME" \
        --os-type linux \
        --storage-account "$STORAGE_ACCOUNT" \
        --output none
    echo "[OK] Function App created."
fi

if az appservice plan show --name "$DASHBOARD_PLAN_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    echo "[OK] App Service plan \"$DASHBOARD_PLAN_NAME\" already exists."
else
    echo "Creating App Service plan \"$DASHBOARD_PLAN_NAME\"..."
    az appservice plan create \
        --name "$DASHBOARD_PLAN_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --location "$LOCATION" \
        --sku "$DASHBOARD_SKU" \
        --is-linux \
        --output none
    echo "[OK] App Service plan created."
fi

if az webapp show --name "$DASHBOARD_APP_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
    echo "[OK] Dashboard Web App \"$DASHBOARD_APP_NAME\" already exists."
else
    echo "Creating Dashboard Web App \"$DASHBOARD_APP_NAME\"..."
    az webapp create \
        --resource-group "$RESOURCE_GROUP" \
        --plan "$DASHBOARD_PLAN_NAME" \
        --name "$DASHBOARD_APP_NAME" \
        --runtime "PYTHON|$PYTHON_VERSION" \
        --output none
    echo "[OK] Dashboard Web App created."
fi

# ------------------------------------------------------------------
# Build settings from .env
# ------------------------------------------------------------------
SETTINGS=()
while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    SETTINGS+=("$line")
done < "$ENV_FILE"

echo
echo "Pushing Function App settings from .env..."
az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --settings "${SETTINGS[@]}" \
    --output none
echo "[OK] Function App settings configured."

echo "Pushing Dashboard Web App settings from .env..."
az webapp config appsettings set \
    --name "$DASHBOARD_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --settings "${SETTINGS[@]}" \
        "AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID" \
        "SCM_DO_BUILD_DURING_DEPLOYMENT=true" \
        "ENABLE_ORYX_BUILD=true" \
    --output none
echo "[OK] Dashboard app settings configured."

echo "Configuring Dashboard startup command..."
az webapp config set \
    --name "$DASHBOARD_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --startup-file "$DASHBOARD_STARTUP_COMMAND" \
    --output none >/dev/null
az webapp config set \
    --name "$DASHBOARD_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --always-on true \
    --output none >/dev/null || true
echo "[OK] Dashboard startup configured."

# ------------------------------------------------------------------
# Dashboard managed identity so hosted config saves can update Azure
# ------------------------------------------------------------------
echo
echo "Enabling managed identity on the Dashboard Web App..."
DASHBOARD_PRINCIPAL_ID="$(
    az webapp identity assign \
        --name "$DASHBOARD_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query principalId \
        -o tsv
)"
FUNCTION_RESOURCE_ID="$(az functionapp show --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
DASHBOARD_RESOURCE_ID="$(az webapp show --name "$DASHBOARD_APP_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"

for resource_id in "$FUNCTION_RESOURCE_ID" "$DASHBOARD_RESOURCE_ID"; do
    az role assignment create \
        --assignee-object-id "$DASHBOARD_PRINCIPAL_ID" \
        --assignee-principal-type ServicePrincipal \
        --role "Contributor" \
        --scope "$resource_id" \
        --output none >/dev/null 2>&1 || true
done
echo "[OK] Managed identity can update hosted app settings."

# ------------------------------------------------------------------
# Deploy Function code
# ------------------------------------------------------------------
echo
echo "Deploying Function App code..."
pushd "$SCRIPT_DIR/function_app" >/dev/null

if [ ! -f "local.settings.json" ]; then
    cat > local.settings.json << 'SETTINGSEOF'
{
  "IsEncrypted": false,
  "Values": {
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AzureWebJobsStorage": "UseDevelopmentStorage=true"
  }
}
SETTINGSEOF
    echo "[OK] Created local.settings.json for deployment."
fi

func azure functionapp publish "$FUNCTION_APP_NAME" --python
popd >/dev/null
echo "[OK] Function App deployment complete."

# ------------------------------------------------------------------
# Deploy Dashboard code
# ------------------------------------------------------------------
echo
echo "Packaging dashboard deployment..."
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
DASHBOARD_PACKAGE="$TMP_DIR/crassus-dashboard.zip"
create_dashboard_package "$DASHBOARD_PACKAGE"
echo "[OK] Dashboard package created."

echo "Deploying Dashboard Web App code..."
az webapp deploy \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DASHBOARD_APP_NAME" \
    --src-path "$DASHBOARD_PACKAGE" \
    --type zip \
    --output none
echo "[OK] Dashboard Web App deployment complete."

# ------------------------------------------------------------------
# Done
# ------------------------------------------------------------------
FUNCTION_BASE_URL=${FUNCTION_BASE_URL%/}
if [[ "$FUNCTION_BASE_URL" == */api/trade ]]; then
    WEBHOOK_ENDPOINT="$FUNCTION_BASE_URL"
else
    WEBHOOK_ENDPOINT="${FUNCTION_BASE_URL}/api/trade"
fi
DASHBOARD_URL="https://${DASHBOARD_APP_NAME}.azurewebsites.net"

echo
echo "===================================="
echo "  Deployment complete!"
echo "===================================="
echo
echo "Shared dashboard:"
echo "  $DASHBOARD_URL"
echo
echo "TradingView webhook endpoint:"
echo "  $WEBHOOK_ENDPOINT"
echo
echo "Webhook auth:"
echo "  X-Webhook-Token: $WEBHOOK_AUTH_TOKEN"
echo
echo "Partners can use the dashboard at:"
echo "  $DASHBOARD_URL"
echo
