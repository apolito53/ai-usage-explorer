# Architecture

AI Usage Explorer has one data pipeline and three presentation surfaces. The
terminal surface lives inside the Bash launcher as embedded Python; both
desktop companions use the smaller shared tray core.

```text
local Claude/Codex history
           |
        ccusage
           |
  Bash collection + normalization
           |
   normalized daily/monthly JSON
       /                 \
pricing backfill      Claude tray dump
      |                    |
Rich terminal UI      ai-usage-tray.py core
                           |
                 Ubuntu GTK / macOS AppKit
```

## Main launcher

`ai-usage-explorer.sh` is the public entry point. Its Bash layer owns:

1. CLI parsing and validation.
2. Conservative Git self-update.
3. Python dependency setup.
4. `ccusage` process selection and invocation.
5. Provider-specific JSON normalization.
6. Pricing-history refresh.
7. Dispatch to the Ubuntu tray or embedded Rich UI.

The embedded Python begins at the `PYTHON_EOF` heredoc. `State` holds UI state;
`UsageExplorer` filters, sorts, renders, handles raw terminal input, and applies
background refresh results.

## Normalized usage model

The full collection path calls both providers for daily and monthly JSON. It
then emits one shape:

```json
{
  "daily": [
    {
      "date": "2026-09-01",
      "provider": "claude",
      "providers": ["claude"],
      "inputTokens": 0,
      "outputTokens": 0,
      "cacheCreationTokens": 0,
      "cacheReadTokens": 0,
      "totalTokens": 0,
      "totalCost": 0.0,
      "modelsUsed": [],
      "modelBreakdowns": []
    }
  ],
  "monthly": [],
  "totals": {}
}
```

Claude rows largely map directly from `ccusage`. Codex reports costs at the row
level, so multi-model row cost is distributed proportionally by each model's
token count. That approximation exists so filtering a Codex row by model can
still produce a meaningful cost.

Provider and model filters are independent. Model filtering recomputes the
visible row totals from `modelBreakdowns`; it does not merely hide model names.

## Pricing history and zero-cost repair

On startup, `refresh_pricing_history` builds a rate catalog from built-in cards
and the configured LiteLLM-compatible source. A new dated snapshot is appended
only when model cards change; otherwise the latest snapshot's check metadata is
updated.

When `ccusage` supplies a zero-cost model breakdown, the app estimates cost
from tokens and the snapshot effective on that usage date. Existing nonzero
costs remain authoritative. Recalculated model costs also update their row and
overall totals.

Pricing helpers currently exist in two places:

- the embedded terminal Python in `ai-usage-explorer.sh`;
- the pure helper section of `ai-usage-tray.py`.

This duplication is intentional for the current packaging model. Any change to
model-name normalization, snapshot selection, rates, or cost repair must keep
both implementations aligned and update their tests.

## Desktop companion core

`ai-usage-tray.py` is importable without GTK. Its pure/shared layer provides:

- pricing fallback and Claude month/today totals;
- `TrayConfig` and construction of the tray dump command;
- subprocess timeouts and error reduction;
- fast-forward update detection.

The Ubuntu frontend loads GTK/AppIndicator only from its entry point. It can
fall back to a small `ctypes` adapter when the native AppIndicator library is
installed but the optional GI typelib is not.

Its Usage graphs action lazily opens `ai_usage_chart.py`, a GTK/Cairo window
with spending bars and cumulative area charts. `fetch_usage_snapshot` retains the daily
series after pricing backfill so chart updates and panel totals share one poll.
Monthly daily bars with timestamped details use a darker color and open a dated
intraday tab; a legend identifies bars that have only an aggregate daily total.
The existing `fetch_usage` tuple interface remains available to macOS.

The Ubuntu poll also uses `ai_usage_intraday.py` to read timestamped Claude
JSONL usage into memory. Unchanged files reuse parsed usage metadata; file
replacement, truncation, and deletion invalidate it. Conversation content is
discarded during parsing. Each snapshot includes timestamped costs grouped by
local day for the current month, priced using each day's effective pricing
snapshot. Today and historical day tabs group usage into 60-, 30-, 15-, 5-, or
1-minute intervals without changing polling or persisting new usage history.
Grouping starts at local midnight and uses elapsed time, preserving repeated
hours across daylight-saving changes. Historical days end at the next midnight;
Today stops at refresh time. Local log failures affect day details and their
availability indicators while aggregate totals continue to refresh.

The macOS frontend dynamically imports the same module and supplies AppKit menu
items, timers, Terminal launching, and AppleScript notifications. Cocoa imports
are lazy, which keeps portable helper tests runnable on Linux.

## Refresh and update behavior

Terminal startup checks the configured upstream. It only fast-forwards when:

- the checkout root is exactly this repository;
- an upstream exists and can be fetched;
- local `HEAD` is behind without divergence; and
- tracked staged and unstaged changes are absent.

After a successful pull, the launcher replaces itself with the updated script.
All other cases are skipped or reported without rewriting local work.

Desktop companions refresh usage once per minute by default. Their child dump
commands include `--no-update`, preventing each data poll from fetching Git.
Instead, a separate hourly background check detects a fast-forward update.
Failures and current versions stay silent. macOS shows a menu item and one
notification for each new app revision.

Ubuntu also checks the npm registry for a newer stable `ccusage` release, after
its first successful poll and on the hourly timer. Each new version opens an
update/restart prompt once per session; the menu keeps an action to reopen it.
Accepting prepares exact requested versions in a worker, then replaces the tray
process through its launcher. Dirty or diverged app checkouts are preserved,
and preparation failures leave the current tray alive. A changed local Git HEAD
also prompts a restart, even when the checkout already matches upstream.

`ai_usage_updates.py` owns stable version comparisons, registry metadata,
executable resolution, and explicit version installation. The shared tray dump
includes the selected executable and version; Ubuntu pins that executable for
subsequent polls so newly cached packages cannot silently replace it. Installs
use a separate version directory and atomically change the `current` symlink
only after verification. Registry checks never install packages. The initial
poll can bootstrap an installation if no existing executable is available.

Terminal and desktop data refreshes run outside their UI loops. Results return
through a queue (terminal), GLib idle callbacks (Ubuntu), or AppHelper callbacks
(macOS).

## Runtime boundaries

- Usage comes from the current user's local provider history through
  `ccusage`.
- Pricing snapshots are stored in `.ai-usage-pricing-history.json`.
- Terminal dependencies live in `.venv/`.
- Tray-managed `ccusage` releases live in the user's XDG cache beneath
  `ai-usage-explorer/ccusage`.
- Ubuntu login integration is one XDG autostart desktop file.
- macOS login integration is one LaunchAgent plus a private Cocoa environment.

There is no server, database, telemetry pipeline, or team-wide aggregation in
this repository.
