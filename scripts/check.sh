#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-.venv/bin/python}"

"$PYTHON" -m compileall -q agents backtest broker codes config data llm tests orchestrator.py

if command -v timeout >/dev/null 2>&1; then
  timeout 60 "$PYTHON" -m pytest -q
else
  "$PYTHON" -m pytest -q
fi

rg -n "TODO|FIXME|NotImplemented|pass|return \\[\\]|return \\{\\}|theme_news|_stp_manager|get_investor_flow|get_balance" \
  agents backtest broker codes orchestrator.py tests || true
