@echo off
setlocal enabledelayedexpansion

echo ====================================
echo   Crassus 2.5 — Azure Deployment
echo ====================================
echo.

REM ------------------------------------------------------------------
REM Pre-flight: Azure CLI
REM ------------------------------------------------------------------
where az >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Azure CLI is not installed.
    echo         Download from: https://aka.ms/installazurecliwindows
    exit /b 1
)
echo [OK] Azure CLI found.

REM ------------------------------------------------------------------
REM Pre-flight: Azure Functions Core Tools
REM ------------------------------------------------------------------
where func >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Azure Functions Core Tools not installed.
    echo         Run: npm install -g azure-functions-core-tools@4 --unsafe-perm true
    exit /b 1
)
echo [OK] Azure Functions Core Tools found.

REM ------------------------------------------------------------------
REM Configuration — edit these if you want different names
REM ------------------------------------------------------------------
set RESOURCE_GROUP=CRG
set LOCATION=eastus
set STORAGE_ACCOUNT=crassusstorage25
set FUNCTION_APP_NAME=crassus-25
set PYTHON_VERSION=3.11

REM ------------------------------------------------------------------
REM Login check
REM ------------------------------------------------------------------
echo.
echo Checking Azure login...
az account show >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo You need to log in to Azure.
    az login
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Azure login failed.
        exit /b 1
    )
)
echo [OK] Logged in to Azure.

REM Show which subscription is active
for /f "delims=" %%s in ('az account show --query "name" -o tsv') do echo     Subscription: %%s
echo.

REM ------------------------------------------------------------------
REM Create storage account (Function Apps require one)
REM ------------------------------------------------------------------
echo Creating storage account "%STORAGE_ACCOUNT%"...
az storage account create ^
    --name %STORAGE_ACCOUNT% ^
    --location %LOCATION% ^
    --resource-group %RESOURCE_GROUP% ^
    --sku Standard_LRS ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to create storage account.
    echo         The name must be globally unique, all lowercase, 3-24 chars.
    echo         Try changing STORAGE_ACCOUNT in this script.
    exit /b 1
)
echo [OK] Storage account created.

REM ------------------------------------------------------------------
REM Create Function App
REM ------------------------------------------------------------------
echo Creating Function App "%FUNCTION_APP_NAME%"...
az functionapp create ^
    --resource-group %RESOURCE_GROUP% ^
    --consumption-plan-location %LOCATION% ^
    --runtime python ^
    --runtime-version %PYTHON_VERSION% ^
    --functions-version 4 ^
    --name %FUNCTION_APP_NAME% ^
    --os-type linux ^
    --storage-account %STORAGE_ACCOUNT% ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to create Function App.
    echo         The name must be globally unique.
    echo         Try changing FUNCTION_APP_NAME in this script.
    exit /b 1
)
echo [OK] Function App created.

REM ------------------------------------------------------------------
REM Set application settings (your secrets + config)
REM ------------------------------------------------------------------
echo.
echo Pushing application settings...
az functionapp config appsettings set ^
    --name %FUNCTION_APP_NAME% ^
    --resource-group %RESOURCE_GROUP% ^
    --settings ^
        ALPACA_API_KEY=PKODDCN5U3KVXG4NJZFXRWMHZL ^
        ALPACA_SECRET_KEY=65oWdZEJXdSP3LqmscBRYaMcqXs7igN9TcyN9tLV899K ^
        WEBHOOK_AUTH_TOKEN=test-live-token-2025 ^
        ALPACA_PAPER=true ^
        DEFAULT_STOCK_QTY=1 ^
        BMR_STOCK_TP_PCT=0.2 ^
        BMR_STOCK_SL_PCT=0.1 ^
        BMR_STOCK_STOP_LIMIT_PCT=0.15 ^
        BMR_OPTIONS_TP_PCT=20.0 ^
        BMR_OPTIONS_SL_PCT=10.0 ^
        LC_STOCK_TP_PCT=1.0 ^
        LC_STOCK_SL_PCT=0.8 ^
        LC_STOCK_STOP_LIMIT_PCT=0.9 ^
        LC_OPTIONS_TP_PCT=50.0 ^
        LC_OPTIONS_SL_PCT=40.0 ^
        OPTIONS_DTE_MIN=14 ^
        OPTIONS_DTE_MAX=45 ^
        OPTIONS_DELTA_MIN=0.30 ^
        OPTIONS_DELTA_MAX=0.70 ^
        OPTIONS_MIN_OI=100 ^
        OPTIONS_MIN_VOLUME=10 ^
        OPTIONS_MAX_SPREAD_PCT=5.0 ^
        OPTIONS_MIN_PRICE=0.50 ^
        OPTIONS_MAX_PRICE=50.0 ^
        RISK_FREE_RATE=0.05 ^
        YAHOO_ENABLED=true ^
        YAHOO_RETRY_COUNT=5 ^
        YAHOO_BACKOFF_BASE=2 ^
        MAX_DOLLAR_RISK=50.0 ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to set application settings.
    exit /b 1
)
echo [OK] Application settings configured.

REM ------------------------------------------------------------------
REM Deploy the function code
REM ------------------------------------------------------------------
echo.
echo Deploying function code...
pushd function_app
func azure functionapp publish %FUNCTION_APP_NAME%
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Deployment failed.
    popd
    exit /b 1
)
popd
echo [OK] Deployment complete.

REM ------------------------------------------------------------------
REM Done — show the endpoint
REM ------------------------------------------------------------------
echo.
echo ====================================
echo   Deployment complete!
echo ====================================
echo.
echo Your webhook endpoint:
echo   https://%FUNCTION_APP_NAME%.azurewebsites.net/api/trade
echo.
echo TradingView webhook setup:
echo   URL:    https://%FUNCTION_APP_NAME%.azurewebsites.net/api/trade
echo   Header: X-Webhook-Token: test-live-token-2025
echo.
echo Test it with:
echo   curl -X POST https://%FUNCTION_APP_NAME%.azurewebsites.net/api/trade ^
echo     -H "Content-Type: application/json" ^
echo     -H "X-Webhook-Token: test-live-token-2025" ^
echo     -d "{\"content\": \"**New Buy Signal:**\nAAPL 5 Min Candle\nStrategy: bollinger_mean_reversion\nMode: stock\nPrice: 189.50\"}"
echo.

endlocal
