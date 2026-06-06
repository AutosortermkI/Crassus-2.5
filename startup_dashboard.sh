#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${APP_ROOT:-$(pwd)}"
if [ ! -f "$APP_ROOT/dashboard_wsgi.py" ] && [ -f "$SCRIPT_DIR/dashboard_wsgi.py" ]; then
    APP_ROOT="$SCRIPT_DIR"
fi

export PYTHONPATH="$APP_ROOT/dashboard:$APP_ROOT/function_app:${PYTHONPATH:-}"
exec python -m gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app
