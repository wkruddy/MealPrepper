#!/usr/bin/env bash
# MealPrepper scheduled jobs — add to crontab (see README)
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate 2>/dev/null || true
export PYTHONPATH="${PYTHONPATH:-}:src"

case "${1:-}" in
  weekly-plan)
    # Saturday 10:00 — plan week and request approval
    python -m mealprepper plan-week
    ;;
  grocery)
    # Sunday 08:00 — generate grocery list (after manual/auto approval)
    python -m mealprepper generate-grocery
    ;;
  daily)
    # Every day 07:00 — morning meal reminder
    python -m mealprepper send-daily
    ;;
  feedback)
    # Sunday 20:00 — process week's feedback into preferences
    python -m mealprepper process-feedback
    ;;
  full)
    python -m mealprepper plan-week --auto-approve
    python -m mealprepper generate-grocery
    ;;
  *)
    echo "Usage: $0 {weekly-plan|grocery|daily|feedback|full}"
    exit 1
    ;;
esac
