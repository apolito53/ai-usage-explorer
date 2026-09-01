# macOS Menu-Bar App

The macOS companion shows personal Claude usage from the local `ccusage` data
on that Mac. It mirrors the Ubuntu tray's month-to-date/today selector,
one-minute refresh, pricing fallback, and silent hourly update checks.

## Requirements

- macOS with Python 3.10 or newer
- Node.js (Node 22 recommended)
- A local Claude Code usage history

The launcher recognizes common Homebrew paths and `~/.nvm/nvm.sh`. It prefers
an installed or cached `ccusage`, then falls back to `pnpm dlx` or `npx`.
The installer checks both Python and Node before registering the login item and
also downloads/checks `ccusage` and verifies that the installed Cocoa bridge
imports successfully.

## Install

From the repository root:

```bash
./macos/install.sh
```

The installer creates a private Python environment under
`~/Library/Application Support/AI Usage Explorer`, installs the maintained
PyObjC Cocoa bridge, and registers a per-user LaunchAgent. It does not require
an Apple Developer account, code signing, or an App Store installation.

The menu bar defaults to Claude month-to-date cost. Its menu also shows today's
cost, refresh status, an immediate refresh action, and a command to open the
terminal explorer.

## Update and uninstall

The app checks the checkout's Git upstream once per hour. When an update is
available, pull the repository and rerun `./macos/install.sh`.

Remove the menu app and its private Python environment with:

```bash
./macos/uninstall.sh
```

Logs remain under `~/Library/Logs/AI Usage Explorer` for diagnostics.

## Validation status

The portable Python and shell paths are covered by the repository unit tests.
The macOS Cocoa UI and LaunchAgent still require a real-Mac smoke test. After
installation, verify:

1. One AI Usage Explorer item appears in the menu bar.
2. Month-to-date and Today switch the displayed value.
3. Refresh now updates without opening a terminal.
4. Open AI Usage Explorer launches the terminal dashboard.
5. The item returns after logging out and back in.

If startup fails, inspect `~/Library/Logs/AI Usage Explorer/stderr.log`.
