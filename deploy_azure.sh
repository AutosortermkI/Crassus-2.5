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

if ! command -v curl &>/dev/null; then
    echo "[ERROR] curl is required for hosted dashboard health checks."
    exit 1
fi
echo "[OK] curl found."

# ------------------------------------------------------------------
# Configuration defaults (can be overridden in .env)
# ------------------------------------------------------------------
DEFAULT_RESOURCE_GROUP="CRG"
DEFAULT_LOCATION="eastus"
DEFAULT_STORAGE_ACCOUNT="crassusstorage25"
DEFAULT_FUNCTION_APP_NAME="crassus-25"
DEFAULT_DASHBOARD_SKU="F1"
DASHBOARD_FALLBACK_LOCATIONS="eastus westus2 centralus westus northeurope westeurope"
PYTHON_VERSION="3.11"
DASHBOARD_STARTUP_COMMAND='gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app'
DASHBOARD_DEPLOYMENT_POLL_SECONDS=5
DASHBOARD_DEPLOYMENT_WAIT_ATTEMPTS=180
DASHBOARD_HEALTH_WAIT_ATTEMPTS=120

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

get_latest_dashboard_deployment_id() {
    local deployment_id
    deployment_id="$(
        az webapp log deployment list \
            --name "$DASHBOARD_APP_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --query "sort_by(@, &received_time)[-1].id" \
            -o tsv 2>/dev/null || true
    )"
    if [ "$deployment_id" = "None" ]; then
        deployment_id=""
    fi
    printf '%s' "$deployment_id"
}

get_dashboard_deployment_field() {
    local deployment_id="$1"
    local field="$2"
    local value
    value="$(
        az webapp log deployment list \
            --name "$DASHBOARD_APP_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --query "[?id=='$deployment_id'] | [0].$field" \
            -o tsv 2>/dev/null || true
    )"
    if [ "$value" = "None" ]; then
        value=""
    fi
    printf '%s' "$value"
}

wait_for_dashboard_deployment() {
    local previous_id="$1"
    local deployment_id=""
    local attempt
    local complete
    local status

    echo "Waiting for Azure to register the dashboard deployment..."
    for ((attempt=1; attempt<=DASHBOARD_DEPLOYMENT_WAIT_ATTEMPTS; attempt++)); do
        deployment_id="$(get_latest_dashboard_deployment_id)"
        if [ -n "$deployment_id" ] && [ "$deployment_id" != "$previous_id" ]; then
            echo "[OK] Azure accepted dashboard deployment: $deployment_id"
            break
        fi
        sleep "$DASHBOARD_DEPLOYMENT_POLL_SECONDS"
    done

    if [ -z "$deployment_id" ] || [ "$deployment_id" = "$previous_id" ]; then
        echo "[ERROR] Timed out waiting for Azure to register dashboard deployment."
        exit 1
    fi

    echo "Waiting for Azure deployment record to complete..."
    for ((attempt=1; attempt<=DASHBOARD_DEPLOYMENT_WAIT_ATTEMPTS; attempt++)); do
        complete="$(get_dashboard_deployment_field "$deployment_id" complete)"
        status="$(get_dashboard_deployment_field "$deployment_id" status)"
        if [ "$complete" = "true" ]; then
            if [ "$status" = "4" ]; then
                echo "[OK] Dashboard deployment record completed successfully."
                return 0
            fi
            echo "[ERROR] Dashboard deployment failed with Azure status ${status:-unknown}."
            exit 1
        fi
        sleep "$DASHBOARD_DEPLOYMENT_POLL_SECONDS"
    done

    echo "[ERROR] Timed out waiting for dashboard deployment to finish."
    exit 1
}

