# Ubuntu Tray Indicator

The Ubuntu companion shows Claude's current calendar-month or today's cost in
the desktop panel. It refreshes usage once per minute without opening the full
terminal dashboard.

## Requirements

Install the GTK and AppIndicator bindings:

```bash
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
```

Older Ubuntu releases may provide `gir1.2-appindicator3-0.1` instead. When the
GI AppIndicator typelib is unavailable but the native library exists, the app
uses its built-in `ctypes` adapter.

The tray uses the system Python GTK bindings. Its usage subprocess still needs
Node.js plus an installed `ccusage`, `pnpm`, or npm/`npx`.

## Run

```bash
./ai-usage-explorer.sh --tray
```

The menu displays:

- month-to-date and today's Claude cost;
- a selector for the value shown in the panel;
- the last successful refresh time or refresh error;
- an update row only when a new revision is available;
- actions to refresh, open the terminal explorer, and quit.

Unless pricing updates are disabled, the first successful data poll refreshes
pricing history. Later polls reuse it so a one-minute panel refresh does not
also become a one-minute pricing request.

## Start after login

```bash
./ai-usage-explorer.sh --install-tray-autostart
```

The command supports Ubuntu and Ubuntu-derived Linux distributions. It writes
`ai-usage-explorer-tray.desktop` beneath `${XDG_CONFIG_HOME:-~/.config}/autostart`
and points it at the current checkout. Moving or deleting the checkout requires
reinstalling the entry.

Remove it with:

```bash
./ai-usage-explorer.sh --remove-tray-autostart
```

At login, the normal launcher performs its safe Git update check and Python
dependency check before starting the indicator.

## Polling and updates

Usage refreshes every 60 seconds. While running, a separate timer checks the Git
upstream every hour. Current versions and failed checks are silent. A newly
available fast-forward revision appears once in the menu and produces one
desktop notification; it is not installed automatically during a data poll.

Intervals can be changed with:

```bash
AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS=120 \
AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS=7200 \
./ai-usage-explorer.sh --tray
```

The minimums are 10 seconds for data and 300 seconds for update checks.

## Troubleshooting

- “Could not connect to the Ubuntu desktop session” means the process cannot
  reach the active graphical session.
- An AppIndicator requirement error means neither the GI typelib nor supported
  native library could be loaded.
- A refresh error is reduced to the final useful `ccusage`/launcher line and
  shown in the menu; the indicator stays alive for the next refresh.
- If the terminal action does nothing, install `gnome-terminal` or provide
  `x-terminal-emulator`.

Run the terminal dashboard directly to expose complete startup and dependency
errors:

```bash
./ai-usage-explorer.sh --no-update
```
