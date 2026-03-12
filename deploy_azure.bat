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
REM Configuration defaults (can be overridden in .env)
REM ------------------------------------------------------------------
set DEFAULT_RESOURCE_GROUP=CRG
set DEFAULT_LOCATION=eastus
set DEFAULT_STORAGE_ACCOUNT=crassusstorage25
set DEFAULT_FUNCTION_APP_NAME=crassus-25
set PYTHON_VERSION=3.11

set SCRIPT_DIR=%~dp0
set ENV_FILE=%SCRIPT_DIR%.env

if not exist "%ENV_FILE%" (
    echo [ERROR] .env file not found at %ENV_FILE%
    echo         Run setup.bat or the dashboard first to create it.
    exit /b 1
)

call :load_env_var ALPACA_API_KEY
call :load_env_var ALPACA_SECRET_KEY
call :load_env_var WEBHOOK_AUTH_TOKEN
call :load_env_var AZURE_RESOURCE_GROUP
call :load_env_var AZURE_LOCATION
call :load_env_var AZURE_STORAGE_ACCOUNT
call :load_env_var AZURE_FUNCTION_APP_NAME
call :load_env_var AZURE_FUNCTION_BASE_URL

if not defined ALPACA_API_KEY (
    echo [ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env
    exit /b 1
)
if not defined ALPACA_SECRET_KEY (
    echo [ERROR] ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in .env
    exit /b 1
)

if not defined WEBHOOK_AUTH_TOKEN (
    for /f %%t in ('python -c "import secrets; print(secrets.token_hex(16))"') do set WEBHOOK_AUTH_TOKEN=%%t
    echo [INFO] Auto-generated WEBHOOK_AUTH_TOKEN: !WEBHOOK_AUTH_TOKEN!
    echo WEBHOOK_AUTH_TOKEN=!WEBHOOK_AUTH_TOKEN!>>"%ENV_FILE%"
    echo [OK] Token saved to .env
)

if defined AZURE_RESOURCE_GROUP (set RESOURCE_GROUP=!AZURE_RESOURCE_GROUP!) else set RESOURCE_GROUP=%DEFAULT_RESOURCE_GROUP%
if defined AZURE_LOCATION (set LOCATION=!AZURE_LOCATION!) else set LOCATION=%DEFAULT_LOCATION%
if defined AZURE_STORAGE_ACCOUNT (set STORAGE_ACCOUNT=!AZURE_STORAGE_ACCOUNT!) else set STORAGE_ACCOUNT=%DEFAULT_STORAGE_ACCOUNT%
if defined AZURE_FUNCTION_APP_NAME (set FUNCTION_APP_NAME=!AZURE_FUNCTION_APP_NAME!) else set FUNCTION_APP_NAME=%DEFAULT_FUNCTION_APP_NAME%
if defined AZURE_FUNCTION_BASE_URL (set FUNCTION_BASE_URL=!AZURE_FUNCTION_BASE_URL!) else set FUNCTION_BASE_URL=https://%FUNCTION_APP_NAME%.azurewebsites.net

echo [OK] Credentials loaded from .env

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
for /f "delims=" %%s in ('az account show --query "name" -o tsv') do echo     Subscription: %%s
echo.

REM ------------------------------------------------------------------
REM Create resource group (if it doesn't exist)
REM ------------------------------------------------------------------
echo Ensuring resource group "%RESOURCE_GROUP%" exists...
az group create ^
    --name %RESOURCE_GROUP% ^
    --location %LOCATION% ^
    --output none >nul 2>&1
echo [OK] Resource group ready.

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
    echo         Update AZURE_STORAGE_ACCOUNT in .env and try again.
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
    echo         Update AZURE_FUNCTION_APP_NAME in .env and try again.
    exit /b 1
)
echo [OK] Function App created.

REM ------------------------------------------------------------------
REM Set application settings from .env
REM ------------------------------------------------------------------
echo.
echo Pushing application settings from .env...
set SETTINGS=
for /f "usebackq tokens=* delims=" %%L in ("%ENV_FILE%") do (
    set LINE=%%L
    if not "!LINE!"=="" if /I not "!LINE:~0,1!"=="#" (
        echo !LINE! | findstr "=" >nul
        if !ERRORLEVEL! equ 0 set SETTINGS=!SETTINGS! !LINE!
    )
)

az functionapp config appsettings set ^
    --name %FUNCTION_APP_NAME% ^
    --resource-group %RESOURCE_GROUP% ^
    --settings !SETTINGS! ^
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
pushd "%SCRIPT_DIR%function_app"
func azure functionapp publish %FUNCTION_APP_NAME% --python
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Deployment failed.
    popd
    exit /b 1
)
popd
echo [OK] Deployment complete.

if "!FUNCTION_BASE_URL:~-10!"=="/api/trade" (
    set ENDPOINT=!FUNCTION_BASE_URL!
) else (
    if "!FUNCTION_BASE_URL:~-1!"=="/" set FUNCTION_BASE_URL=!FUNCTION_BASE_URL:~0,-1!
    set ENDPOINT=!FUNCTION_BASE_URL!/api/trade
)

REM ------------------------------------------------------------------
REM Done — show the endpoint
REM ------------------------------------------------------------------
echo.
echo ====================================
echo   Deployment complete!
echo ====================================
echo.
echo Your webhook endpoint:
echo   !ENDPOINT!
echo.
echo TradingView webhook setup:
echo   URL:    !ENDPOINT!
echo   Header: X-Webhook-Token: !WEBHOOK_AUTH_TOKEN!
echo.
echo Test it with:
echo   curl -X POST !ENDPOINT! ^
echo     -H "Content-Type: application/json" ^
echo     -H "X-Webhook-Token: !WEBHOOK_AUTH_TOKEN!" ^
echo     -d "{\"content\": \"**New Buy Signal:**\nAAPL 5 Min Candle\nStrategy: bollinger_mean_reversion\nMode: stock\nPrice: 189.50\"}"
echo.

endlocal
goto :eof

:load_env_var
set "%~1="
for /f "usebackq tokens=1* delims==" %%A in (`findstr /B /C:"%~1=" "%ENV_FILE%"`) do (
    set "%~1=%%B"
    goto :eof
)
goto :eof
