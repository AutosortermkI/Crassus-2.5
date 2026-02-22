#!/usr/bin/env bash
set -e
source "$(dirname "$0")/.venv/bin/activate"
python -m pytest "$(dirname "$0")/tests/" -v
