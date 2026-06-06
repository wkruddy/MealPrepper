#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="${PY:-python3.11}"
if ! command -v "$PY" >/dev/null 2>&1; then
  PY=python3
fi

"$PY" -m venv .venv
source .venv/bin/activate
pip install -U pip setuptools wheel -q
pip install -e ".[dev]" -q

cp -n .env.example .env 2>/dev/null || true
python -m mealprepper init-db

echo ""
echo "MealPrepper installed. Next steps:"
echo "  1. Start Ollama: ollama serve && ollama pull llama3.2"
echo "  2. Edit .env if needed (SMS_BACKEND=twilio for production SMS on Linux)"
echo "  3. mealprepper plan-week --auto-approve"
echo "  4. mealprepper generate-grocery"
if [[ "$(uname -s)" == "Linux" ]]; then
  echo "  5. ./scripts/install_systemd.sh   # schedule weekly/daily jobs"
elif [[ "$(uname -s)" == "Darwin" ]]; then
  echo "  5. ./scripts/install_launchd.sh   # schedule weekly/daily jobs"
fi
