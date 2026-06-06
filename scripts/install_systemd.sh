#!/usr/bin/env bash
# Install MealPrepper systemd user timers (Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
RUNNER="$ROOT/scripts/cron/run_scheduled.sh"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script is for Linux (systemd). On macOS use: ./scripts/install_launchd.sh" >&2
  exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
  echo "systemctl not found — install systemd or use cron: ./scripts/cron/run_scheduled.sh" >&2
  exit 1
fi

mkdir -p "$UNIT_DIR" "$ROOT/data/logs"
chmod +x "$RUNNER"

install_job() {
  local name="$1"
  local description="$2"
  local schedule="$3"
  local job="$4"
  local service="$UNIT_DIR/mealprepper-${name}.service"
  local timer="$UNIT_DIR/mealprepper-${name}.timer"

  cat > "$service" <<EOF
[Unit]
Description=MealPrepper ${description}

[Service]
Type=oneshot
WorkingDirectory=${ROOT}
EnvironmentFile=-${ROOT}/.env
ExecStart=${RUNNER} ${job}
StandardOutput=append:${ROOT}/data/logs/${name}.log
StandardError=append:${ROOT}/data/logs/${name}.err
EOF

  cat > "$timer" <<EOF
[Unit]
Description=MealPrepper ${description}

[Timer]
OnCalendar=${schedule}
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable --now "mealprepper-${name}.timer"
  echo "Installed mealprepper-${name}.timer (${schedule})"
}

# Saturday 10:00 — weekly plan
install_job "plan-week" "weekly plan (Saturday 10:00)" "Sat *-*-* 10:00:00" "weekly-plan"

# Sunday 08:00 — grocery list
install_job "generate-grocery" "grocery list (Sunday 08:00)" "Sun *-*-* 08:00:00" "grocery"

# Daily 07:00 — morning SMS
install_job "send-daily" "daily reminder (07:00)" "*-*-* 07:00:00" "daily"

echo ""
echo "Done. Logs: $ROOT/data/logs/"
echo ""
echo "Status:"
systemctl --user list-timers 'mealprepper-*' --no-pager || true
echo ""
echo "If timers do not run while logged out, enable lingering for this user:"
echo "  sudo loginctl enable-linger \$USER"
echo ""
echo "Manual test:"
echo "  systemctl --user start mealprepper-send-daily.service"
echo ""
echo "Remove timers:"
echo "  systemctl --user disable --now mealprepper-plan-week.timer mealprepper-generate-grocery.timer mealprepper-send-daily.timer"
echo "  rm $UNIT_DIR/mealprepper-*.service $UNIT_DIR/mealprepper-*.timer"
echo "  systemctl --user daemon-reload"
