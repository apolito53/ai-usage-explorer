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
cost, refresh status, a **Usage graphs…** action, an immediate refresh action,
and a command to open the terminal explorer.

## Usage graphs

Choose **Usage graphs…** to open a local page in your default browser. It shows
monthly spending by model, earlier months, cumulative spending, and timestamped
day details with 1-hour, 30-minute, 15-minute, 5-minute, and 1-minute intervals.
Click an outlined daily bar (or focus the chart, use arrow keys, and press
Enter) to inspect that day. Choose **Monthly** to return, or **Today** for the
current day. Use **Models** to select multiple models or click a model's name
to show only that model; the totals follow the selection.

The page updates automatically after the app's normal usage polls. **Refresh
now** requests an immediate poll. If a poll fails, the last successful graphs
remain visible with an error message. Missing timestamped logs leave daily
totals available; the page explains when day details cannot be shown. Earlier
months only cover usage records still present on the Mac.

Graphs run locally with bundled assets, without a hosted service or additional
Python packages. Keep the menu-bar app running while using them. After restarting
the app, reopen graphs from its menu; an old browser tab cannot reconnect to the
new app session.

## Update and uninstall

The app checks the checkout's Git upstream once per hour. When an update is
available, pull the repository and rerun `./macos/install.sh`.

Remove the menu app and its private Python environment with:

```bash
./macos/uninstall.sh
```

Logs remain under `~/Library/Logs/AI Usage Explorer` for diagnostics.

## Validation status

The portable Python paths, local graph server, and JavaScript calculations have
automated tests. The browser smoke test exercises the real page and server on
Linux. This does not verify the Cocoa menu, macOS browser launching, or
LaunchAgent; those still require a real-Mac smoke test. After installation,
verify:

1. One AI Usage Explorer item appears in the menu bar.
2. Month-to-date and Today switch the displayed value.
3. Refresh now updates without opening a terminal.
4. Usage graphs opens the default browser; month navigation, day details,
   filters, and Refresh now work.
5. Open AI Usage Explorer launches the terminal dashboard.
6. The item returns after logging out and back in.

If startup fails, inspect `~/Library/Logs/AI Usage Explorer/stderr.log`.

For implementation details, see the repository
[architecture](../docs/ARCHITECTURE.md) and [codebase index](../CODEBASE_INDEX.md).
