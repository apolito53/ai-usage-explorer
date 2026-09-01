#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
    echo "ERROR: This installer only supports macOS." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
INSTALL_ROOT="${HOME}/Library/Application Support/AI Usage Explorer"
VENV_DIR="${INSTALL_ROOT}/venv"
LOG_DIR="${HOME}/Library/Logs/AI Usage Explorer"
PLIST_PATH="${HOME}/Library/LaunchAgents/com.apolito.ai-usage-explorer.plist"
LABEL="com.apolito.ai-usage-explorer"
USER_DOMAIN="gui/$(id -u)"

if [ -z "$PYTHON_BIN" ]; then
    echo "ERROR: Python 3.10 or newer is required." >&2
    exit 1
fi
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "ERROR: Python 3.10 or newer is required (found $($PYTHON_BIN --version 2>&1))." >&2
    exit 1
fi

for candidate in /opt/homebrew/bin /usr/local/bin "${HOME}/.local/bin"; do
    if [ -d "$candidate" ]; then
        PATH="$candidate:$PATH"
    fi
done
if [ -s "${HOME}/.nvm/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${HOME}/.nvm/nvm.sh"
fi
if command -v nvm >/dev/null 2>&1; then
    nvm use 22 >/dev/null 2>&1 || nvm use default >/dev/null 2>&1 || true
fi
if ! command -v node >/dev/null 2>&1; then
    echo "ERROR: Node.js is required. Install Node 22 and rerun this installer." >&2
    exit 1
fi
if ! command -v ccusage >/dev/null 2>&1 \
    && ! command -v pnpm >/dev/null 2>&1 \
    && ! command -v npx >/dev/null 2>&1; then
    echo "ERROR: Install ccusage, pnpm, or npm/npx and rerun this installer." >&2
    exit 1
fi

echo "Checking ccusage..."
if command -v ccusage >/dev/null 2>&1; then
    ccusage --version >/dev/null
elif command -v pnpm >/dev/null 2>&1; then
    pnpm dlx ccusage --version >/dev/null
else
    npx --yes ccusage@latest --version >/dev/null
fi

mkdir -p "$INSTALL_ROOT" "$LOG_DIR" "$(dirname "$PLIST_PATH")"
if [ ! -x "${VENV_DIR}/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
"${VENV_DIR}/bin/python" -m pip install --disable-pip-version-check \
    -r "${SCRIPT_DIR}/requirements.txt"
PYTHONPYCACHEPREFIX="${INSTALL_ROOT}/pycache" \
    "${VENV_DIR}/bin/python" -m py_compile \
    "${SCRIPT_DIR}/ai-usage-macos.py" \
    "${REPO_ROOT}/ai-usage-tray.py"
"${VENV_DIR}/bin/python" -c 'import AppKit, Foundation, objc'

xml_escape() {
    "$PYTHON_BIN" -c 'import html, sys; print(html.escape(sys.argv[1], quote=True))' "$1"
}

PYTHON_XML="$(xml_escape "${VENV_DIR}/bin/python")"
APP_XML="$(xml_escape "${SCRIPT_DIR}/ai-usage-macos.py")"
SCRIPT_XML="$(xml_escape "${REPO_ROOT}/ai-usage-explorer.sh")"
PRICING_XML="$(xml_escape "${REPO_ROOT}/.ai-usage-pricing-history.json")"
STDOUT_XML="$(xml_escape "${LOG_DIR}/stdout.log")"
STDERR_XML="$(xml_escape "${LOG_DIR}/stderr.log")"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_XML}</string>
        <string>${APP_XML}</string>
        <string>--script</string>
        <string>${SCRIPT_XML}</string>
        <string>--pricing-history</string>
        <string>${PRICING_XML}</string>
    </array>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${STDOUT_XML}</string>
    <key>StandardErrorPath</key>
    <string>${STDERR_XML}</string>
</dict>
</plist>
EOF

plutil -lint "$PLIST_PATH" >/dev/null
launchctl bootout "$USER_DOMAIN" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "$USER_DOMAIN" "$PLIST_PATH"
launchctl kickstart -k "${USER_DOMAIN}/${LABEL}"

echo "Installed and started AI Usage Explorer for macOS."
echo "Logs: ${LOG_DIR}"
