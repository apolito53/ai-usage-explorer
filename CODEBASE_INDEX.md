# Codebase Index

This repository is deliberately small, but its main launcher contains both
Bash orchestration and an embedded Python terminal UI. Use this index before
editing so changes land on the correct side of that boundary.

## Top-level map

| Path | Responsibility |
| --- | --- |
| `ai-usage-explorer.sh` | CLI parsing, self-update, dependency setup, `ccusage` invocation, provider normalization, pricing snapshots, and the embedded Rich terminal UI |
| `ai-usage-tray.py` | Shared Claude tray core plus the Ubuntu GTK/AppIndicator frontend |
| `macos/ai-usage-macos.py` | macOS AppKit menu-bar frontend; imports the shared tray core |
| `macos/install.sh` | macOS dependency preflight and per-user LaunchAgent installation |
| `macos/uninstall.sh` | Removes the macOS LaunchAgent and private Python environment |
| `assets/` | Ubuntu symbolic status icon |
| `demo/usage-demo.json` | Stable sample input for UI smoke testing |
| `tests/` | Standard-library `unittest` coverage for pricing, filters, startup, Ubuntu tray, and portable macOS helpers |
| `VERSION` | User-visible version string |
| `requirements.txt` | Terminal UI Python dependencies |
| `macos/requirements.txt` | macOS Cocoa bridge dependency |

## `ai-usage-explorer.sh` landmarks

Search by symbol name rather than relying on line numbers; this file moves
often.

| Area | Symbols or marker |
| --- | --- |
| Argument parsing and defaults | `usage`, the opening `while [[ $# -gt 0 ]]` loop |
| Safe startup update | `self_update` |
| Python dependency bootstrap | `find_python`, `python_has_dependencies`, `ensure_python_dependencies` |
| Full Claude/Codex collection | `fetch_usage_data` |
| Provider normalization | `normalize_claude_row`, `normalize_codex_row`, `normalize_rows` in the first Python heredoc |
| Tray-only Claude collection | `fetch_claude_tray_data` |
| Ubuntu tray dispatch | `find_tray_python`, `run_tray`, `manage_tray_autostart` |
| Pricing snapshots | `refresh_pricing_history` and its Python heredoc |
| Terminal UI boundary | `<<'PYTHON_EOF'` |
| Terminal data/pricing helpers | `read_pricing_history`, `pricing_snapshot_for_date`, `estimated_model_cost`, `backfill_missing_costs` |
| Terminal state | `State` |
| Terminal filtering/rendering/input | `UsageExplorer` |

The shell has two internal JSON modes:

- `--dump-json` emits normalized Claude and Codex daily/monthly data for a UI
  refresh.
- `--dump-claude-tray-json` emits the smaller Claude-only payload used by both
  desktop companions.

They are implementation entry points, not part of the normal user-facing CLI.

## Tray core landmarks

`ai-usage-tray.py` contains three layers:

1. Pure usage and pricing helpers, from `safe_float` through `format_cost`.
2. Shared process/update helpers: `TrayConfig`, `fetch_usage`, and
   `available_update_revision`.
3. Ubuntu integration: autostart helpers, the native AppIndicator fallback,
   `ClaudeUsageTray`, and `main`.

The macOS adapter dynamically imports this module so month/today totals,
pricing fallback, fetch timeouts, and update detection stay consistent across
desktop platforms. It does not import GTK unless the Ubuntu entry point asks
for it.

## Test routing

| Change | Start with |
| --- | --- |
| Startup ordering or launcher text | `tests/test_startup.py` |
| Historical rates or zero-cost repair | `tests/test_pricing.py` |
| Provider/model filtering | `tests/test_model_filter.py` |
| Claude totals, update polling, Ubuntu autostart, AppIndicator fallback | `tests/test_tray.py` |
| macOS argument/escaping/platform behavior | `tests/test_macos.py` |

The terminal tests extract selected functions and classes from the embedded
Python with `ast`. If a tested symbol is renamed or begins depending on a new
global, update the extraction namespace deliberately.

## Where should I edit?

- New terminal option: parser and help text in `ai-usage-explorer.sh`, then
  `docs/USAGE.md`.
- Provider JSON shape: normalization heredoc in `fetch_usage_data`, then
  pricing/filter tests.
- Terminal layout or controls: embedded `UsageExplorer`, then usage docs.
- Shared tray math or update behavior: pure/shared sections of
  `ai-usage-tray.py`; verify both platform adapters.
- Ubuntu-only UI or login behavior: lower half of `ai-usage-tray.py` and
  `docs/UBUNTU_TRAY.md`.
- macOS-only UI or login behavior: `macos/` and `macos/README.md`.
- Model rates or pricing fallback: update both embedded terminal helpers and
  tray-core helpers. They intentionally mirror one another today.

## Runtime and generated files

These are local state and are ignored by Git:

- `.venv/` — terminal UI environment
- `__pycache__/` — Python bytecode
- `.ai-usage-pricing-history.json` — dated pricing snapshots

Ubuntu autostart writes one file beneath the user's XDG autostart directory.
The macOS installer writes a LaunchAgent, a private virtual environment under
`~/Library/Application Support`, and logs under `~/Library/Logs`.
