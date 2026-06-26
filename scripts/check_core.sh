#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-.venv/bin/python}"

"$PYTHON" -m compileall -q agents broker codes config data llm tests orchestrator_v3.py

if command -v timeout >/dev/null 2>&1; then
  timeout 60 "$PYTHON" -m pytest -q -k 'not run_close_review and not improvement and not backtest'
else
  "$PYTHON" -m pytest -q -k 'not run_close_review and not improvement and not backtest'
fi
