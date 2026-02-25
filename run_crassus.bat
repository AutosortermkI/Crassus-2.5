@echo off
setlocal

if not exist ".venv" (
    echo Virtual environment not found. Creating and installing deps...
    python -m venv .venv
    call .venv\Scripts\activate
    pip install -r function_app\requirements.txt --quiet
) else (
    call .venv\Scripts\activate
)

cd function_app
func start
