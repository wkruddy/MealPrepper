#!/usr/bin/env bash
# Install MealPrepper launchd jobs (macOS only).
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is for macOS (launchd). On Linux use: ./scripts/install_systemd.sh" >&2
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST_DIR="$HOME/Library/LaunchAgents"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="$(command -v python3)"
fi
MEALPREPPER="$PYTHON -m mealprepper"

mkdir -p "$PLIST_DIR"

install_plist() {
  local label="$1"
  local hour="$2"
  local minute="$3"
  local weekday="$4"   # 0-6 (Sunday=0) or * for daily
  local command="$5"
  local plist="$PLIST_DIR/com.mealprepper.${label}.plist"

  cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mealprepper.${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>-m</string>
    <string>mealprepper</string>
    ${command}
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${hour}</integer>
    <key>Minute</key>
    <integer>${minute}</integer>
    <key>Weekday</key>
    <integer>${weekday}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/data/logs/${label}.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/data/logs/${label}.err</string>
</dict>
</plist>
EOF

  launchctl unload "$plist" 2>/dev/null || true
  launchctl load "$plist"
  echo "Installed $plist"
}

mkdir -p "$ROOT/data/logs"

# Saturday 10:00 — weekly plan (weekday 6 = Saturday)
install_plist "plan-week" 10 0 6 '<string>plan-week</string>'

# Sunday 08:00 — grocery list (weekday 0 = Sunday)
install_plist "generate-grocery" 8 0 0 '<string>generate-grocery</string>'

# Daily 07:00 — morning notification (weekday omitted = every day; use separate daily plist)
DAILY_PLIST="$PLIST_DIR/com.mealprepper.send-daily.plist"
cat > "$DAILY_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.mealprepper.send-daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON}</string>
    <string>-m</string>
    <string>mealprepper</string>
    <string>send-daily</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${ROOT}/data/logs/send-daily.log</string>
  <key>StandardErrorPath</key>
  <string>${ROOT}/data/logs/send-daily.err</string>
</dict>
</plist>
EOF
launchctl unload "$DAILY_PLIST" 2>/dev/null || true
launchctl load "$DAILY_PLIST"
echo "Installed $DAILY_PLIST"

echo ""
echo "Done. Logs: $ROOT/data/logs/"
echo ""
echo "On Linux, use ./scripts/install_systemd.sh instead."
echo ""
echo "Cron alternative:"
echo "  0 10 * * 6  cd $ROOT && $ROOT/scripts/cron/run_scheduled.sh weekly-plan"
echo "  0  8 * * 0  cd $ROOT && $ROOT/scripts/cron/run_scheduled.sh grocery"
echo "  0  7 * * *  cd $ROOT && $ROOT/scripts/cron/run_scheduled.sh daily"
