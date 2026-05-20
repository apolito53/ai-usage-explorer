# AI Usage Explorer

Interactive terminal dashboard for Claude and Codex usage data from `ccusage`.

## Usage

```bash
./ai-usage-explorer.sh
```

The explorer runs provider-specific `ccusage claude` and `ccusage codex` JSON commands, then shows daily usage, a trend chart, and the selected day's model breakdown.

On startup, the script checks for required Python packages. If they are missing, it creates `.venv` and installs them before launching the terminal UI.

`ccusage` does not need to be installed globally. The data fetch path runs `pnpm dlx ccusage`, so `pnpm` downloads or reuses `ccusage` on demand. The script expects `nvm`, Node 22, and `pnpm` to be available in the login shell used for fetching.

## Options

```bash
./ai-usage-explorer.sh --since 20260401
./ai-usage-explorer.sh --refresh
./ai-usage-explorer.sh --demo
./ai-usage-explorer.sh --file /tmp/ccusage-daily.json
```

- `--since YYYYMMDD`: Start date for ccusage daily data.
- `--until YYYYMMDD`: End date for ccusage daily data.
- `--project NAME`: Pass through Claude `ccusage --project`.
- `--group day|month`: Initial grouping.
- `--refresh`: Fetch current model pricing instead of using `ccusage --offline`.
- `--demo`: Load bundled demo data instead of running `ccusage`.
- `--file PATH`: Load an existing `ccusage` JSON file.

## Keyboard

- `j` / `k` or `↑` / `↓`: Move day selection.
- `pgup` / `pgdn`: Page day selection.
- `tab`: Switch focus between the day list and trend chart.
- `g` / `G`: Jump to first / last day.
- `a`: Cycle provider filter (`Claude`, `Codex`, mixed, or all detected providers).
- `m`: Open the multi-select model filter grouped by detected provider. In the picker, `space` toggles a model, `a` selects all models, `enter` applies, and `esc` cancels.
- `esc` / `v`: Open date range filter. Defaults to month to date. Custom ranges use `YYYY-MM-DD..YYYY-MM-DD`.
- `space` / `enter`: Expand selected row model breakdown.
- `←/→`: Cycle sort column (prev/next).
- `s`: Reverse sort order.
- `r`: Refresh data.
- `1`-`5`: Chart metric: cost, total, input, output, cache.
- `q`: Quit.
