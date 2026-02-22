#!/usr/bin/env bash
set -e
source "$(dirname "$0")/.venv/bin/activate"
python "$(dirname "$0")/dashboard/app.py"
