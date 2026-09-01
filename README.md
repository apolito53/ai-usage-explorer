# AI Usage Explorer

Interactive terminal dashboard for Claude and Codex usage data from `ccusage`.

## Usage

```bash
./ai-usage-explorer.sh
```

The explorer runs provider-specific `ccusage claude` and `ccusage codex` JSON commands, then shows daily usage, a trend chart, and token price rates for the selected day's models.

On startup, the explorer checks current token price cards and stores dated snapshots in `.ai-usage-pricing-history.json`. The `Token Rates` panel uses the snapshot active on the selected row date so historical views do not silently use newer prices.

When `rtk` is available, the bottom row includes an `RTK Gain` panel with current token savings from `rtk gain`.

On startup, the script checks for required Python packages. If they are missing, it creates `.venv` and installs them before launching the terminal UI.

`ccusage` does not need to be installed globally. The data fetch path runs `pnpm dlx ccusage`, so `pnpm` downloads or reuses `ccusage` on demand. The script expects `nvm`, Node 22, and `pnpm` to be available in the login shell used for fetching.

## Ubuntu Tray Indicator

```bash
./ai-usage-explorer.sh --tray
```

The indicator shows Claude's current calendar-month cost next to a monochrome usage icon and refreshes once per minute. Its menu always displays both month-to-date and today's cost, and lets you choose which one appears in the panel; month-to-date is selected by default. The menu can also refresh immediately, open the full terminal explorer, or quit. The first poll refreshes the cached model price cards; later polls reuse them so the tray does not hit pricing sources every minute.

While running, the tray silently checks the Git upstream once per hour. Current versions and failed checks produce no menu status or notification. When a fast-forward update is available, the tray shows its revision in the menu and sends one desktop notification; it does not repeat the notification for the same revision. Set `AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS` to change the interval (minimum 300 seconds).

The tray uses Ubuntu's AppIndicator support and the system Python GTK bindings. On current Ubuntu releases, install them with:

```bash
sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1
```

Older Ubuntu releases may provide `gir1.2-appindicator3-0.1` instead; the launcher supports either package. Set `AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS` to change the 60-second interval (minimum 10 seconds).

To launch the tray automatically after signing in to Ubuntu:

```bash
./ai-usage-explorer.sh --install-tray-autostart
```

This first verifies that the host is Ubuntu or an Ubuntu-derived Linux distribution, then installs `ai-usage-explorer-tray.desktop` in the current user's XDG autostart directory. At login, the launcher checks the Git upstream, safely fast-forwards when possible, and verifies the Python dependencies before starting the tray. The tray's later data polls skip the Git check. Unsupported operating systems are rejected without writing a startup file. The entry points at the current checkout, so moving or deleting the checkout requires reinstalling it. Remove it with `./ai-usage-explorer.sh --remove-tray-autostart`.

## Versioning and Updates

```bash
./ai-usage-explorer.sh --version
./ai-usage-explorer.sh --no-update
```

The version is stored in `VERSION`. On startup, the script checks the current Git upstream. If the local checkout is behind and has no tracked local changes, it runs a fast-forward-only pull and restarts itself. It skips the update check when the script is not running from a Git checkout, no upstream is configured, the branch has diverged, or tracked local changes are present. Set `AI_USAGE_EXPLORER_NO_UPDATE=1` or pass `--no-update` to disable the check for a run.

## Options

```bash
./ai-usage-explorer.sh --since 20260401
./ai-usage-explorer.sh --refresh
./ai-usage-explorer.sh --demo
./ai-usage-explorer.sh --file /tmp/ccusage-daily.json
./ai-usage-explorer.sh --tray
./ai-usage-explorer.sh --version
```

- `--since YYYYMMDD`: Start date for ccusage daily data.
- `--until YYYYMMDD`: End date for ccusage daily data.
- `--project NAME`: Pass through Claude `ccusage --project`.
- `--group day|month`: Initial grouping.
- `--refresh`: Fetch current model pricing instead of using `ccusage --offline`.
- `--demo`: Load bundled demo data instead of running `ccusage`.
- `--file PATH`: Load an existing `ccusage` JSON file.
- `--tray`: Show Claude usage in the Ubuntu tray, selectable between month-to-date and today.
- `--install-tray-autostart`: Launch the tray automatically after Ubuntu login.
- `--remove-tray-autostart`: Remove the Ubuntu login startup entry.
- `--no-update`: Skip the startup Git update check.
- `--no-pricing-update`: Skip the startup token price check.
- `--version`: Show the current explorer version.

## Keyboard

- `j` / `k` or `↑` / `↓`: Move day selection.
- `pgup` / `pgdn`: Page day selection.
- `tab`: Switch focus between the day list and trend chart.
- `g` / `G`: Jump to first / last day.
- `a`: Cycle provider filter (`Claude`, `Codex`, mixed, or all detected providers).
- `m`: Open the multi-select model filter grouped by detected provider. In the picker, `space` toggles a model or an entire provider, `a` selects all models, `enter` applies, and `esc` cancels.
- `esc` / `v`: Open date range filter. Defaults to month to date. Custom ranges use `YYYY-MM-DD..YYYY-MM-DD`.
- `space` / `enter`: Expand selected row model breakdown.
- `←/→`: Cycle sort column (prev/next).
- `s`: Reverse sort order.
- `r`: Refresh data.
- `1`-`5`: Chart metric: cost, total, input, output, cache.
- `q`: Quit.
