# AI Usage Explorer

Interactive terminal dashboard for Claude and Codex usage data from `ccusage`.

## Usage

```bash
./ai-usage-explorer.sh
```

The explorer runs provider-specific `ccusage claude` and `ccusage codex` JSON commands, then shows daily usage, a trend chart, and the selected day's model breakdown.

On startup, the script checks for required Python packages. If they are missing, it creates `.venv` and installs them before launching the terminal UI.

`ccusage` does not need to be installed globally. The data fetch path runs `pnpm dlx ccusage`, so `pnpm` downloads or reuses `ccusage` on demand. The script expects `nvm`, Node 22, and `pnpm` to be available in the login shell used for fetching.

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
./ai-usage-explorer.sh --version
```

- `--since YYYYMMDD`: Start date for ccusage daily data.
- `--until YYYYMMDD`: End date for ccusage daily data.
- `--project NAME`: Pass through Claude `ccusage --project`.
- `--group day|month`: Initial grouping.
- `--refresh`: Fetch current model pricing instead of using `ccusage --offline`.
- `--demo`: Load bundled demo data instead of running `ccusage`.
- `--file PATH`: Load an existing `ccusage` JSON file.
- `--no-update`: Skip the startup Git update check.
- `--version`: Show the current explorer version.

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
