#!/usr/bin/env bash
# MealPrepper scheduled jobs — used by systemd timers and cron (see README)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
else
  echo "python3 not found — create a venv: python3 -m venv .venv" >&2
  exit 1
fi

export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}$ROOT/src"

case "${1:-}" in
  weekly-plan)
    # Saturday 10:00 — plan week and request approval
    "$PYTHON" -m mealprepper plan-week
    ;;
  grocery)
    # Sunday 08:00 — generate grocery list (after manual/auto approval)
    "$PYTHON" -m mealprepper generate-grocery
    ;;
  daily)
    # Every day 07:00 — morning meal reminder
    "$PYTHON" -m mealprepper send-daily
    ;;
  feedback)
    # Sunday 20:00 — process week's feedback into preferences
    "$PYTHON" -m mealprepper process-feedback
    ;;
  full)
    "$PYTHON" -m mealprepper plan-week --auto-approve
    "$PYTHON" -m mealprepper generate-grocery
    ;;
  *)
    echo "Usage: $0 {weekly-plan|grocery|daily|feedback|full}"
    exit 1
    ;;
esac
