@echo off
setlocal

if not exist ".venv" (
    echo Virtual environment not found. Running first-time setup...
    echo Creating venv...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -r requirements-dashboard.txt --quiet
    echo Setup complete.
) else (
    call .venv\Scripts\activate
)

python -c "import flask, requests, alpaca" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    pip install -r requirements-dashboard.txt --quiet
)

python dashboard\app.py
