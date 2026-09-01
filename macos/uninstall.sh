#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: This uninstaller only supports macOS." >&2
    exit 1
fi

INSTALL_ROOT="${HOME}/Library/Application Support/AI Usage Explorer"
PLIST_PATH="${HOME}/Library/LaunchAgents/com.apolito.ai-usage-explorer.plist"
USER_DOMAIN="gui/$(id -u)"

launchctl bootout "$USER_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
rm -f -- "$PLIST_PATH"
rm -rf -- "$INSTALL_ROOT"

echo "Removed AI Usage Explorer for macOS. Logs were left in ~/Library/Logs."
