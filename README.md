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
./ai-usage-explorer.sh --file /tmp/ccusage-daily.json
```

- `--since YYYYMMDD`: Start date for ccusage daily data.
- `--until YYYYMMDD`: End date for ccusage daily data.
- `--project NAME`: Pass through `ccusage --project`.
- `--refresh`: Fetch current model pricing instead of using `ccusage --offline`.
- `--file PATH`: Load an existing `ccusage daily --json` file.

## Keyboard

- `j` / `k` or `↑` / `↓`: Move day selection.
- `g` / `G`: Jump to first / last day.
- `m`: Cycle model filter.
- `c` / `t` / `d`: Sort by cost / tokens / date.
- `r`: Reverse sort order.
- `1`-`5`: Chart metric: cost, total, input, output, cache.
- `q`: Quit.
