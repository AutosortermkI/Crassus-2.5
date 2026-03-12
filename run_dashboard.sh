#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$DIR/.venv" ]; then
    echo "Virtual environment not found. Running first-time setup..."
    echo "Creating venv..."
    python3 -m venv "$DIR/.venv"
    source "$DIR/.venv/bin/activate"
    pip install -r "$DIR/requirements-dashboard.txt" --quiet
    echo "Setup complete. Launch again or run ./setup.sh for full credential setup."
else
    source "$DIR/.venv/bin/activate"
fi

if ! python -c "import flask, requests, alpaca" >/dev/null 2>&1; then
    pip install -r "$DIR/requirements-dashboard.txt" --quiet
fi

python "$DIR/dashboard/app.py"
