@echo off
setlocal enabledelayedexpansion

echo ====================================
echo   Crassus 2.5 — Azure Deployment
echo ====================================
echo.

REM ------------------------------------------------------------------
REM Pre-flight
REM ------------------------------------------------------------------
where az >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Azure CLI is not installed.
    echo         Download from: https://aka.ms/installazurecliwindows
    exit /b 1
)
echo [OK] Azure CLI found.

where func >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Azure Functions Core Tools not installed.
    echo         Run: npm install -g azure-functions-core-tools@4 --unsafe-perm true
    exit /b 1
)
echo [OK] Azure Functions Core Tools found.

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is required for deployment packaging.
    exit /b 1
)
echo [OK] Python found.

REM ------------------------------------------------------------------
REM Configuration defaults (can be overridden in .env)
REM ------------------------------------------------------------------
set DEFAULT_RESOURCE_GROUP=CRG
set DEFAULT_LOCATION=eastus
set DEFAULT_STORAGE_ACCOUNT=crassusstorage25
set DEFAULT_FUNCTION_APP_NAME=crassus-25
set DEFAULT_DASHBOARD_SKU=B1
set PYTHON_VERSION=3.11
set DASHBOARD_STARTUP_COMMAND=gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app

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
call :load_env_var AZURE_SUBSCRIPTION_ID
call :load_env_var AZURE_DASHBOARD_APP_NAME
call :load_env_var AZURE_DASHBOARD_PLAN_NAME
call :load_env_var AZURE_DASHBOARD_SKU
call :load_env_var DASHBOARD_ACCESS_PASSWORD
call :load_env_var DASHBOARD_ACCESS_PASSWORD_HASH

if not defined AZURE_RESOURCE_GROUP set AZURE_RESOURCE_GROUP=%DEFAULT_RESOURCE_GROUP%
if not defined AZURE_LOCATION set AZURE_LOCATION=%DEFAULT_LOCATION%
if not defined AZURE_STORAGE_ACCOUNT set AZURE_STORAGE_ACCOUNT=%DEFAULT_STORAGE_ACCOUNT%
if not defined AZURE_FUNCTION_APP_NAME set AZURE_FUNCTION_APP_NAME=%DEFAULT_FUNCTION_APP_NAME%
if not defined AZURE_FUNCTION_BASE_URL set AZURE_FUNCTION_BASE_URL=https://%AZURE_FUNCTION_APP_NAME%.azurewebsites.net
if not defined AZURE_DASHBOARD_APP_NAME set AZURE_DASHBOARD_APP_NAME=%AZURE_FUNCTION_APP_NAME%-dashboard
if not defined AZURE_DASHBOARD_PLAN_NAME set AZURE_DASHBOARD_PLAN_NAME=%AZURE_DASHBOARD_APP_NAME%-plan
if not defined AZURE_DASHBOARD_SKU set AZURE_DASHBOARD_SKU=%DEFAULT_DASHBOARD_SKU%

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
    call :upsert_env_var WEBHOOK_AUTH_TOKEN !WEBHOOK_AUTH_TOKEN!
    echo [OK] Token saved to .env
)

call :upsert_env_var AZURE_RESOURCE_GROUP !AZURE_RESOURCE_GROUP!
call :upsert_env_var AZURE_LOCATION !AZURE_LOCATION!
call :upsert_env_var AZURE_STORAGE_ACCOUNT !AZURE_STORAGE_ACCOUNT!
call :upsert_env_var AZURE_FUNCTION_APP_NAME !AZURE_FUNCTION_APP_NAME!
call :upsert_env_var AZURE_DASHBOARD_APP_NAME !AZURE_DASHBOARD_APP_NAME!
call :upsert_env_var AZURE_DASHBOARD_PLAN_NAME !AZURE_DASHBOARD_PLAN_NAME!
call :upsert_env_var AZURE_DASHBOARD_SKU !AZURE_DASHBOARD_SKU!

