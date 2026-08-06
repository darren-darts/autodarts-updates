#!/usr/bin/env bash
# Production start for Linux / Raspberry Pi - mirrors start.bat for Windows.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PY="$ROOT/backend/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "Backend virtual environment not found at backend/.venv"
    echo "Run the first-time setup in README.md, then try again."
    exit 1
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
    echo "Frontend build not found - building now..."
    (cd "$ROOT/frontend" && npm run build)
fi

echo "Starting Autodarts on http://0.0.0.0:8000 ..."
cd "$ROOT/backend"
exec "$VENV_PY" -m uvicorn app:app --host 0.0.0.0 --port 8000
