#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -d ".venv" ]]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt

# Dev mode reload is convenient, but it can restart the process and close websockets (code 1012).
# Use DEV_RELOAD=0 for stable streaming.
if [[ "${DEV_RELOAD:-1}" == "1" ]]; then
  uvicorn app.main:app --reload --reload-dir app --port 8000
else
  uvicorn app.main:app --port 8000
fi

