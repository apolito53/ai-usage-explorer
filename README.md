# AI Usage Explorer

AI Usage Explorer is a local-first dashboard for personal Claude and Codex
usage. It reads the developer's own `ccusage` data, normalizes both providers
into one view, and shows costs, token volume, model breakdowns, trends, and
historical token rates.

| Interface | Platform | Purpose |
| --- | --- | --- |
| Terminal dashboard | Linux and macOS | Full interactive usage exploration |
| Panel indicator | Ubuntu and derivatives | At-a-glance Claude month/today cost |
| Menu-bar companion | macOS | At-a-glance Claude month/today cost |

The desktop companions are personal monitors. They read usage history from the
current machine; there is no hosted service or shared team account.

## Quick start

Requirements:

- Python 3 with `venv` support
- Node.js (Node 22 recommended)
- `ccusage`, `pnpm`, or npm/`npx`

Run the terminal dashboard:

```bash
./ai-usage-explorer.sh
```

Or explore the bundled sample without reading local usage:

```bash
./ai-usage-explorer.sh --demo --no-update --no-pricing-update
```

The launcher creates a local `.venv` and installs the Python UI dependency when
needed. It prefers an installed `ccusage`, then falls back to `pnpm dlx` or
`npx`. Common Homebrew paths and `~/.nvm/nvm.sh` are detected automatically.

## Desktop companions

On Ubuntu:

```bash
./ai-usage-explorer.sh --tray
```

See [Ubuntu tray setup](docs/UBUNTU_TRAY.md) for dependencies and autostart.

On macOS:

```bash
./macos/install.sh
```

See the [macOS setup guide](macos/README.md) for installation, removal, logs,
and the real-Mac validation checklist.

## Documentation

- [Usage reference](docs/USAGE.md) — CLI options, keyboard controls, and
  environment variables
- [Architecture](docs/ARCHITECTURE.md) — data flow, pricing, updates, and
  component boundaries
- [Development guide](docs/DEVELOPMENT.md) — tests, smoke checks, and change
  conventions
- [Codebase index](CODEBASE_INDEX.md) — file map and “where should I edit?”
  routing
- [Ubuntu tray setup](docs/UBUNTU_TRAY.md)
- [macOS menu-bar setup](macos/README.md)

## Network behavior

Usage history stays local, but a normal run may use the network to resolve
`ccusage`, refresh the LiteLLM pricing catalog, and check the configured Git
upstream. Use `--no-update` and `--no-pricing-update` when those checks are not
wanted. See the [usage reference](docs/USAGE.md#network-and-local-data) for the
full breakdown.
