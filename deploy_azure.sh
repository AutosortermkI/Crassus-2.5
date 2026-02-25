#!/usr/bin/env bash
set -e

echo "===================================="
echo "  Crassus 2.5 — Azure Deployment"
echo "===================================="
echo

# ------------------------------------------------------------------
# Pre-flight: Azure CLI
# ------------------------------------------------------------------
if ! command -v az &>/dev/null; then
    echo "[ERROR] Azure CLI is not installed."
    echo "        macOS:  brew install azure-cli"
    echo "        Linux:  curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash"
    exit 1
fi
echo "[OK] Azure CLI found."

# ------------------------------------------------------------------
# Pre-flight: Azure Functions Core Tools
# ------------------------------------------------------------------
if ! command -v func &>/dev/null; then
    echo "[ERROR] Azure Functions Core Tools not installed."
    echo "        macOS:  brew tap azure/functions && brew install azure-functions-core-tools@4"
    echo "        Linux:  npm install -g azure-functions-core-tools@4"
    exit 1
fi
echo "[OK] Azure Functions Core Tools found."

# ------------------------------------------------------------------
# Configuration — edit these if you want different names
# ------------------------------------------------------------------
RESOURCE_GROUP="CRG"
LOCATION="eastus"
STORAGE_ACCOUNT="crassusstorage25"
FUNCTION_APP_NAME="crassus-25"
PYTHON_VERSION="3.11"

# ------------------------------------------------------------------
# Load credentials from .env
# ------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "[ERROR] .env file not found at $ENV_FILE"
    echo "        Run ./setup.sh or the dashboard first to create it."
    exit 1
fi

# Read key=value pairs from .env (skip comments and blanks)
load_env_var() {
    local val
    val=$(grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d'=' -f2-)
    echo "$val"
}

ALPACA_API_KEY=$(load_env_var "ALPACA_API_KEY")
ALPACA_SECRET_KEY=$(load_env_var "ALPACA_SECRET_KEY")
WEBHOOK_AUTH_TOKEN=$(load_env_var "WEBHOOK_AUTH_TOKEN")

if [ -z "$ALPACA_API_KEY" ] || [ -z "$ALPACA_SECRET_KEY" ]; then
    echo "[ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env"
    exit 1
fi

if [ -z "$WEBHOOK_AUTH_TOKEN" ]; then
    WEBHOOK_AUTH_TOKEN=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    echo "[INFO] Auto-generated WEBHOOK_AUTH_TOKEN: $WEBHOOK_AUTH_TOKEN"
    # Write it back to .env so local and Azure stay in sync
    if grep -q "^WEBHOOK_AUTH_TOKEN=" "$ENV_FILE" 2>/dev/null; then
        sed -i.bak "s/^WEBHOOK_AUTH_TOKEN=.*/WEBHOOK_AUTH_TOKEN=$WEBHOOK_AUTH_TOKEN/" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
    else
        echo "WEBHOOK_AUTH_TOKEN=$WEBHOOK_AUTH_TOKEN" >> "$ENV_FILE"
    fi
    echo "[OK] Token saved to .env"
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
echo "[OK] Logged in to Azure."
echo "     Subscription: $(az account show --query 'name' -o tsv)"
echo

# ------------------------------------------------------------------
# Create resource group (if it doesn't exist)
# ------------------------------------------------------------------
echo "Ensuring resource group \"$RESOURCE_GROUP\" exists..."
az group create \
    --name "$RESOURCE_GROUP" \
    --location "$LOCATION" \
    --output none 2>/dev/null || true
echo "[OK] Resource group ready."

# ------------------------------------------------------------------
# Create storage account (Function Apps require one)
# ------------------------------------------------------------------
echo "Creating storage account \"$STORAGE_ACCOUNT\"..."
if ! az storage account create \
    --name "$STORAGE_ACCOUNT" \
    --location "$LOCATION" \
    --resource-group "$RESOURCE_GROUP" \
    --sku Standard_LRS \
    --output none; then
    echo "[ERROR] Failed to create storage account."
    echo "        The name must be globally unique, all lowercase, 3-24 chars."
    echo "        Try changing STORAGE_ACCOUNT in this script."
    exit 1
fi
echo "[OK] Storage account created."

# ------------------------------------------------------------------
# Create Function App
# ------------------------------------------------------------------
echo "Creating Function App \"$FUNCTION_APP_NAME\"..."
if ! az functionapp create \
    --resource-group "$RESOURCE_GROUP" \
    --consumption-plan-location "$LOCATION" \
    --runtime python \
    --runtime-version "$PYTHON_VERSION" \
    --functions-version 4 \
    --name "$FUNCTION_APP_NAME" \
    --os-type linux \
    --storage-account "$STORAGE_ACCOUNT" \
    --output none; then
    echo "[ERROR] Failed to create Function App."
    echo "        The name must be globally unique."
    echo "        Try changing FUNCTION_APP_NAME in this script."
    exit 1
fi
echo "[OK] Function App created."

# ------------------------------------------------------------------
# Set application settings from .env (no hardcoded secrets)
# ------------------------------------------------------------------
echo
echo "Pushing application settings from .env..."

# Build settings array from all non-comment lines in .env
SETTINGS=""
while IFS= read -r line; do
    line=$(echo "$line" | xargs)  # trim whitespace
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    SETTINGS="$SETTINGS $line"
done < "$ENV_FILE"

az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP" \
    --settings $SETTINGS \
    --output none
echo "[OK] Application settings configured."

# ------------------------------------------------------------------
# Deploy the function code
# ------------------------------------------------------------------
echo
echo "Deploying function code..."
pushd "$SCRIPT_DIR/function_app" >/dev/null

# Ensure local.settings.json exists (func CLI needs it for runtime detection)
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
echo "[OK] Deployment complete."

# ------------------------------------------------------------------
# Done — show the endpoint
# ------------------------------------------------------------------
ENDPOINT="https://${FUNCTION_APP_NAME}.azurewebsites.net/api/trade"
echo
echo "===================================="
echo "  Deployment complete!"
echo "===================================="
echo
echo "Your webhook endpoint:"
echo "  $ENDPOINT"
echo
echo "TradingView webhook setup:"
echo "  URL:    $ENDPOINT"
echo "  Header: X-Webhook-Token: $WEBHOOK_AUTH_TOKEN"
echo
echo "Test it with:"
echo "  curl -X POST $ENDPOINT \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -H 'X-Webhook-Token: $WEBHOOK_AUTH_TOKEN' \\"
echo "    -d '{\"content\": \"**New Buy Signal:**\\nAAPL 5 Min Candle\\nStrategy: bollinger_mean_reversion\\nMode: stock\\nPrice: 189.50\"}'"
echo
