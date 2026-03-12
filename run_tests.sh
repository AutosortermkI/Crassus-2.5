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

if ! python -c "import scipy, pytest" >/dev/null 2>&1; then
    pip install -r "$DIR/function_app/requirements.txt" --quiet
    pip install pytest --quiet
fi

cd "$DIR"
python -m pytest -v
