@echo off
setlocal

if not exist ".venv" (
    echo Virtual environment not found. Running first-time setup...
    echo Creating venv...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -r function_app\requirements.txt --quiet
    pip install flask python-dotenv alpaca-py requests --quiet
    echo Setup complete.
) else (
    call .venv\Scripts\activate
)

python dashboard\app.py
