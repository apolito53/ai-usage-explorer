# AI Usage Explorer

Interactive terminal dashboard for Claude Code usage data from `ccusage daily --json`.

## Usage

```bash
./ai-usage-explorer.sh
```

The explorer runs `pnpm dlx ccusage daily -b --json --offline`, then shows daily usage, a trend chart, and the selected day's model breakdown.

## Options

```bash
./ai-usage-explorer.sh --since 20260401
./ai-usage-explorer.sh --refresh
./ai-usage-explorer.sh --demo
./ai-usage-explorer.sh --file /tmp/ccusage-daily.json
```

- `--since YYYYMMDD`: Start date for ccusage daily data.
- `--until YYYYMMDD`: End date for ccusage daily data.
- `--project NAME`: Pass through `ccusage --project`.
- `--group day|month`: Initial grouping.
- `--refresh`: Fetch current model pricing instead of using `ccusage --offline`.
- `--demo`: Load bundled demo data instead of running `ccusage`.
- `--file PATH`: Load an existing `ccusage` JSON file.

## Keyboard

- `j` / `k` or `↑` / `↓`: Move day selection.
- `pgup` / `pgdn`: Page day selection.
- `tab`: Switch focus between the day list and trend chart.
- `g` / `G`: Jump to first / last day.
- `m`: Cycle model filter.
- `v`: Open date range filter. Defaults to month to date. Custom ranges use `YYYY-MM-DD..YYYY-MM-DD`.
- `space` / `enter`: Expand selected row model breakdown.
- `←/→`: Cycle sort column (prev/next).
- `s`: Reverse sort order.
- `r`: Refresh data.
- `1`-`5`: Chart metric: cost, total, input, output, cache.
- `q`: Quit.