if not defined DASHBOARD_ACCESS_PASSWORD if not defined DASHBOARD_ACCESS_PASSWORD_HASH (
    echo [WARN] No dashboard access password is configured.
    echo        Set DASHBOARD_ACCESS_PASSWORD or DASHBOARD_ACCESS_PASSWORD_HASH in .env before sharing the hosted dashboard.
)

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
for /f "delims=" %%s in ('az account show --query "id" -o tsv') do set ACTIVE_SUBSCRIPTION_ID=%%s
for /f "delims=" %%s in ('az account show --query "name" -o tsv') do set ACTIVE_SUBSCRIPTION_NAME=%%s
echo [OK] Logged in to Azure.
echo     Subscription: !ACTIVE_SUBSCRIPTION_NAME!
echo.

if not defined AZURE_SUBSCRIPTION_ID (
    set AZURE_SUBSCRIPTION_ID=!ACTIVE_SUBSCRIPTION_ID!
    call :upsert_env_var AZURE_SUBSCRIPTION_ID !AZURE_SUBSCRIPTION_ID!
)

REM ------------------------------------------------------------------
REM Shared Azure resources
REM ------------------------------------------------------------------
echo Ensuring resource group "!AZURE_RESOURCE_GROUP!" exists...
az group create ^
    --name !AZURE_RESOURCE_GROUP! ^
    --location !AZURE_LOCATION! ^
    --output none >nul 2>&1
echo [OK] Resource group ready.

az storage account show --name !AZURE_STORAGE_ACCOUNT! --resource-group !AZURE_RESOURCE_GROUP! --output none >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Storage account "!AZURE_STORAGE_ACCOUNT!" already exists.
) else (
    echo Creating storage account "!AZURE_STORAGE_ACCOUNT!"...
    az storage account create ^
        --name !AZURE_STORAGE_ACCOUNT! ^
        --location !AZURE_LOCATION! ^
        --resource-group !AZURE_RESOURCE_GROUP! ^
        --sku Standard_LRS ^
        --output none
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create storage account.
        exit /b 1
    )
    echo [OK] Storage account created.
)

az functionapp show --name !AZURE_FUNCTION_APP_NAME! --resource-group !AZURE_RESOURCE_GROUP! --output none >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Function App "!AZURE_FUNCTION_APP_NAME!" already exists.
) else (
    echo Creating Function App "!AZURE_FUNCTION_APP_NAME!"...
    az functionapp create ^
        --resource-group !AZURE_RESOURCE_GROUP! ^
        --consumption-plan-location !AZURE_LOCATION! ^
        --runtime python ^
        --runtime-version %PYTHON_VERSION% ^
        --functions-version 4 ^
        --name !AZURE_FUNCTION_APP_NAME! ^
        --os-type linux ^
        --storage-account !AZURE_STORAGE_ACCOUNT! ^
        --output none
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create Function App.
        exit /b 1
    )
    echo [OK] Function App created.
)

az appservice plan show --name !AZURE_DASHBOARD_PLAN_NAME! --resource-group !AZURE_RESOURCE_GROUP! --output none >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] App Service plan "!AZURE_DASHBOARD_PLAN_NAME!" already exists.
) else (
    echo Creating App Service plan "!AZURE_DASHBOARD_PLAN_NAME!"...
    az appservice plan create ^
        --name !AZURE_DASHBOARD_PLAN_NAME! ^
        --resource-group !AZURE_RESOURCE_GROUP! ^
        --location !AZURE_LOCATION! ^
        --sku !AZURE_DASHBOARD_SKU! ^
        --is-linux ^
        --output none
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create App Service plan.
        exit /b 1
    )
    echo [OK] App Service plan created.
)

az webapp show --name !AZURE_DASHBOARD_APP_NAME! --resource-group !AZURE_RESOURCE_GROUP! --output none >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Dashboard Web App "!AZURE_DASHBOARD_APP_NAME!" already exists.
) else (
    echo Creating Dashboard Web App "!AZURE_DASHBOARD_APP_NAME!"...
    az webapp create ^
        --resource-group !AZURE_RESOURCE_GROUP! ^
        --plan !AZURE_DASHBOARD_PLAN_NAME! ^
        --name !AZURE_DASHBOARD_APP_NAME! ^
        --runtime PYTHON^|%PYTHON_VERSION% ^
        --output none
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to create Dashboard Web App.
        exit /b 1
    )
    echo [OK] Dashboard Web App created.
)

