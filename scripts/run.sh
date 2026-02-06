#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

# Stable mode (no reload) to avoid websocket 1012 restarts.
uvicorn app.main:app --port 8000

