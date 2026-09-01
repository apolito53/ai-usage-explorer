# Usage Reference

## Requirements

- Bash
- Python 3 with `venv` support
- Node.js; Node 22 is recommended
- One way to run `ccusage`: an installed binary, `pnpm`, or npm/`npx`

The launcher checks `/opt/homebrew/bin`, `/usr/local/bin`, and
`~/.local/bin`, and loads `~/.nvm/nvm.sh` when present. It prefers Node 22 from
NVM and falls back to the configured default.

The full dashboard needs the Python package in `requirements.txt`. Missing
packages are installed into the repository-local `.venv` automatically.

## Commands

```bash
# Local Claude and Codex history
./ai-usage-explorer.sh

# A narrower collection window
./ai-usage-explorer.sh --since 20260801 --until 20260831

# Claude project filter
./ai-usage-explorer.sh --project my-project

# Bundled data, with all network checks disabled
./ai-usage-explorer.sh --demo --no-update --no-pricing-update

# Previously normalized JSON
./ai-usage-explorer.sh --file /path/to/usage.json
```

## CLI options

| Option | Behavior |
| --- | --- |
| `--since YYYYMMDD` | Collection start date. The current default is `20260209`. |
| `--until YYYYMMDD` | Optional collection end date. |
| `--project NAME` | Passes a project filter to Claude collection. |
| `--group day\|month` | Accepted for compatibility and validated, but the current terminal view remains daily. |
| `--refresh` | Omits `ccusage --offline`, allowing `ccusage` to refresh its own pricing information. |
| `--demo` | Uses `demo/usage-demo.json` instead of local usage. |
| `--file PATH` | Uses an existing normalized JSON payload instead of invoking `ccusage`. |
| `--tray` | Starts the Ubuntu panel indicator. |
| `--install-tray-autostart` | Installs the Ubuntu per-user autostart entry. |
| `--remove-tray-autostart` | Removes that Ubuntu autostart entry. |
| `--no-update` | Disables the startup Git update check for this run. |
| `--no-pricing-update` | Skips the pricing-history refresh; existing snapshots may still be used. |
| `--version` | Prints the version from `VERSION`. |
| `-h`, `--help` | Prints the command help. |

`--project` applies only to Claude because that is the provider command that
accepts the project filter in the current collection path.

## Keyboard controls

### Main dashboard

| Key | Action |
| --- | --- |
| `j` / `k`, `↑` / `↓` | Move the selected day or scroll the focused chart. |
| `PgUp` / `PgDn` | Move by one visible page. |
| `g` / `G` | Jump to the first or last row/page. |
| `Tab` | Switch focus between the daily table and trend chart. |
| `a` | Cycle the provider filter: all, each detected provider, and mixed when present. |
| `m` | Open the model picker. |
| `Esc` or `v` | Open the date-range picker. |
| `Space` or `Enter` | Expand/collapse the selected row's model breakdown. |
| `←` / `→` | Select the previous/next sort column. |
| `s` | Reverse the current sort direction. |
| `r` | Refresh usage in a background thread. |
| `1`–`5` | Chart cost, total tokens, input, output, or cache. |
| `q` or `Ctrl-C` | Quit. |

### Model picker

- `j` / `k`, arrows, and page keys move through providers and models.
- `Space` toggles a provider group or model.
- `a` selects every model.
- `Enter` applies the draft selection.
- `Esc` or `m` cancels it.

An applied empty selection is meaningful: it displays no models. “All models”
is represented separately, so cancel and apply semantics must not collapse the
two states.

### Date-range picker

Presets include all loaded data, month to date, last 7 days, and last 30 days.
The custom range accepts `YYYY-MM-DD` start and end dates. `Tab` moves into or
between the custom fields; `Enter` advances or applies.

## Environment variables

| Variable | Meaning |
| --- | --- |
| `AI_USAGE_EXPLORER_NO_UPDATE=1` | Same as `--no-update`. |
| `AI_USAGE_EXPLORER_NO_PRICING_UPDATE=1` | Same as `--no-pricing-update`. |
| `AI_USAGE_EXPLORER_PRICING_HISTORY` | Overrides the pricing-history JSON path. |
| `AI_USAGE_EXPLORER_PRICING_URL` | Overrides the LiteLLM-compatible pricing catalog URL. |
| `AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS` | Desktop-companion data interval; minimum 10 seconds, default 60. |
| `AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS` | Desktop-companion Git check interval; minimum 300 seconds, default 3600. |

`XDG_CONFIG_HOME` controls the Ubuntu autostart location in the usual XDG way.

## Network and local data

Claude and Codex history is read locally by `ccusage`. A normal run can still
make these outbound requests:

- package resolution when `ccusage` must run through `pnpm dlx` or `npx`;
- the pricing catalog refresh used to maintain historical rate snapshots;
- `git fetch` for the safe startup update check;
- `ccusage` pricing/network activity when `--refresh` is selected.

`--no-update --no-pricing-update` disables the repository and pricing-catalog
checks. Use `--demo` or `--file` when local provider history should not be read.

The generated `.ai-usage-pricing-history.json` and `.venv/` stay in the local
checkout and are ignored by Git.
