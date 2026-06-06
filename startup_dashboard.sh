#!/usr/bin/env bash
set -e

APP_ROOT="${APP_ROOT:-/home/site/wwwroot}"
SITE_PACKAGES="$APP_ROOT/.python_packages/lib/site-packages"

if [ ! -d "$SITE_PACKAGES/flask" ] || [ ! -d "$SITE_PACKAGES/requests" ]; then
    mkdir -p "$SITE_PACKAGES"
    python -m pip install \
        --disable-pip-version-check \
        --no-cache-dir \
        -r "$APP_ROOT/requirements-dashboard.txt" \
        -t "$SITE_PACKAGES"
fi

export PYTHONPATH="$SITE_PACKAGES:$APP_ROOT/dashboard:$APP_ROOT/function_app:${PYTHONPATH:-}"
exec gunicorn --bind=0.0.0.0:${PORT:-8000} --timeout 600 dashboard_wsgi:app