wait_for_dashboard_health() {
    local url="$1"
    local attempt
    local status_code

    echo "Waiting for dashboard to answer at $url ..."
    for ((attempt=1; attempt<=DASHBOARD_HEALTH_WAIT_ATTEMPTS; attempt++)); do
        status_code="$(curl -sS -o /dev/null -w "%{http_code}" --max-time 15 "$url" || true)"
        if [ "$status_code" = "200" ]; then
            echo "[OK] Dashboard responded with HTTP 200."
            return 0
        fi
        sleep "$DASHBOARD_DEPLOYMENT_POLL_SECONDS"
    done

    echo "[ERROR] Dashboard did not become healthy at $url."
    exit 1
}

is_secret_key() {
    case "$1" in
        ALPACA_API_KEY|ALPACA_SECRET_KEY|WEBHOOK_AUTH_TOKEN|DASHBOARD_ACCESS_PASSWORD|DASHBOARD_ACCESS_PASSWORD_HASH|DASHBOARD_SESSION_SECRET)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

build_dashboard_password_hash() {
    python3 - "$1" <<'PY'
import hashlib
import secrets
import sys

password = sys.argv[1]
salt = secrets.token_hex(16)
iterations = 600000
digest = hashlib.pbkdf2_hmac(
    "sha256",
    password.encode("utf-8"),
    salt.encode("utf-8"),
    iterations,
).hex()
print(f"pbkdf2:sha256:{iterations}${salt}${digest}")
PY
}

key_vault_secret_name() {
    python3 - "$KEY_VAULT_SECRET_PREFIX" "$1" <<'PY'
import sys

def sanitize(value: str, fallback: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "-" for ch in value.strip().lower())
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    normalized = normalized.strip("-")
    return normalized or fallback

prefix = sanitize(sys.argv[1], "crassus")
key = sanitize(sys.argv[2], "secret")
print(f"{prefix}-{key}")
PY
}

