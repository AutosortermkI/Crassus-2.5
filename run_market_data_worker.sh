#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
export PYTHONPATH="$PWD/function_app${PYTHONPATH:+:$PYTHONPATH}"
python3 function_app/market_data.py