REM ------------------------------------------------------------------
REM Build settings from .env
REM ------------------------------------------------------------------
set SETTINGS=
for /f "usebackq tokens=* delims=" %%L in ("%ENV_FILE%") do (
    set LINE=%%L
    if not "!LINE!"=="" if /I not "!LINE:~0,1!"=="#" (
        echo !LINE! | findstr "=" >nul
        if !ERRORLEVEL! equ 0 set SETTINGS=!SETTINGS! !LINE!
    )
)

echo.
echo Pushing Function App settings from .env...
az functionapp config appsettings set ^
    --name !AZURE_FUNCTION_APP_NAME! ^
    --resource-group !AZURE_RESOURCE_GROUP! ^
    --settings !SETTINGS! ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to configure Function App settings.
    exit /b 1
)
echo [OK] Function App settings configured.

echo Pushing Dashboard Web App settings from .env...
az webapp config appsettings set ^
    --name !AZURE_DASHBOARD_APP_NAME! ^
    --resource-group !AZURE_RESOURCE_GROUP! ^
    --settings !SETTINGS! AZURE_SUBSCRIPTION_ID=!AZURE_SUBSCRIPTION_ID! SCM_DO_BUILD_DURING_DEPLOYMENT=true ENABLE_ORYX_BUILD=true ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to configure Dashboard app settings.
    exit /b 1
)
echo [OK] Dashboard app settings configured.

echo Configuring Dashboard startup command...
az webapp config set ^
    --name !AZURE_DASHBOARD_APP_NAME! ^
    --resource-group !AZURE_RESOURCE_GROUP! ^
    --startup-file "!DASHBOARD_STARTUP_COMMAND!" ^
    --output none >nul
az webapp config set ^
    --name !AZURE_DASHBOARD_APP_NAME! ^
    --resource-group !AZURE_RESOURCE_GROUP! ^
    --always-on true ^
    --output none >nul 2>&1
echo [OK] Dashboard startup configured.

REM ------------------------------------------------------------------
REM Dashboard managed identity
REM ------------------------------------------------------------------
echo.
echo Enabling managed identity on the Dashboard Web App...
for /f "delims=" %%s in ('az webapp identity assign --name !AZURE_DASHBOARD_APP_NAME! --resource-group !AZURE_RESOURCE_GROUP! --query principalId -o tsv') do set DASHBOARD_PRINCIPAL_ID=%%s
for /f "delims=" %%s in ('az functionapp show --name !AZURE_FUNCTION_APP_NAME! --resource-group !AZURE_RESOURCE_GROUP! --query id -o tsv') do set FUNCTION_RESOURCE_ID=%%s
for /f "delims=" %%s in ('az webapp show --name !AZURE_DASHBOARD_APP_NAME! --resource-group !AZURE_RESOURCE_GROUP! --query id -o tsv') do set DASHBOARD_RESOURCE_ID=%%s

for %%R in ("!FUNCTION_RESOURCE_ID!" "!DASHBOARD_RESOURCE_ID!") do (
    az role assignment create ^
        --assignee-object-id !DASHBOARD_PRINCIPAL_ID! ^
        --assignee-principal-type ServicePrincipal ^
        --role Contributor ^
        --scope %%~R ^
        --output none >nul 2>&1
)
echo [OK] Managed identity can update hosted app settings.

REM ------------------------------------------------------------------
REM Deploy Function code
REM ------------------------------------------------------------------
echo.
echo Deploying Function App code...
pushd "%SCRIPT_DIR%function_app"
if not exist "local.settings.json" (
    (
    echo {
    echo   "IsEncrypted": false,
    echo   "Values": {
    echo     "FUNCTIONS_WORKER_RUNTIME": "python",
    echo     "AzureWebJobsStorage": "UseDevelopmentStorage=true"
    echo   }
    echo }
    ) > local.settings.json
    echo [OK] Created local.settings.json for deployment.
)
func azure functionapp publish !AZURE_FUNCTION_APP_NAME! --python
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Function App deployment failed.
    popd
    exit /b 1
)
popd
echo [OK] Function App deployment complete.

REM ------------------------------------------------------------------
REM Deploy Dashboard code
REM ------------------------------------------------------------------
echo.
echo Packaging dashboard deployment...
set TMP_DIR=%TEMP%\crassus_dashboard_%RANDOM%%RANDOM%
set DASHBOARD_PACKAGE=%TMP_DIR%\crassus-dashboard.zip
mkdir "%TMP_DIR%" >nul 2>&1

