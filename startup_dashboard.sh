#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="${APP_ROOT:-$SCRIPT_DIR}"
if [ ! -f "$APP_ROOT/dashboard_wsgi.py" ]; then
    echo "dashboard_wsgi.py not found in APP_ROOT=$APP_ROOT" >&2
    exit 1
fi

cd "$APP_ROOT"
export PYTHONPATH="$APP_ROOT:$APP_ROOT/dashboard:$APP_ROOT/function_app:${PYTHONPATH:-}"
exec python -m gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app