key_vault_reference() {
    local secret_name
    secret_name="$(key_vault_secret_name "$1")"
    printf '@Microsoft.KeyVault(SecretUri=https://%s.vault.azure.net/secrets/%s)' "$KEY_VAULT_NAME" "$secret_name"
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
USE_KEY_VAULT=$(load_env_var "AZURE_USE_KEY_VAULT")
KEY_VAULT_NAME=$(load_env_var "AZURE_KEY_VAULT_NAME")
KEY_VAULT_SECRET_PREFIX=$(load_env_var "AZURE_KEY_VAULT_SECRET_PREFIX")
DASHBOARD_SESSION_SECRET=$(load_env_var "DASHBOARD_SESSION_SECRET")
DASHBOARD_ACCESS_PASSWORD=$(load_env_var "DASHBOARD_ACCESS_PASSWORD")
DASHBOARD_ACCESS_PASSWORD_HASH=$(load_env_var "DASHBOARD_ACCESS_PASSWORD_HASH")

RESOURCE_GROUP=${RESOURCE_GROUP:-$DEFAULT_RESOURCE_GROUP}
LOCATION=${LOCATION:-$DEFAULT_LOCATION}
STORAGE_ACCOUNT=${STORAGE_ACCOUNT:-$DEFAULT_STORAGE_ACCOUNT}
FUNCTION_APP_NAME=${FUNCTION_APP_NAME:-$DEFAULT_FUNCTION_APP_NAME}
FUNCTION_BASE_URL=${FUNCTION_BASE_URL:-"https://${FUNCTION_APP_NAME}.azurewebsites.net"}
DASHBOARD_APP_NAME=${DASHBOARD_APP_NAME:-"${FUNCTION_APP_NAME}-dashboard"}
DASHBOARD_PLAN_NAME=${DASHBOARD_PLAN_NAME:-"${DASHBOARD_APP_NAME}-plan"}
DASHBOARD_SKU=${DASHBOARD_SKU:-$DEFAULT_DASHBOARD_SKU}
USE_KEY_VAULT=$(printf '%s' "${USE_KEY_VAULT:-true}" | tr '[:upper:]' '[:lower:]')
if [[ "$USE_KEY_VAULT" != "false" && "$USE_KEY_VAULT" != "0" && "$USE_KEY_VAULT" != "no" ]]; then
    USE_KEY_VAULT="true"
else
    USE_KEY_VAULT="false"
fi
if [ "$USE_KEY_VAULT" = "true" ] && [ -z "$KEY_VAULT_NAME" ]; then
    KEY_VAULT_NAME="${STORAGE_ACCOUNT:0:22}kv"
fi
KEY_VAULT_SECRET_PREFIX=${KEY_VAULT_SECRET_PREFIX:-$FUNCTION_APP_NAME}

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

if [ -z "$DASHBOARD_SESSION_SECRET" ]; then
    DASHBOARD_SESSION_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "[INFO] Auto-generated DASHBOARD_SESSION_SECRET."
    upsert_env_var "DASHBOARD_SESSION_SECRET" "$DASHBOARD_SESSION_SECRET"
    echo "[OK] Session secret saved to .env"
fi

upsert_env_var "AZURE_RESOURCE_GROUP" "$RESOURCE_GROUP"
upsert_env_var "AZURE_LOCATION" "$LOCATION"
upsert_env_var "AZURE_STORAGE_ACCOUNT" "$STORAGE_ACCOUNT"
upsert_env_var "AZURE_FUNCTION_APP_NAME" "$FUNCTION_APP_NAME"
upsert_env_var "AZURE_DASHBOARD_APP_NAME" "$DASHBOARD_APP_NAME"
upsert_env_var "AZURE_DASHBOARD_PLAN_NAME" "$DASHBOARD_PLAN_NAME"
upsert_env_var "AZURE_DASHBOARD_SKU" "$DASHBOARD_SKU"
upsert_env_var "AZURE_USE_KEY_VAULT" "$USE_KEY_VAULT"
if [ "$USE_KEY_VAULT" = "true" ]; then
    upsert_env_var "AZURE_KEY_VAULT_NAME" "$KEY_VAULT_NAME"
    upsert_env_var "AZURE_KEY_VAULT_SECRET_PREFIX" "$KEY_VAULT_SECRET_PREFIX"
fi

if [ -z "$DASHBOARD_ACCESS_PASSWORD" ] && [ -z "$DASHBOARD_ACCESS_PASSWORD_HASH" ]; then
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

if [ "$USE_KEY_VAULT" = "true" ]; then
    echo "Ensuring Microsoft.KeyVault provider is registered..."
    az provider register --namespace Microsoft.KeyVault --wait --output none >/dev/null
    echo "[OK] Microsoft.KeyVault provider is registered."
fi

# ------------------------------------------------------------------
# Ensure required resource providers are registered
# ------------------------------------------------------------------
for _ns in Microsoft.Web Microsoft.Storage Microsoft.Compute; do
    _state=$(az provider show --namespace "$_ns" --query registrationState -o tsv 2>/dev/null || echo "NotRegistered")
    if [ "$_state" = "Registered" ]; then
        echo "[OK] $_ns provider already registered."
    else
        echo "Registering $_ns provider (this may take a minute)..."
        az provider register --namespace "$_ns" --wait --output none >/dev/null
        echo "[OK] $_ns provider registered."
    fi
done

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
    # Build the ordered list of regions to try: configured LOCATION first,
    # then every fallback location that isn't a duplicate.
    _plan_regions="$LOCATION"
    for _fb in $DASHBOARD_FALLBACK_LOCATIONS; do
        case " $_plan_regions " in
            *" $_fb "*) ;;          # already in the list
            *) _plan_regions="$_plan_regions $_fb" ;;
        esac
    done

    _plan_created=false
    for _region in $_plan_regions; do
        echo "Creating App Service plan \"$DASHBOARD_PLAN_NAME\" in $_region..."
        _plan_err=$(az appservice plan create \
            --name "$DASHBOARD_PLAN_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$_region" \
            --sku "$DASHBOARD_SKU" \
            --is-linux \
            --output none 2>&1) && {
            echo "[OK] App Service plan created in $_region."
            if [ "$_region" != "$LOCATION" ]; then
                echo "[INFO] Dashboard region differs from primary ($LOCATION). Saving AZURE_DASHBOARD_LOCATION=$_region to .env"
                upsert_env_var "AZURE_DASHBOARD_LOCATION" "$_region"
            fi
            _plan_created=true
            break
        }
        echo "[WARN] $_region: quota unavailable — $_plan_err"
    done

    if [ "$_plan_created" = false ]; then
        echo
        echo "[ERROR] Could not create App Service plan in any region."
        echo "        Tried: $_plan_regions"
        echo "        Request a quota increase at https://aka.ms/ProdportalCRP/#blade/Microsoft_Azure_Capacity/UsageAndQuota.ReactView"
        exit 1
    fi
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
# Optional Azure Key Vault for hosted secrets
# ------------------------------------------------------------------
if [ "$USE_KEY_VAULT" = "true" ]; then
    if az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$RESOURCE_GROUP" --output none >/dev/null 2>&1; then
        echo "[OK] Key Vault \"$KEY_VAULT_NAME\" already exists."
    else
        echo "Creating Key Vault \"$KEY_VAULT_NAME\"..."
        az keyvault create \
            --name "$KEY_VAULT_NAME" \
            --resource-group "$RESOURCE_GROUP" \
            --location "$LOCATION" \
            --output none >/dev/null
        echo "[OK] Key Vault created."
    fi
    KEY_VAULT_RESOURCE_ID="$(az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$RESOURCE_GROUP" --query id -o tsv)"
    KEY_VAULT_USES_RBAC="$(az keyvault show --name "$KEY_VAULT_NAME" --resource-group "$RESOURCE_GROUP" --query properties.enableRbacAuthorization -o tsv)"
    CURRENT_PRINCIPAL_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
