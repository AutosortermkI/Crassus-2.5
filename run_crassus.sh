#!/usr/bin/env bash
set -e
source "$(dirname "$0")/.venv/bin/activate"
cd "$(dirname "$0")/function_app"
func start
