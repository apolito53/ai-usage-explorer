# Ubuntu Tray Indicator

The Ubuntu companion shows Claude's current calendar-month or today's cost in
the desktop panel. It refreshes usage once per minute without opening the full
terminal dashboard.

## Requirements

Install the GTK and AppIndicator bindings:

```bash
sudo apt install python3-gi python3-gi-cairo gir1.2-ayatanaappindicator3-0.1
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
- **Usage graphs…**, opening a compact window with spending charts;
- a selector for the value shown in the panel;
- the last successful refresh time or refresh error;
- an update row only when a new revision is available;
- actions to refresh, open the terminal explorer, and quit.

Choose **Usage graphs…**, then **This month** for daily or cumulative spending,
or **Today** for an intraday chart. Today's interval selector offers **1h · 30m ·
15m · 5m · 1m**. Choose **Per interval** for spending within each interval, or
**Cumulative** for the running total since local midnight. Both modes use the
selected interval. Daily spending and Per interval use bars; Cumulative uses a
shaded line. Today keeps its mode selection separate from This month. Hover
to inspect a point, or Tab to the chart and use Left/Right. Empty elapsed
intervals are zero in Per interval mode and flat in Cumulative mode; the
current interval is labeled as incomplete, and future
intervals are not plotted. Times follow your computer's local timezone.
Escape or closing the window hides it while the tray keeps running.

The Today chart reads timestamped usage already present in Claude's local
session logs, including usage before the tray started. It handles streaming
duplicates, copied messages, and nested subagent progress records. It estimates
costs using the existing pricing history when a record has no cost. If pricing
is missing or the timestamped total differs from the tray total, the chart
shows a note rather than silently adjusting the data.

Log locations follow `CLAUDE_CONFIG_DIR` (comma-separated directories), or
`${XDG_CONFIG_HOME:-~/.config}/claude/projects` and `~/.claude/projects` by
default. The chart keeps parsed usage metadata in memory and reuses unchanged
files. It does not create a usage database, save conversation text, or change
the polling interval when you switch chart intervals.

The chart shares the tray's project filter, pricing fallback, and one-minute
refresh. Refresh failures keep the last successful chart visible with an error
and its original timestamp. A failure to read local session logs is reported
on the Today tab without preventing the tray totals from refreshing.
Ubuntu's AppIndicator menu transports standard menu
items rather than custom drawing widgets, so the graph opens in its own window.

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
