#!/usr/bin/env bash
# Install MealPrepper scheduled jobs for the current OS.
set -euo pipefail

case "$(uname -s)" in
  Linux)
    exec "$(dirname "$0")/install_systemd.sh" "$@"
    ;;
  Darwin)
    exec "$(dirname "$0")/install_launchd.sh" "$@"
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    echo "Use ./scripts/install_systemd.sh (Linux) or ./scripts/install_launchd.sh (macOS)." >&2
    exit 1
    ;;
esac
