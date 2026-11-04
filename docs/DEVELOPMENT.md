# Development Guide

## Setup

The Python test suite uses only the standard library. JavaScript chart tests
use Node's built-in test runner. Running the application also requires Bash,
Node.js, a `ccusage` runner, and the packages declared in `requirements.txt`.

```bash
python3 -m unittest discover -s tests -v
```

The launcher creates `.venv/` automatically for the terminal UI. The macOS
installer deliberately uses a separate environment under the user's
`~/Library/Application Support` directory.

## Validation

Run the checks appropriate to every code change:

```bash
bash -n ai-usage-explorer.sh macos/install.sh macos/uninstall.sh
python3 -m py_compile ai-usage-tray.py ai_usage_chart.py ai_usage_intraday.py ai_usage_updates.py ai_usage_web.py macos/ai-usage-macos.py
python3 -m unittest discover -s tests -v
node tests/test_web_chart.cjs
git diff --check
```

For documentation-only changes, at minimum run the unit tests and diff check so
broken links or accidental code edits do not hitch a ride.

Useful smoke checks:

```bash
# Does not read local usage or perform update/pricing requests
./ai-usage-explorer.sh --demo --no-update --no-pricing-update

# Prints public help without launching the UI
./ai-usage-explorer.sh --help
```

Ubuntu tray work should be exercised in a real desktop session with the
installed AppIndicator implementation. macOS UI or LaunchAgent changes require
a real Mac; Linux can validate only the portable Python helpers, shell guards,
and source syntax, plus the portable browser graphs. Follow the checklist in `macos/README.md` when handing off a
macOS change.

The browser graph tests need permission to bind loopback sockets. The optional
Playwright smoke test uses synthetic data and starts its own local server; it
never reads personal usage logs. Install its development-only browser tools in
a temporary directory, then run:

```bash
npm install --prefix /tmp/ai-usage-browser-tests playwright@1.61.1
/tmp/ai-usage-browser-tests/node_modules/.bin/playwright install chromium webkit
PLAYWRIGHT_MODULE=/tmp/ai-usage-browser-tests/node_modules/playwright node tests/browser_smoke.cjs
PLAYWRIGHT_MODULE=/tmp/ai-usage-browser-tests/node_modules/playwright BROWSER=webkit node tests/browser_smoke.cjs
```

Alternatively, set `BROWSER_EXECUTABLE` to an installed Chromium/Chrome binary.
`SCREENSHOT` and `MOBILE_SCREENSHOT` optionally save light/desktop and dark/mobile
previews. WebKit on Linux is useful compatibility coverage, not a substitute for
testing Safari and the menu-bar integration on macOS.
The pinned test version avoids a [known Playwright/WebKit startup mismatch](https://github.com/microsoft/playwright/issues/42964)
with frozen WebKit builds on older operating systems.

## Change conventions

### Preserve the normalized JSON contract

The terminal UI expects `daily`, `monthly`, and `totals`, with row-level token,
cost, provider, model, and breakdown fields. Provider-specific changes belong
in the normalization layer; avoid leaking raw Claude or Codex shapes into UI
code.

### Keep pricing behavior paired

The terminal's pricing helpers and `ai-usage-tray.py` mirror one another. When
changing snapshot selection, rate extraction, model aliases, or zero-cost
backfill, update both paths and cover the behavior in `tests/test_pricing.py`
and/or `tests/test_tray.py`.

### Treat model-picker draft state as transactional

`State.selected_models` is the applied filter. `None` means all models, while
an empty set means no models. `State.model_menu_selection` is a draft copy used
only while the menu is open. Escape cancels the draft; Enter applies it. Do not
collapse these states to a single set.

### Keep polling out of data refresh

Desktop usage polls invoke the shell with `--no-update`. Git and registry polling
have their own timer and must remain silent for current versions and errors.
Ubuntu prompts once per new version per session and installs only after the user
accepts. Keep the active `ccusage` executable pinned across usage polls.

### Preserve safe update behavior

Startup updates are fast-forward-only and must not overwrite tracked local
changes or operate from a nested/foreign Git root. Update tests before relaxing
any guard.

## Test organization

- `test_startup.py` checks launcher ordering and exercises tray detachment,
  logging, startup failures, and foreground mode with a stand-in tray process.
- `test_pricing.py` extracts embedded pricing functions with `ast` and tests
  historical backfill.
- `test_model_filter.py` extracts terminal filter functions/classes with `ast`.
- `test_tray.py` imports the tray module directly and fakes GTK/native/update
  boundaries.
- `test_updates.py` covers version detection, executable selection, installation
  failure/activation, and Git update guards using temporary repositories.
- `test_macos.py` tests configuration, escaping, isolated imports, and graph
  refresh/open/close wiring using fake Cocoa objects.
- `test_web.py` covers aggregate serialization, missing logs, time buckets/DST,
  server access boundaries, refresh requests, and preserved snapshots on errors.
- `test_web_chart.cjs` checks filtered/cumulative series, totals, and chart scales.
- `browser_smoke.cjs` exercises the real browser/server flow, including keyboard
  day navigation, filters, refresh failures, responsive layout, and month rollover.

Because embedded terminal tests execute selected AST nodes, new dependencies on
module globals must be added to their test namespace. A NameError there usually
means the extraction fixture needs the same constant—not that Python has become
haunted.

## Adding features

1. Decide whether the change belongs to collection, normalization, shared tray
   logic, or one presentation adapter.
2. Add the smallest test at that boundary.
3. Update the relevant reference document and `CODEBASE_INDEX.md` if routing or
   file responsibilities changed.
4. Run the full portable validation set.
5. Record any Ubuntu/macOS smoke-test gap explicitly in the handoff.

`VERSION` is not bumped automatically. Update it only when making an intentional
release/versioning change.