powershell -NoProfile -Command ^
    "$ErrorActionPreference = 'Stop';" ^
    "$root = [IO.Path]::GetFullPath('%SCRIPT_DIR%');" ^
    "$stage = Join-Path '%TMP_DIR%' 'stage';" ^
    "New-Item -ItemType Directory -Force -Path $stage | Out-Null;" ^
    "$files = @('.env.example','dashboard_wsgi.py','requirements.txt','requirements-dashboard.txt');" ^
    "foreach($file in $files){ Copy-Item (Join-Path $root $file) -Destination (Join-Path $stage $file) -Force };" ^
    "Copy-Item (Join-Path $root 'dashboard') -Destination $stage -Recurse -Force;" ^
    "Copy-Item (Join-Path $root 'function_app') -Destination $stage -Recurse -Force;" ^
    "Get-ChildItem -Path $stage -Recurse -Directory | Where-Object { $_.Name -in @('__pycache__','.pytest_cache','.git','.venv') } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;" ^
    "Get-ChildItem -Path $stage -Recurse -File | Where-Object { $_.Extension -in @('.pyc','.pyo') -or $_.Name -in @('local.settings.json','.options_targets.json','.webhook_activity.json') } | Remove-Item -Force -ErrorAction SilentlyContinue;" ^
    "if (Test-Path '%DASHBOARD_PACKAGE%') { Remove-Item '%DASHBOARD_PACKAGE%' -Force };" ^
    "Compress-Archive -Path (Join-Path $stage '*') -DestinationPath '%DASHBOARD_PACKAGE%' -Force;"
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to package Dashboard Web App.
    rmdir /s /q "%TMP_DIR%" >nul 2>&1
    exit /b 1
)
echo [OK] Dashboard package created.

echo Deploying Dashboard Web App code...
az webapp deploy ^
    --resource-group !AZURE_RESOURCE_GROUP! ^
    --name !AZURE_DASHBOARD_APP_NAME! ^
    --src-path "%DASHBOARD_PACKAGE%" ^
    --type zip ^
    --output none
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Dashboard Web App deployment failed.
    rmdir /s /q "%TMP_DIR%" >nul 2>&1
    exit /b 1
)
rmdir /s /q "%TMP_DIR%" >nul 2>&1
echo [OK] Dashboard Web App deployment complete.

if /I "!AZURE_FUNCTION_BASE_URL:~-10!"=="/api/trade" (
    set WEBHOOK_ENDPOINT=!AZURE_FUNCTION_BASE_URL!
) else (
    set FUNCTION_BASE=!AZURE_FUNCTION_BASE_URL!
    if "!FUNCTION_BASE:~-1!"=="/" set FUNCTION_BASE=!FUNCTION_BASE:~0,-1!
    set WEBHOOK_ENDPOINT=!FUNCTION_BASE!/api/trade
)
set DASHBOARD_URL=https://!AZURE_DASHBOARD_APP_NAME!.azurewebsites.net

echo.
echo ====================================
echo   Deployment complete!
echo ====================================
echo.
echo Shared dashboard:
echo   !DASHBOARD_URL!
echo.
echo TradingView webhook endpoint:
echo   !WEBHOOK_ENDPOINT!
echo.
echo Webhook auth:
echo   X-Webhook-Token: !WEBHOOK_AUTH_TOKEN!
echo.
echo Partners can use the dashboard at:
echo   !DASHBOARD_URL!
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

:upsert_env_var
powershell -NoProfile -Command ^
    "$envFile = '%ENV_FILE%';" ^
    "$key = '%~1';" ^
    "$value = '%~2';" ^
    "$lines = @(); if (Test-Path $envFile) { $lines = Get-Content $envFile };" ^
    "$updated = $false;" ^
    "$newLines = foreach ($line in $lines) { if ($line.StartsWith($key + '=')) { $updated = $true; $key + '=' + $value } else { $line } };" ^
    "if (-not $updated) { if ($newLines.Count -gt 0 -and $newLines[-1] -ne '') { $newLines += '' }; $newLines += $key + '=' + $value };" ^
    "Set-Content -Path $envFile -Value $newLines"
goto :eof
