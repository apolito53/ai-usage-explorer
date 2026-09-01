# Development Guide

## Setup

The test suite uses only the Python standard library. Running the application
also requires Bash, Node.js, a `ccusage` runner, and the packages declared in
`requirements.txt`.

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
python3 -m py_compile ai-usage-tray.py macos/ai-usage-macos.py
python3 -m unittest discover -s tests -v
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
and source syntax. Follow the checklist in `macos/README.md` when handing off a
macOS change.

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

Desktop usage polls invoke the shell with `--no-update`. Git polling has its own
timer and must remain silent for current versions and errors. Only a newly
available revision belongs in the menu or notification stream.

### Preserve safe update behavior

Startup updates are fast-forward-only and must not overwrite tracked local
changes or operate from a nested/foreign Git root. Update tests before relaxing
any guard.

## Test organization

- `test_startup.py` uses source ordering assertions for launcher invariants.
- `test_pricing.py` extracts embedded pricing functions with `ast` and tests
  historical backfill.
- `test_model_filter.py` extracts terminal filter functions/classes with `ast`.
- `test_tray.py` imports the tray module directly and fakes GTK/native/update
  boundaries.
- `test_macos.py` imports the macOS adapter without Cocoa and tests portable
  configuration and escaping.

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