fi

# ------------------------------------------------------------------
# Managed identities for hosted config sync and Key Vault references
# ------------------------------------------------------------------
echo
echo "Enabling managed identities on the shared Azure apps..."
FUNCTION_PRINCIPAL_ID="$(
    az functionapp identity assign \
        --name "$FUNCTION_APP_NAME" \
        --resource-group "$RESOURCE_GROUP" \
        --query principalId \
        -o tsv
)"
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

if [ "$USE_KEY_VAULT" = "true" ]; then
    sleep 5
    if [ "$KEY_VAULT_USES_RBAC" = "true" ]; then
        if [ -n "$CURRENT_PRINCIPAL_ID" ]; then
            az role assignment create \
                --assignee-object-id "$CURRENT_PRINCIPAL_ID" \
                --assignee-principal-type User \
                --role "Key Vault Secrets Officer" \
                --scope "$KEY_VAULT_RESOURCE_ID" \
                --output none >/dev/null 2>&1 || true
        fi
        az role assignment create \
            --assignee-object-id "$FUNCTION_PRINCIPAL_ID" \
            --assignee-principal-type ServicePrincipal \
            --role "Key Vault Secrets User" \
            --scope "$KEY_VAULT_RESOURCE_ID" \
            --output none >/dev/null 2>&1 || true
        az role assignment create \
            --assignee-object-id "$DASHBOARD_PRINCIPAL_ID" \
            --assignee-principal-type ServicePrincipal \
            --role "Key Vault Secrets Officer" \
            --scope "$KEY_VAULT_RESOURCE_ID" \
            --output none >/dev/null 2>&1 || true
    else
        az keyvault set-policy \
            --name "$KEY_VAULT_NAME" \
            --object-id "$FUNCTION_PRINCIPAL_ID" \
            --secret-permissions get list \
            --output none >/dev/null
        az keyvault set-policy \
            --name "$KEY_VAULT_NAME" \
            --object-id "$DASHBOARD_PRINCIPAL_ID" \
            --secret-permissions get list set \
            --output none >/dev/null
    fi
    echo "[OK] Managed identities can read/write hosted secrets through Key Vault."
else
    echo "[OK] Managed identities can update hosted app settings."
fi

