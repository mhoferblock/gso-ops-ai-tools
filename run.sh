#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

# Load .env if it exists
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "════════════════════════════════════════"
echo "  GSO Ops AI Tools"
echo "  http://localhost:8000"
echo "════════════════════════════════════════"
PYTHONPATH=. python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
