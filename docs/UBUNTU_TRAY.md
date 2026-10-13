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

In **This month → Daily spending**, darker bars have timestamped day details.
Click one (or select it with Left/Right and press Enter) to open that date with
the same interval and cumulative controls as Today. Past dates cover the full
local day; clicking today's bar opens Today. **← This month** returns to the
selected bar. Lighter bars show daily totals only, and a legend below the month
chart explains both colors. A day can have partial timestamped history; any
difference from its daily total is shown below the detail chart.

The day charts read timestamped usage already present in Claude's local
session logs, including usage before the tray started. They handle streaming
duplicates, copied messages, and nested subagent progress records. They estimate
costs using the pricing history effective on each usage date when a record has
no cost. If pricing is missing or the timestamped total differs from the daily
total, the chart shows a note rather than silently adjusting the data.

Log locations follow `CLAUDE_CONFIG_DIR` (comma-separated directories), or
`${XDG_CONFIG_HOME:-~/.config}/claude/projects` and `~/.claude/projects` by
default. The chart keeps parsed usage metadata in memory and reuses unchanged
files. It does not create a usage database, save conversation text, or change
the polling interval when you switch chart intervals.

The chart shares the tray's project filter, pricing fallback, and one-minute
refresh. Refresh failures keep the last successful chart visible with an error
and its original timestamp. A failure to read local session logs is reported
in the day charts without preventing the tray totals from refreshing.
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

Usage refreshes every 60 seconds. After the first successful refresh and then
every hour, the tray checks its Git upstream for app updates and the npm registry
for a newer stable `ccusage` release. **Check for updates** runs the same check
from the menu. Current versions and failed checks stay silent.

A newly available version opens a prompt with **Later** and **Update and
restart**. Choosing Later keeps the current version running; the prompt appears
only once per version during that session, and **Update and restart…** stays in
the menu. If the app checkout was updated externally, the prompt offers a restart
to load it.

Update checks only fetch Git and package metadata. Accepting the prompt installs
the specific `ccusage` release, verifies its version, applies any app update, and
restarts the tray. App updates require a clean checkout and a fast-forward;
local changes are preserved. Download or preparation failures leave the current
tray running and show an error that can be retried from the menu.

The Ubuntu tray keeps using its selected `ccusage` executable until restart.
Accepted releases are installed under
`${XDG_CACHE_HOME:-~/.cache}/ai-usage-explorer/ccusage`, with the active selection
changed only after verification. An existing installed or cached executable is
reused initially; if none exists, the launcher downloads `ccusage` to bootstrap
the first usage poll.

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