# ------------------------------------------------------------------
# Build app settings payloads from .env
# ------------------------------------------------------------------
SETTINGS=()
SECRET_SETTINGS=()
AZURE_DASHBOARD_ACCESS_PASSWORD_HASH="$DASHBOARD_ACCESS_PASSWORD_HASH"
if [ -n "$DASHBOARD_ACCESS_PASSWORD" ] && [ -z "$AZURE_DASHBOARD_ACCESS_PASSWORD_HASH" ]; then
    AZURE_DASHBOARD_ACCESS_PASSWORD_HASH="$(build_dashboard_password_hash "$DASHBOARD_ACCESS_PASSWORD")"
    echo "[OK] Dashboard access password will be stored in Azure as a hash."
fi

while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"

    if [ "$key" = "DASHBOARD_ACCESS_PASSWORD" ] || [ "$key" = "DASHBOARD_ACCESS_PASSWORD_HASH" ]; then
        continue
    fi

    if [ "$USE_KEY_VAULT" = "true" ] && is_secret_key "$key"; then
        if [ -n "$value" ]; then
            SECRET_SETTINGS+=("$key=$value")
            SETTINGS+=("$key=$(key_vault_reference "$key")")
        else
            SETTINGS+=("$key=")
        fi
        continue
    fi

    SETTINGS+=("$line")
done < "$ENV_FILE"

SETTINGS+=("DASHBOARD_ACCESS_PASSWORD=")
if [ -n "$AZURE_DASHBOARD_ACCESS_PASSWORD_HASH" ]; then
    if [ "$USE_KEY_VAULT" = "true" ]; then
        SECRET_SETTINGS+=("DASHBOARD_ACCESS_PASSWORD_HASH=$AZURE_DASHBOARD_ACCESS_PASSWORD_HASH")
        SETTINGS+=("DASHBOARD_ACCESS_PASSWORD_HASH=$(key_vault_reference 'DASHBOARD_ACCESS_PASSWORD_HASH')")
    else
        SETTINGS+=("DASHBOARD_ACCESS_PASSWORD_HASH=$AZURE_DASHBOARD_ACCESS_PASSWORD_HASH")
    fi
else
    SETTINGS+=("DASHBOARD_ACCESS_PASSWORD_HASH=")
fi

if [ "$USE_KEY_VAULT" = "true" ] && [ "${#SECRET_SETTINGS[@]}" -gt 0 ]; then
    echo
    echo "Syncing hosted secrets to Azure Key Vault..."
    for entry in "${SECRET_SETTINGS[@]}"; do
        key="${entry%%=*}"
        value="${entry#*=}"
        secret_name="$(key_vault_secret_name "$key")"
        secret_saved="false"
        for attempt in 1 2 3 4 5 6; do
            if az keyvault secret set \
                --vault-name "$KEY_VAULT_NAME" \
                --name "$secret_name" \
                --value "$value" \
                --output none >/dev/null 2>&1; then
                secret_saved="true"
                break
            fi
            sleep 10
        done
        if [ "$secret_saved" != "true" ]; then
            echo "[ERROR] Failed to store secret $secret_name in Azure Key Vault."
            exit 1
        fi
    done
    echo "[OK] Azure Key Vault secrets configured."
fi

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
        "WEBSITES_PORT=8000" \
        "WEBSITE_WARMUP_PATH=/login" \
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
PREVIOUS_DASHBOARD_DEPLOYMENT_ID="$(get_latest_dashboard_deployment_id)"
az webapp deploy \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DASHBOARD_APP_NAME" \
    --src-path "$DASHBOARD_PACKAGE" \
    --type zip \
    --async true \
    --track-status false \
    --output none
DASHBOARD_URL="https://${DASHBOARD_APP_NAME}.azurewebsites.net"
wait_for_dashboard_deployment "$PREVIOUS_DASHBOARD_DEPLOYMENT_ID"
wait_for_dashboard_health "${DASHBOARD_URL}/login"
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
