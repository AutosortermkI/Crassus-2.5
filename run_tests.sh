#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ ! -d "$DIR/.venv" ]; then
    echo "Virtual environment not found. Creating and installing deps..."
    python3 -m venv "$DIR/.venv"
    source "$DIR/.venv/bin/activate"
    pip install -r "$DIR/function_app/requirements.txt" --quiet
    pip install pytest --quiet
else
    source "$DIR/.venv/bin/activate"
fi

python -m pytest "$DIR/tests/" -v
