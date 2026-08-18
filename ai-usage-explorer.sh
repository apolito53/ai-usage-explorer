#!/usr/bin/env bash
# Interactive AI usage explorer built on ccusage JSON output.
#
# Examples:
#   ./ai-usage-explorer.sh
#   ./ai-usage-explorer.sh --since 20260401
#   ./ai-usage-explorer.sh --group month
#   ./ai-usage-explorer.sh --refresh
#   ./ai-usage-explorer.sh --demo
#   ./ai-usage-explorer.sh --file /tmp/ccusage-daily.json
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
REQUIREMENTS_FILE="${SCRIPT_DIR}/requirements.txt"
PYTHON_DEP_MODULES=(rich)
VERSION_FILE="${SCRIPT_DIR}/VERSION"
VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null || printf '0.1.0')"
PRICING_HISTORY_FILE="${AI_USAGE_EXPLORER_PRICING_HISTORY:-${SCRIPT_DIR}/.ai-usage-pricing-history.json}"
ORIGINAL_ARGS=("$@")

SINCE="20260209"
UNTIL=""
PROJECT=""
JSON_FILE=""
OFFLINE=1
GROUP="day"
DUMP_JSON=0
DUMP_CLAUDE_TRAY_JSON=0
TRAY=0
INSTALL_TRAY_AUTOSTART=0
REMOVE_TRAY_AUTOSTART=0
NO_UPDATE="${AI_USAGE_EXPLORER_NO_UPDATE:-0}"
NO_PRICING_UPDATE="${AI_USAGE_EXPLORER_NO_PRICING_UPDATE:-0}"

usage() {
    cat <<'EOF'
Usage: ./ai-usage-explorer.sh [options]

Options:
  --since YYYYMMDD     Start date for ccusage daily data (default: 20260209)
  --until YYYYMMDD     End date for ccusage daily data
  --project NAME       Pass through Claude ccusage --project
  --group day|month    Initial grouping (default: day)
  --refresh            Fetch current model pricing instead of ccusage --offline
  --demo               Load bundled demo data instead of running ccusage
  --file PATH          Load an existing ccusage JSON file
  --tray               Show selectable Claude usage in the Ubuntu tray
  --install-tray-autostart
                       Launch the tray automatically after Ubuntu login
  --remove-tray-autostart
                       Remove the Ubuntu login startup entry
  --no-update          Skip the startup git update check
  --no-pricing-update  Skip the startup token price check
  --version            Show version and exit
  -h, --help           Show this help

Keyboard:
  j/k or ↑/↓           Move day selection
  pgup/pgdn            Page day selection
  tab                  Switch day list / chart focus
  g/G                  Jump to first/last day
  a                    Cycle provider filter
  m                    Open model filter menu
  p                    Toggle day/month grouping
  esc or v             Open date range menu
  space/enter          Expand selected row model breakdown
  ←/→                  Cycle sort column (prev/next)
  s                    Reverse sort order
  r                    Refresh data
  1-5                  Chart metric: cost, total, input, output, cache
  q                    Quit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dump-json) DUMP_JSON=1; shift ;;
        --dump-claude-month-json|--dump-claude-tray-json)
            DUMP_CLAUDE_TRAY_JSON=1
            shift
            ;;
        --since) SINCE="$2"; shift 2 ;;
        --until) UNTIL="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --group) GROUP="$2"; shift 2 ;;
        --refresh) OFFLINE=0; shift ;;
        --demo) JSON_FILE="${SCRIPT_DIR}/demo/usage-demo.json"; shift ;;
        --file) JSON_FILE="$2"; shift 2 ;;
        --tray) TRAY=1; shift ;;
        --install-tray-autostart) INSTALL_TRAY_AUTOSTART=1; shift ;;
        --remove-tray-autostart) REMOVE_TRAY_AUTOSTART=1; shift ;;
        --no-update) NO_UPDATE=1; shift ;;
        --no-pricing-update) NO_PRICING_UPDATE=1; shift ;;
        --version) echo "AI Usage Explorer ${VERSION}"; exit 0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

case "$GROUP" in
    day|month) ;;
    *) echo "ERROR: --group must be day or month" >&2; exit 1 ;;
esac

self_update() {
    if [ "$NO_UPDATE" = "1" ]; then
        return
    fi
    if ! command -v git >/dev/null 2>&1; then
        return
    fi
    if ! git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        return
    fi

    local git_root upstream local_rev remote_rev base_rev
    git_root="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel 2>/dev/null)" || return
    if [ "$git_root" != "$SCRIPT_DIR" ]; then
        return
    fi
    upstream="$(git -C "$SCRIPT_DIR" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" || return
    if ! git -C "$SCRIPT_DIR" fetch --quiet; then
        echo "Update check skipped: could not fetch ${upstream}." >&2
        return
    fi

    local_rev="$(git -C "$SCRIPT_DIR" rev-parse HEAD 2>/dev/null)" || return
    remote_rev="$(git -C "$SCRIPT_DIR" rev-parse "$upstream" 2>/dev/null)" || return
    base_rev="$(git -C "$SCRIPT_DIR" merge-base HEAD "$upstream" 2>/dev/null)" || return

    if [ "$local_rev" = "$remote_rev" ]; then
        return
    fi
    if [ "$local_rev" = "$base_rev" ]; then
        if ! git -C "$SCRIPT_DIR" diff --quiet || ! git -C "$SCRIPT_DIR" diff --cached --quiet; then
            echo "Update available on ${upstream}, but tracked local changes are present; skipping." >&2
            return
        fi
        echo "Updating AI Usage Explorer from ${local_rev:0:7} to ${remote_rev:0:7}..." >&2
        if git -C "$SCRIPT_DIR" pull --ff-only --quiet; then
            exec "$0" "$@"
        fi
        echo "Update check skipped: git pull --ff-only failed." >&2
        return
    fi
    if [ "$remote_rev" != "$base_rev" ]; then
        echo "Update check skipped: local branch has diverged from ${upstream}." >&2
    fi
}

find_python() {
    if [ -x "$PYTHON_BIN" ]; then
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="$(command -v python3)"
        return
    fi
    echo "ERROR: python3 is required to run the usage explorer." >&2
    exit 1
}

python_has_dependencies() {
    "$PYTHON_BIN" - "${PYTHON_DEP_MODULES[@]}" <<'PY'
import importlib.util
import sys

missing = [
    module
    for module in sys.argv[1:]
    if importlib.util.find_spec(module) is None
]
if missing:
    print(",".join(missing))
    sys.exit(1)
PY
}

ensure_python_dependencies() {
    find_python
    if python_has_dependencies >/dev/null; then
        return
    fi

    echo "Installing Python dependencies into ${VENV_DIR}..." >&2
    if [ ! -x "${VENV_DIR}/bin/python" ]; then
        if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
            echo "ERROR: Could not create ${VENV_DIR}. Install python3-venv or install dependencies manually." >&2
            exit 1
        fi
    fi

    PYTHON_BIN="${VENV_DIR}/bin/python"
    if ! "$PYTHON_BIN" -m pip install -r "$REQUIREMENTS_FILE"; then
        echo "ERROR: Could not install Python dependencies from ${REQUIREMENTS_FILE}." >&2
        exit 1
    fi
    if ! python_has_dependencies >/dev/null; then
        echo "ERROR: Python dependencies are still missing after installation." >&2
        exit 1
    fi
}

fetch_usage_data() {
    AI_USAGE_SINCE="$SINCE" \
    AI_USAGE_UNTIL="$UNTIL" \
    AI_USAGE_PROJECT="$PROJECT" \
    AI_USAGE_OFFLINE="$OFFLINE" \
    bash -lic '
        if ! command -v nvm >/dev/null 2>&1; then
            echo "ERROR: nvm is required to run ccusage. Install nvm and Node 22, or load your shell profile before running this script." >&2
            exit 1
        fi
        nvm use 22 >/dev/null
        if ! command -v pnpm >/dev/null 2>&1; then
            if command -v corepack >/dev/null 2>&1; then
                corepack enable pnpm >/dev/null 2>&1 || true
            fi
        fi
        if ! command -v pnpm >/dev/null 2>&1; then
            echo "ERROR: pnpm is required to run ccusage via pnpm dlx. Install pnpm or enable corepack for Node 22." >&2
            exit 1
        fi

        build_provider_args() {
            local provider="$1"
            local period="$2"
            args=("$provider" "$period" -s "$AI_USAGE_SINCE" --json)
            if [ "$provider" = "claude" ]; then
                args+=(-b)
            fi
            if [ -n "$AI_USAGE_UNTIL" ]; then
                args+=(-u "$AI_USAGE_UNTIL")
            fi
            if [ "$provider" = "claude" ] && [ -n "$AI_USAGE_PROJECT" ]; then
                args+=(-p "$AI_USAGE_PROJECT")
            fi
            if [ "$AI_USAGE_OFFLINE" -eq 1 ]; then
                args+=(--offline)
            fi
        }

        data_dir="$(mktemp -d)"
        trap "rm -rf \"$data_dir\"" EXIT

        for provider in claude codex; do
            for period in daily monthly; do
                build_provider_args "$provider" "$period"
                # pnpm dlx downloads ccusage on demand when it is not already cached.
                pnpm dlx ccusage "${args[@]}" > "$data_dir/$provider-$period.json"
            done
        done

        python3 - "$data_dir" <<'"'"'PY'"'"'
import json
import os
import sys

PROVIDERS = ("claude", "codex")


def read_provider(data_dir, provider, period):
    with open(os.path.join(data_dir, f"{provider}-{period}.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def int_value(row, key):
    return int(row.get(key, 0) or 0)


def float_value(row, key):
    return float(row.get(key, 0.0) or 0.0)


def row_period(row, period):
    if period == "monthly":
        return row.get("month") or row.get("date") or row.get("period") or ""
    return row.get("date") or row.get("period") or ""


def normalize_claude_row(row, period):
    normalized = {
        "date": row_period(row, period),
        "provider": "claude",
        "providers": ["claude"],
        "agent": "claude",
        "inputTokens": int_value(row, "inputTokens"),
        "outputTokens": int_value(row, "outputTokens"),
        "cacheCreationTokens": int_value(row, "cacheCreationTokens"),
        "cacheReadTokens": int_value(row, "cacheReadTokens"),
        "totalTokens": int_value(row, "totalTokens"),
        "totalCost": float_value(row, "totalCost"),
        "modelsUsed": list(row.get("modelsUsed", [])),
        "modelBreakdowns": list(row.get("modelBreakdowns", [])),
        "metadata": {"agents": ["claude"]},
    }
    if not normalized["modelsUsed"]:
        normalized["modelsUsed"] = [
            item.get("modelName")
            for item in normalized["modelBreakdowns"]
            if item.get("modelName")
        ]
    return normalized


def normalize_codex_row(row, period):
    models = row.get("models") or {}
    total_cost = float_value(row, "costUSD")
    total_model_tokens = sum(int_value(values, "totalTokens") for values in models.values())
    model_breakdowns = []
    for model_name, values in models.items():
        model_tokens = int_value(values, "totalTokens")
        if len(models) == 1:
            model_cost = total_cost
        elif total_model_tokens:
            model_cost = total_cost * (model_tokens / total_model_tokens)
        else:
            model_cost = 0.0
        model_breakdowns.append({
            "modelName": model_name,
            "inputTokens": int_value(values, "inputTokens"),
            "outputTokens": int_value(values, "outputTokens"),
            "cacheCreationTokens": int_value(values, "cacheCreationTokens"),
            "cacheReadTokens": int_value(values, "cacheReadTokens") or int_value(values, "cachedInputTokens"),
            "cost": model_cost,
        })

    return {
        "date": row_period(row, period),
        "provider": "codex",
        "providers": ["codex"],
        "agent": "codex",
        "inputTokens": int_value(row, "inputTokens"),
        "outputTokens": int_value(row, "outputTokens"),
        "cacheCreationTokens": int_value(row, "cacheCreationTokens"),
        "cacheReadTokens": int_value(row, "cacheReadTokens") or int_value(row, "cachedInputTokens"),
        "totalTokens": int_value(row, "totalTokens"),
        "totalCost": total_cost,
        "modelsUsed": list(models.keys()),
        "modelBreakdowns": model_breakdowns,
        "metadata": {"agents": ["codex"]},
    }


def normalize_rows(data, provider, period):
    rows = data.get(period, [])
    if provider == "claude":
        return [normalize_claude_row(row, period) for row in rows]
    return [normalize_codex_row(row, period) for row in rows]


def totals(rows):
    return {
        "inputTokens": sum(int_value(row, "inputTokens") for row in rows),
        "outputTokens": sum(int_value(row, "outputTokens") for row in rows),
        "cacheCreationTokens": sum(int_value(row, "cacheCreationTokens") for row in rows),
        "cacheReadTokens": sum(int_value(row, "cacheReadTokens") for row in rows),
        "totalTokens": sum(int_value(row, "totalTokens") for row in rows),
        "totalCost": sum(float_value(row, "totalCost") for row in rows),
    }


data_dir = sys.argv[1]
daily_rows = []
monthly_rows = []
for provider in PROVIDERS:
    daily_rows.extend(normalize_rows(read_provider(data_dir, provider, "daily"), provider, "daily"))
    monthly_rows.extend(normalize_rows(read_provider(data_dir, provider, "monthly"), provider, "monthly"))

print(json.dumps({
    "daily": daily_rows,
    "monthly": monthly_rows,
    "totals": totals(daily_rows),
}))
PY
    '
}

fetch_claude_tray_data() {
    AI_USAGE_SINCE="$SINCE" \
    AI_USAGE_PROJECT="$PROJECT" \
    AI_USAGE_OFFLINE="$OFFLINE" \
    bash -lic '
        if ! command -v nvm >/dev/null 2>&1; then
            echo "ERROR: nvm is required to run ccusage. Install nvm and Node 22, or load your shell profile before running this script." >&2
            exit 1
        fi
        nvm use 22 >/dev/null

        # Prefer an already-installed or pnpm-cached ccusage binary. A one-minute
        # tray poll should not ask the package registry the same question forever.
        ccusage_bin="$(command -v ccusage 2>/dev/null || true)"
        if [ -z "$ccusage_bin" ]; then
            cache_root="${XDG_CACHE_HOME:-$HOME/.cache}/pnpm/dlx"
            if [ -d "$cache_root" ]; then
                ccusage_bin="$(
                    find "$cache_root" -path "*/node_modules/.bin/ccusage" -type f -perm -u+x -printf "%T@ %p\n" 2>/dev/null \
                        | sort -nr \
                        | sed -n "1{s/^[^ ]* //;p;}"
                )"
            fi
        fi

        ensure_pnpm() {
            if ! command -v pnpm >/dev/null 2>&1; then
                if command -v corepack >/dev/null 2>&1; then
                    corepack enable pnpm >/dev/null 2>&1 || true
                fi
            fi
            if ! command -v pnpm >/dev/null 2>&1; then
                echo "ERROR: pnpm is required to run ccusage via pnpm dlx. Install pnpm or enable corepack for Node 22." >&2
                exit 1
            fi
        }

        run_ccusage() {
            period="$1"
            output_file="$2"
            args=(claude "$period" -s "$AI_USAGE_SINCE" --json -b)
            if [ -n "$AI_USAGE_PROJECT" ]; then
                args+=(-p "$AI_USAGE_PROJECT")
            fi
            if [ "$AI_USAGE_OFFLINE" -eq 1 ]; then
                args+=(--offline)
            fi
            if [ -n "$ccusage_bin" ] && "$ccusage_bin" "${args[@]}" > "$output_file"; then
                return
            fi
            ensure_pnpm
            pnpm dlx ccusage "${args[@]}" > "$output_file"
        }

        daily_file="$(mktemp)"
        monthly_file="$(mktemp)"
        trap "rm -f \"$daily_file\" \"$monthly_file\"" EXIT
        run_ccusage daily "$daily_file"
        run_ccusage monthly "$monthly_file"

        python3 - "$daily_file" "$monthly_file" <<PY
import json
import sys


def section(path, name):
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        rows = payload.get(name, [])
        return rows if isinstance(rows, list) else []
    return payload if isinstance(payload, list) else []


json.dump(
    {
        "daily": section(sys.argv[1], "daily"),
        "monthly": section(sys.argv[2], "monthly"),
    },
    sys.stdout,
)
PY
    '
}

find_tray_python() {
    if [ -x /usr/bin/python3 ]; then
        printf '%s\n' /usr/bin/python3
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        echo "ERROR: python3 is required for the Ubuntu tray indicator." >&2
        exit 1
    fi
}

run_tray() {
    local tray_python tray_args
    tray_python="$(find_tray_python)"

    tray_args=(
        --script "${SCRIPT_DIR}/ai-usage-explorer.sh"
        --pricing-history "$PRICING_HISTORY_FILE"
    )
    if [ -n "$PROJECT" ]; then
        tray_args+=(--project "$PROJECT")
    fi
    if [ "$OFFLINE" -eq 0 ]; then
        tray_args+=(--online)
    fi
    if [ "$NO_PRICING_UPDATE" -eq 1 ]; then
        tray_args+=(--no-pricing-update)
    fi
    exec "$tray_python" "${SCRIPT_DIR}/ai-usage-tray.py" "${tray_args[@]}"
}

manage_tray_autostart() {
    local action="$1" tray_python
    tray_python="$(find_tray_python)"
    "$tray_python" "${SCRIPT_DIR}/ai-usage-tray.py" \
        --script "${SCRIPT_DIR}/ai-usage-explorer.sh" \
        --pricing-history "$PRICING_HISTORY_FILE" \
        "--${action}-autostart"
}

refresh_pricing_history() {
    if [ "$NO_PRICING_UPDATE" = "1" ]; then
        return
    fi
    find_python
    "$PYTHON_BIN" - "$PRICING_HISTORY_FILE" "${1:-}" <<'PY' || echo "Pricing update skipped; using cached rates if available." >&2
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

HISTORY_PATH = sys.argv[1]
USAGE_PATH = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else ""
SOURCE_URL = os.environ.get(
    "AI_USAGE_EXPLORER_PRICING_URL",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
)


def builtin_price_cards():
    return {
        "claude-fable-5": {"input": 10.0, "output": 50.0, "cacheWrite": 12.5, "cacheRead": 1.0},
        "claude-mythos-5": {"input": 10.0, "output": 50.0, "cacheWrite": 12.5, "cacheRead": 1.0},
        "claude-opus-5": {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5},
        "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5},
        "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5},
        "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5},
        "claude-opus-4-5": {"input": 5.0, "output": 25.0, "cacheWrite": 6.25, "cacheRead": 0.5},
        "claude-opus-4-1": {"input": 15.0, "output": 75.0, "cacheWrite": 18.75, "cacheRead": 1.5},
        "claude-opus-4": {"input": 15.0, "output": 75.0, "cacheWrite": 18.75, "cacheRead": 1.5},
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cacheWrite": 3.75, "cacheRead": 0.3},
        "claude-sonnet-4-5": {"input": 3.0, "output": 15.0, "cacheWrite": 3.75, "cacheRead": 0.3},
        "claude-sonnet-4": {"input": 3.0, "output": 15.0, "cacheWrite": 3.75, "cacheRead": 0.3},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cacheWrite": 1.25, "cacheRead": 0.1},
        "claude-haiku-3-5": {"input": 0.8, "output": 4.0, "cacheWrite": 1.0, "cacheRead": 0.08},
        "gpt-5.5": {"input": 5.0, "output": 30.0, "cacheRead": 0.5},
        "gpt-5.4": {"input": 2.5, "output": 15.0, "cacheRead": 0.25},
        "gpt-5.4-mini": {"input": 0.75, "output": 4.5, "cacheRead": 0.075},
    }


def simplify_model_name(name):
    value = str(name or "").strip().lower()
    for prefix in ("anthropic/", "openai/", "azure/", "bedrock/", "vertex_ai/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    for suffix in ("-20251001", "-20250929"):
        value = value.replace(suffix, "")
    return value.replace("_", "-")


def load_usage_models(path):
    models = set()
    if not path:
        return models
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return models
    for section in ("daily", "monthly"):
        for row in data.get(section, []):
            for model in row.get("modelsUsed", []) or []:
                models.add(simplify_model_name(model))
            for item in row.get("modelBreakdowns", []) or []:
                if item.get("modelName"):
                    models.add(simplify_model_name(item["modelName"]))
    return models


def wanted_model(model, wanted):
    if not wanted:
        return True
    simplified = simplify_model_name(model)
    return any(simplified == item or simplified.startswith(item) or item.startswith(simplified) for item in wanted)


def first_number(row, *keys):
    for key in keys:
        value = row.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def to_per_million(value):
    if value is None:
        return None
    return round(float(value) * 1_000_000, 8)


def extract_litellm_rates(row):
    rates = {
        "input": to_per_million(first_number(row, "input_cost_per_token", "inputCostPerToken")),
        "output": to_per_million(first_number(row, "output_cost_per_token", "outputCostPerToken")),
        "cacheWrite": to_per_million(first_number(row, "cache_creation_input_token_cost", "cacheCreationInputTokenCost")),
        "cacheRead": to_per_million(first_number(row, "cache_read_input_token_cost", "cacheReadInputTokenCost")),
    }
    long_cache_write = to_per_million(first_number(row, "cache_creation_input_token_cost_above_200k_tokens", "cacheCreationInputTokenCostAbove200kTokens"))
    long_cache_read = to_per_million(first_number(row, "cache_read_input_token_cost_above_200k_tokens", "cacheReadInputTokenCostAbove200kTokens"))
    if long_cache_write is not None:
        rates["cacheWriteAbove200k"] = long_cache_write
    if long_cache_read is not None:
        rates["cacheReadAbove200k"] = long_cache_read
    return {key: value for key, value in rates.items() if value is not None}


def fetch_litellm_price_cards(wanted):
    request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "ai-usage-explorer"})
    with urllib.request.urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    cards = {}
    for model, row in payload.items():
        if not isinstance(row, dict) or not wanted_model(model, wanted):
            continue
        rates = extract_litellm_rates(row)
        if rates:
            cards[simplify_model_name(model)] = {
                "displayName": str(model),
                "rates": rates,
                "source": "litellm",
            }
    return cards


def load_history(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            history = json.load(handle)
    except (OSError, json.JSONDecodeError):
        history = {}
    if not isinstance(history.get("snapshots"), list):
        history["snapshots"] = []
    history["version"] = 1
    return history


def comparable_models(snapshot):
    models = snapshot.get("models", {})
    return json.dumps(models, sort_keys=True, separators=(",", ":"))


wanted_models = load_usage_models(USAGE_PATH)
cards = {
    model: {"displayName": model, "rates": rates, "source": "builtin"}
    for model, rates in builtin_price_cards().items()
    if wanted_model(model, wanted_models)
}
source_status = "builtin"
try:
    fetched = fetch_litellm_price_cards(wanted_models)
except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
    print(f"Pricing refresh warning: {exc}", file=sys.stderr)
else:
    if fetched:
        cards.update(fetched)
        source_status = "litellm"

now = datetime.now(timezone.utc).replace(microsecond=0)
snapshot = {
    "checkedAt": now.isoformat().replace("+00:00", "Z"),
    "effectiveDate": date.today().isoformat(),
    "sourceUrl": SOURCE_URL,
    "sourceStatus": source_status,
    "models": dict(sorted(cards.items())),
}

history = load_history(HISTORY_PATH)
history["lastCheckedAt"] = snapshot["checkedAt"]
history["lastSourceStatus"] = source_status
history["sourceUrl"] = SOURCE_URL
snapshots = history["snapshots"]
if snapshots and comparable_models(snapshots[-1]) == comparable_models(snapshot):
    snapshots[-1]["checkedAt"] = snapshot["checkedAt"]
    snapshots[-1]["sourceStatus"] = snapshot["sourceStatus"]
    snapshots[-1]["sourceUrl"] = snapshot["sourceUrl"]
else:
    snapshots.append(snapshot)

directory = os.path.dirname(HISTORY_PATH) or "."
os.makedirs(directory, exist_ok=True)
fd, tmp_path = tempfile.mkstemp(prefix=".pricing-history-", suffix=".json", dir=directory)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(history, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, HISTORY_PATH)
finally:
    if os.path.exists(tmp_path):
        os.unlink(tmp_path)
PY
}

if [ "$INSTALL_TRAY_AUTOSTART" -eq 1 ]; then
    manage_tray_autostart install
    exit 0
fi

if [ "$REMOVE_TRAY_AUTOSTART" -eq 1 ]; then
    manage_tray_autostart remove
    exit 0
fi

if [ "$DUMP_CLAUDE_TRAY_JSON" -eq 1 ]; then
    self_update "${ORIGINAL_ARGS[@]}"
    CLAUDE_TRAY_FILE="$(mktemp)"
    trap 'rm -f "$CLAUDE_TRAY_FILE"' EXIT
    fetch_claude_tray_data > "$CLAUDE_TRAY_FILE"
    refresh_pricing_history "$CLAUDE_TRAY_FILE"
    cat "$CLAUDE_TRAY_FILE"
    exit 0
fi

if [ "$DUMP_JSON" -eq 1 ]; then
    self_update "${ORIGINAL_ARGS[@]}"
    fetch_usage_data
    exit 0
fi

self_update "${ORIGINAL_ARGS[@]}"
if [ "$TRAY" -eq 1 ]; then
    run_tray
fi
ensure_python_dependencies

DATA_FILE="$JSON_FILE"
if [ -z "$DATA_FILE" ]; then
    DATA_FILE="$(mktemp)"
    trap 'rm -f "$DATA_FILE"' EXIT

    echo "Loading usage data..." >&2
    fetch_usage_data > "$DATA_FILE"
fi

refresh_pricing_history "$DATA_FILE"

"$PYTHON_BIN" - "$DATA_FILE" "$GROUP" "$0" "$JSON_FILE" "$SINCE" "$UNTIL" "$PROJECT" "$OFFLINE" "$PRICING_HISTORY_FILE" <<'PYTHON_EOF'
import json
import os
import queue
import re
import select
import subprocess
import sys
import termios
import threading
import tty
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: rich is required but could not be imported after dependency setup.", file=sys.stderr)
    sys.exit(1)


METRICS = [
    ("cost", "Cost", lambda row: float(row.get("totalCost", 0.0)), "${:,.2f}"),
    ("tokens", "Tokens", lambda row: int(row.get("totalTokens", 0)), "{:,.0f}"),
    ("input", "Input", lambda row: int(row.get("inputTokens", 0)), "{:,.0f}"),
    ("output", "Output", lambda row: int(row.get("outputTokens", 0)), "{:,.0f}"),
    ("cache", "Cache", lambda row: int(row.get("cacheCreationTokens", 0)) + int(row.get("cacheReadTokens", 0)), "{:,.0f}"),
]

MODEL_COLORS = [
    "bright_cyan", "bright_magenta", "bright_yellow", "bright_green",
    "bright_blue", "bright_red", "cyan", "magenta", "green", "yellow",
]

PROVIDER_ORDER = ["claude", "codex"]
PROVIDER_LABELS = {
    "claude": "Claude",
    "codex": "Codex",
}

SORT_COLUMNS = [
    ("date", "Date"),
    ("cost", "Cost"),
    ("tokens", "Total"),
    ("input", "Input"),
    ("output", "Output"),
    ("cache", "Cache"),
    ("providers", "Provider"),
    ("models", "Models"),
]
SORT_LABELS = {key: label for key, label in SORT_COLUMNS}
SORT_KEYS = [key for key, _label in SORT_COLUMNS]

DATE_RANGES = [
    ("all", "All loaded data", None),
    ("mtd", "Month to date", "month"),
    ("last_7", "Last 7 days", "days_7"),
    ("last_30", "Last 30 days", "days_30"),
    ("custom", "Custom range", "custom"),
]
DATE_RANGE_LABELS = {key: label for key, label, _kind in DATE_RANGES}
DATE_RANGE_KEYS = [key for key, _label, _kind in DATE_RANGES]
SPINNER_FRAMES = ["|", "/", "-", "\\"]
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def compact_model(name: str) -> str:
    name = name.replace("claude-", "")
    for suffix in ("-20251001", "-20250929"):
        name = name.replace(suffix, "")
    return name


def compact_provider(name: str) -> str:
    return PROVIDER_LABELS.get(name, name.title())


def compact_providers(names: List[str]) -> str:
    return ", ".join(compact_provider(name) for name in names) or "Unknown"


def fmt_int(value: float) -> str:
    return f"{int(value):,}"


def fmt_cost(value: float) -> str:
    return f"${value:,.2f}"


def fmt_rate(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:,.2f}"
    return f"${value:,.4f}"


def read_rtk_gain() -> Optional[Dict]:
    try:
        result = subprocess.run(
            ["rtk", "gain"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    stats = {"commands": []}
    in_command_table = False
    for raw_line in ANSI_ESCAPE.sub("", result.stdout).splitlines():
        line_text = raw_line.rstrip()
        line = " ".join(raw_line.split())
        if line.startswith("Total commands:"):
            stats["total_commands"] = line.split(":", 1)[1].strip()
        elif line.startswith("Input tokens:"):
            stats["input_tokens"] = line.split(":", 1)[1].strip()
        elif line.startswith("Output tokens:"):
            stats["output_tokens"] = line.split(":", 1)[1].strip()
        elif line.startswith("Tokens saved:"):
            stats["tokens_saved"] = line.split(":", 1)[1].strip()
        elif line.startswith("Total exec time:"):
            stats["exec_time"] = line.split(":", 1)[1].strip()
        elif line.startswith("Efficiency meter:"):
            meter = line.split(":", 1)[1].strip()
            stats["efficiency"] = meter.split()[-1] if meter else ""
        elif line == "By Command":
            in_command_table = True
        elif in_command_table:
            match = re.match(
                r"^\s*\d+\.\s+(?P<command>.*?)\s{2,}(?P<count>\d+)\s+(?P<saved>\S+)\s+(?P<avg>\S+)\s+(?P<time>\S+)\s+",
                line_text,
            )
            if match:
                stats["commands"].append(match.groupdict())

    if not stats.get("tokens_saved") and not stats["commands"]:
        return None
    return stats


def parse_row_date(value: str) -> Optional[datetime]:
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    return None


def parse_custom_date(value: str) -> Optional[datetime]:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def row_date(row: Dict) -> str:
    return str(row.get("date") or row.get("period") or "")


def ordered_providers(names: List[str]) -> List[str]:
    return sorted(
        names,
        key=lambda name: (
            PROVIDER_ORDER.index(name) if name in PROVIDER_ORDER else len(PROVIDER_ORDER),
            name,
        ),
    )


def infer_model_provider(model: str) -> Optional[str]:
    normalized = model.lower()
    if normalized.startswith("claude-") or "claude" in normalized:
        return "claude"
    if normalized.startswith("gpt-") or "codex" in normalized or normalized.startswith(("o1", "o3", "o4")):
        return "codex"
    return None


def model_menu_marker(selection: Set[str], models: Set[str]) -> str:
    if models and models.issubset(selection):
        return "[x]"
    if models.intersection(selection):
        return "[-]"
    return "[ ]"


def toggle_model_group(selection: Set[str], models: Set[str]) -> Set[str]:
    if not models:
        return selection
    if models.issubset(selection):
        return selection - models
    return selection | models


def row_model_names(row: Dict) -> List[str]:
    breakdowns = [
        item.get("modelName")
        for item in row.get("modelBreakdowns", [])
        if item.get("modelName")
    ]
    if breakdowns:
        return breakdowns
    return [
        model
        for model in row.get("modelsUsed", [])
        if model
    ]


def row_provider_names(row: Dict) -> List[str]:
    providers = []

    def add(provider: str):
        provider = str(provider).strip().lower()
        if provider and provider != "all" and provider not in providers:
            providers.append(provider)

    explicit = row.get("providers") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    for provider in explicit:
        add(provider)

    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    agents = metadata.get("agents") or []
    if isinstance(agents, str):
        agents = [agents]
    for agent in agents:
        add(agent)

    if row.get("agent"):
        add(row.get("agent"))

    for model in row_model_names(row):
        provider = infer_model_provider(model)
        if provider:
            add(provider)

    return ordered_providers(providers)


def read_pricing_history(path: str) -> Dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"snapshots": []}
    if not isinstance(history.get("snapshots"), list):
        history["snapshots"] = []
    history["snapshots"] = sorted(
        [snapshot for snapshot in history["snapshots"] if isinstance(snapshot, dict)],
        key=lambda snapshot: str(snapshot.get("effectiveDate") or ""),
    )
    return history


def simplify_model_name(name: str) -> str:
    value = str(name or "").strip().lower()
    for prefix in ("anthropic/", "openai/", "azure/", "bedrock/", "vertex_ai/"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    for suffix in ("-20251001", "-20250929"):
        value = value.replace(suffix, "")
    return value.replace("_", "-")


def pricing_snapshot_for_date(history: Dict, date_value: str) -> Optional[Dict]:
    snapshots = history.get("snapshots", [])
    if not snapshots:
        return None
    parsed = parse_row_date(date_value)
    target = parsed.date().isoformat() if parsed else ""
    if not target:
        return snapshots[-1]
    chosen = None
    for snapshot in snapshots:
        effective = str(snapshot.get("effectiveDate") or "")
        if effective and effective <= target:
            chosen = snapshot
    return chosen or snapshots[0]


def model_pricing_record(snapshot: Optional[Dict], model: str) -> Optional[Dict]:
    if not snapshot:
        return None
    models = snapshot.get("models", {})
    if not isinstance(models, dict):
        return None
    simplified = simplify_model_name(model)
    candidates = [
        simplified,
        str(model or "").strip().lower(),
    ]
    for candidate in candidates:
        if candidate in models:
            return models[candidate]

    for key, record in models.items():
        normalized_key = simplify_model_name(key)
        if normalized_key == simplified or normalized_key.startswith(simplified) or simplified.startswith(normalized_key):
            return record
    return None


def rate_value(record: Optional[Dict], key: str) -> Optional[float]:
    if not record:
        return None
    rates = record.get("rates") if isinstance(record.get("rates"), dict) else record
    value = rates.get(key) if isinstance(rates, dict) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def estimated_model_cost(item: Dict, record: Optional[Dict]) -> Optional[float]:
    total = 0.0
    has_usage = False
    for token_key, rate_key in (
        ("inputTokens", "input"),
        ("outputTokens", "output"),
        ("cacheCreationTokens", "cacheWrite"),
        ("cacheReadTokens", "cacheRead"),
    ):
        try:
            tokens = int(item.get(token_key, 0) or 0)
        except (TypeError, ValueError):
            return None
        if tokens <= 0:
            continue
        has_usage = True
        rate = rate_value(record, rate_key)
        if rate is None:
            return None
        total += tokens * rate / 1_000_000
    return total if has_usage else None


def backfill_missing_costs(data: Dict, history: Dict) -> int:
    updated = 0
    daily_changed = False
    for section in ("daily", "monthly"):
        rows = data.get(section, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            breakdowns = row.get("modelBreakdowns", [])
            if not isinstance(breakdowns, list):
                continue
            snapshot = pricing_snapshot_for_date(history, row_date(row))
            row_changed = False
            for item in breakdowns:
                if not isinstance(item, dict):
                    continue
                try:
                    current_cost = float(item.get("cost", 0.0) or 0.0)
                except (TypeError, ValueError):
                    current_cost = 0.0
                if current_cost != 0.0:
                    continue
                record = model_pricing_record(snapshot, item.get("modelName", ""))
                estimated = estimated_model_cost(item, record)
                if estimated is None or estimated <= 0.0:
                    continue
                item["cost"] = estimated
                row_changed = True
                updated += 1
            if row_changed:
                row["totalCost"] = sum(
                    float(item.get("cost", 0.0) or 0.0)
                    for item in breakdowns
                    if isinstance(item, dict)
                )
                daily_changed = daily_changed or section == "daily"

    if daily_changed and isinstance(data.get("totals"), dict):
        data["totals"]["totalCost"] = sum(
            float(row.get("totalCost", 0.0) or 0.0)
            for row in data.get("daily", [])
            if isinstance(row, dict)
        )
    return updated


def detail_items(row: Dict) -> List[Dict]:
    items = []
    row_providers = row_provider_names(row)
    for item in row.get("modelBreakdowns", []):
        model = item.get("modelName", "")
        provider = infer_model_provider(model)
        items.append({
            "label": compact_model(model),
            "providers": [provider] if provider else row_providers,
            "cost": float(item.get("cost", 0.0)),
            "inputTokens": int(item.get("inputTokens", 0)),
            "outputTokens": int(item.get("outputTokens", 0)),
            "cacheCreationTokens": int(item.get("cacheCreationTokens", 0)),
            "cacheReadTokens": int(item.get("cacheReadTokens", 0)),
            "aggregate": False,
        })
    return items


@dataclass
class State:
    selected: int = 0
    provider_index: int = 0
    # None means "all models"; an empty set is a valid filter that shows no models.
    selected_models: Optional[Set[str]] = None
    model_menu_open: bool = False
    model_menu_index: int = 0
    model_menu_viewport: int = 0
    # Draft model selection copied from selected_models when the picker opens.
    model_menu_selection: Set[str] = field(default_factory=set)
    sort_key: str = "date"
    sort_desc: bool = True
    metric_index: int = 0
    viewport: int = 0
    expanded: bool = False
    date_range: str = "mtd"
    range_menu_open: bool = False
    range_menu_index: int = 0
    custom_range_start: str = ""
    custom_range_end: str = ""
    range_focus: str = "menu"
    range_field_index: int = 0
    range_start_input: str = ""
    range_end_input: str = ""
    range_error: str = ""
    focus: str = "days"
    chart_offset: int = 0
    status: str = ""
    refreshing: bool = False
    spinner_index: int = 0


class UsageExplorer:
    def __init__(self, data: Dict, source_path: str, script_path: str, file_path: str, since: str, until: str, project: str, offline: str, pricing_history_path: str):
        self.console = Console()
        self.source_path = source_path
        self.script_path = script_path
        self.file_path = file_path
        self.since = since
        self.until = until
        self.project = project
        self.offline = offline
        self.pricing_history_path = pricing_history_path
        self.pricing_history = read_pricing_history(pricing_history_path)
        self.state = State()
        self.rtk_gain = read_rtk_gain()
        self.load_data(data)
        self.running = True
        self._tty = None
        self.page_size = 10
        self.chart_page_size = 10
        self._refresh_queue = queue.Queue()
        self._refresh_thread = None

    def load_data(self, data: Dict):
        backfill_missing_costs(data, self.pricing_history)
        self.rows = data.get("daily", [])
        self.totals = data.get("totals", {})
        self.providers = self._providers()
        self.models = self._models()
        self.model_colors = {m: MODEL_COLORS[i % len(MODEL_COLORS)] for i, m in enumerate(self.models)}
        self.state.provider_index = min(self.state.provider_index, len(self.provider_filter_options()) - 1)
        if self.state.selected_models is not None:
            self.state.selected_models &= set(self.models)
        self.state.selected = 0
        self.state.viewport = 0
        self.state.chart_offset = 0
        self.state.expanded = False

    def _providers(self) -> List[str]:
        seen = []
        for row in self.rows:
            for provider in row_provider_names(row):
                if provider not in seen:
                    seen.append(provider)
        return ordered_providers(seen)

    def _models(self) -> List[str]:
        seen = []
        for row in self.rows:
            for model in row_model_names(row):
                if model not in seen:
                    seen.append(model)
        return sorted(seen)

    def provider_filter_options(self):
        options = [("all", "All")]
        for provider in self.providers:
            options.append((provider, compact_provider(provider)))
        if any(len(row_provider_names(row)) > 1 for row in self.rows):
            options.append(("mixed", "Mixed"))
        return options

    def selected_provider_filter(self):
        options = self.provider_filter_options()
        if not options:
            return ("all", "All")
        if self.state.provider_index >= len(options):
            self.state.provider_index = 0
        return options[self.state.provider_index]

    def selected_provider_label(self) -> str:
        return self.selected_provider_filter()[1]

    def row_matches_provider_filter(self, row: Dict) -> bool:
        key, _label = self.selected_provider_filter()
        if key == "all":
            return True
        providers = row_provider_names(row)
        if key == "mixed":
            return len(providers) > 1
        return providers == [key]

    def selected_models(self) -> Optional[Set[str]]:
        # Provider and model filters are independent; filtering applies both at render time.
        if self.state.selected_models is None:
            return None
        return self.state.selected_models & set(self.models)

    def model_filter_label(self) -> str:
        selected = self.selected_models()
        if selected is None:
            return "All"
        models = sorted(selected)
        if not models:
            return "None"
        if len(models) == 1:
            return compact_model(models[0])
        return f"{len(models)} selected"

    def date_range_start(self) -> Optional[datetime]:
        today = datetime.now()
        if self.state.date_range == "mtd":
            return today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if self.state.date_range == "last_7":
            return (today - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.state.date_range == "last_30":
            return (today - timedelta(days=29)).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.state.date_range == "custom":
            return parse_row_date(self.state.custom_range_start)
        return None

    def date_range_end(self) -> Optional[datetime]:
        if self.state.date_range == "custom":
            end = parse_row_date(self.state.custom_range_end)
            if end:
                return end.replace(hour=23, minute=59, second=59, microsecond=999999)
        return None

    def filtered_rows(self) -> List[Dict]:
        models = self.selected_models()
        rows = self.rows
        start = self.date_range_start()
        end = self.date_range_end()
        if start or end:
            rows = [
                row for row in rows
                if (parsed := parse_row_date(row_date(row)))
                and (not start or parsed >= start)
                and (not end or parsed <= end)
            ]
        rows = [
            row for row in rows
            if self.row_matches_provider_filter(row)
        ]
        if models is not None:
            rows = [
                row for row in rows
                if models.intersection(row_model_names(row))
            ]

        key = self.state.sort_key
        def sorter(row: Dict):
            effective = self.model_row_values(row, models)
            if key == "cost":
                return float(effective.get("totalCost", 0.0))
            if key == "tokens":
                return int(effective.get("totalTokens", 0))
            if key == "input":
                return int(effective.get("inputTokens", 0))
            if key == "output":
                return int(effective.get("outputTokens", 0))
            if key == "cache":
                return int(effective.get("cacheCreationTokens", 0)) + int(effective.get("cacheReadTokens", 0))
            if key == "providers":
                return compact_providers(row_provider_names(effective))
            if key == "models":
                return ", ".join(compact_model(name) for name in effective.get("modelsUsed", []))
            return row_date(effective)

        return sorted(rows, key=sorter, reverse=self.state.sort_desc)

    def model_row_values(self, row: Dict, models: Optional[Set[str]]) -> Dict:
        if models is None:
            return row
        matched = [
            item for item in row.get("modelBreakdowns", [])
            if item.get("modelName") in models
        ]
        if matched:
            input_tokens = sum(int(item.get("inputTokens", 0)) for item in matched)
            output_tokens = sum(int(item.get("outputTokens", 0)) for item in matched)
            cache_creation_tokens = sum(int(item.get("cacheCreationTokens", 0)) for item in matched)
            cache_read_tokens = sum(int(item.get("cacheReadTokens", 0)) for item in matched)
            matched_models = [
                item.get("modelName")
                for item in matched
                if item.get("modelName")
            ]
            matched_providers = ordered_providers(list({
                provider for provider in (infer_model_provider(model) for model in matched_models)
                if provider
            }))
            return {
                "date": row_date(row),
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheCreationTokens": cache_creation_tokens,
                "cacheReadTokens": cache_read_tokens,
                "totalTokens": input_tokens + output_tokens + cache_creation_tokens + cache_read_tokens,
                "totalCost": sum(float(item.get("cost", 0.0)) for item in matched),
                "providers": matched_providers or row_provider_names(row),
                "modelsUsed": matched_models,
                "modelBreakdowns": matched,
            }
        if models.intersection(row_model_names(row)):
            return row
        return row

    def summary(self, rows: List[Dict]) -> Dict:
        models = self.selected_models()
        effective = [self.model_row_values(row, models) for row in rows]
        total_cost = sum(float(row.get("totalCost", 0.0)) for row in effective)
        total_tokens = sum(int(row.get("totalTokens", 0)) for row in effective)
        days = len(effective)
        avg_cost = total_cost / days if days else 0.0
        peak = max(effective, key=lambda row: float(row.get("totalCost", 0.0)), default=None)
        return {
            "days": days,
            "cost": total_cost,
            "tokens": total_tokens,
            "avg_cost": avg_cost,
            "peak": peak,
        }

    def spinner_frame(self) -> str:
        return SPINNER_FRAMES[self.state.spinner_index % len(SPINNER_FRAMES)]

    def render_header(self, rows: List[Dict]) -> Panel:
        metric = METRICS[self.state.metric_index][1]
        sort_label = SORT_LABELS.get(self.state.sort_key, self.state.sort_key).lower()
        sort_arrow = "▼" if self.state.sort_desc else "▲"
        range_label = DATE_RANGE_LABELS.get(self.state.date_range, self.state.date_range)
        if self.state.date_range == "custom":
            range_label = f"{self.state.custom_range_start}..{self.state.custom_range_end}"
        text = Text()
        text.append(" AI Usage Explorer ", style="bold white on blue")
        text.append(f"  Provider: {self.selected_provider_label()}  ", style="bright_magenta")
        text.append(f"  Models: {self.model_filter_label()}  ", style="magenta")
        text.append(f"  Range: {range_label}  ", style="yellow")
        text.append(f"  Metric: {metric}  ", style="bright_blue")
        text.append(f"  Sort: {sort_label} {sort_arrow}  ", style="white")
        status = self.state.status
        status_style = "bright_green"
        if self.state.refreshing:
            status = f"{self.spinner_frame()} Refreshing..."
            status_style = "bright_yellow"
        elif self.state.status.startswith("Refresh failed"):
            status_style = "bold red"
        if status:
            text.append(f"  {status}", style=status_style)
        return Panel(text, border_style="dim")

    def column_header(self, key: str, label: str) -> Text:
        text = Text(label)
        if self.state.sort_key == key:
            text.append(" " + ("▼" if self.state.sort_desc else "▲"))
            text.stylize("bold black on bright_yellow")
        return text

    def render_metrics_footer(self, rows: List[Dict]) -> Group:
        summary = self.summary(rows)
        totals = Text()
        totals.append(" TOTALS ", style="bold black on bright_green")
        totals.append(f"  Rows: {summary['days']}  ", style="bold")
        totals.append(f"Cost: {fmt_cost(summary['cost'])}  ", style="bold green")
        totals.append(f"Tokens: {fmt_int(summary['tokens'])}  ", style="bold cyan")
        totals.append(f"Avg/day: {fmt_cost(summary['avg_cost'])}  ", style="bold yellow")
        if summary["peak"]:
            peak = summary["peak"]
            totals.append(
                f"Peak: {row_date(peak)} {fmt_cost(float(peak.get('totalCost', 0)))}",
                style="bold red",
            )
        return Group(totals)

    def render_days(self, rows: List[Dict], height: int) -> Group:
        models = self.selected_models()
        max_lines = max(height - 6, 0)
        self.page_size = max(max_lines, 1)
        if self.state.selected >= len(rows):
            self.state.selected = max(0, len(rows) - 1)
        if self.state.selected < self.state.viewport:
            self.state.viewport = self.state.selected
        selected_row = self.model_row_values(rows[self.state.selected], models) if rows else {}
        selected_breakdown_count = len(detail_items(selected_row)) if self.state.expanded and rows else 0
        selected_gap_count = 1 if selected_breakdown_count else 0
        selected_block_lines = 1 + selected_breakdown_count + selected_gap_count
        if max_lines:
            selected_lines_from_viewport = self.state.selected - self.state.viewport + selected_block_lines
            if selected_lines_from_viewport > max_lines:
                rows_before_selected = max(max_lines - selected_block_lines, 0)
                self.state.viewport = self.state.selected - rows_before_selected
        self.state.viewport = max(0, min(self.state.viewport, max(0, len(rows) - 1)))

        table = Table(show_header=True, header_style="bold cyan", box=None, expand=True)
        table.add_column(self.column_header("date", "Date"), no_wrap=True)
        table.add_column(self.column_header("providers", "Provider"), no_wrap=True)
        table.add_column(self.column_header("cost", "Cost"), justify="right", no_wrap=True)
        table.add_column(self.column_header("tokens", "Total"), justify="right", no_wrap=True)
        table.add_column(self.column_header("input", "Input"), justify="right", no_wrap=True)
        table.add_column(self.column_header("output", "Output"), justify="right", no_wrap=True)
        table.add_column(self.column_header("cache", "Cache"), justify="right", no_wrap=True)
        table.add_column(self.column_header("models", "Models"), overflow="ellipsis", no_wrap=True)

        used_lines = 0
        for absolute in range(self.state.viewport, len(rows)):
            if used_lines >= max_lines:
                break
            original = rows[absolute]
            row = self.model_row_values(original, models)
            selected = absolute == self.state.selected
            row_style = "bold black on bright_white" if selected else ""
            models_text = Text()
            model_style = "black" if selected else None
            separator_style = "black" if selected else "dim"
            provider_text = Text(compact_providers(row_provider_names(row)), style="black" if selected else "bright_magenta")
            for n, model_name in enumerate(row_model_names(row)):
                if n:
                    models_text.append(", ", style=separator_style)
                models_text.append(compact_model(model_name), style=model_style or self.model_colors.get(model_name, "white"))
            cache_tokens = int(row.get("cacheCreationTokens", 0)) + int(row.get("cacheReadTokens", 0))
            table.add_row(
                row_date(row),
                provider_text,
                fmt_cost(float(row.get("totalCost", 0.0))),
                fmt_int(row.get("totalTokens", 0)),
                fmt_int(row.get("inputTokens", 0)),
                fmt_int(row.get("outputTokens", 0)),
                fmt_int(cache_tokens),
                models_text,
                style=row_style,
            )
            used_lines += 1
            if selected and self.state.expanded:
                rendered_breakdowns = False
                for item in detail_items(row):
                    if used_lines >= max_lines:
                        break
                    rendered_breakdowns = True
                    cache_create = int(item.get("cacheCreationTokens", 0))
                    cache_read = int(item.get("cacheReadTokens", 0))
                    cache_tokens = cache_create + cache_read
                    input_tokens = int(item.get("inputTokens", 0))
                    output_tokens = int(item.get("outputTokens", 0))
                    total_tokens = input_tokens + output_tokens + cache_tokens
                    table.add_row(
                        "",
                        Text("  " + compact_providers(item.get("providers", [])), style="bold white"),
                        fmt_cost(float(item.get("cost", 0.0))),
                        fmt_int(total_tokens),
                        fmt_int(input_tokens),
                        fmt_int(output_tokens),
                        fmt_int(cache_tokens),
                        Text("  " + item.get("label", ""), style="bold white"),
                        style="white on grey15",
                    )
                    used_lines += 1
                if rendered_breakdowns and used_lines < max_lines:
                    table.add_row("", "", "", "", "", "", "", "")
                    used_lines += 1
        return Group(table, Text(""), self.render_metrics_footer(rows))

    def chart_rows(self, rows: List[Dict]) -> List[Dict]:
        models = self.selected_models()
        return [
            self.model_row_values(row, models)
            for row in rows
        ]

    def render_chart(self, rows: List[Dict], height: int) -> Panel:
        metric_key, metric_label, getter, fmt = METRICS[self.state.metric_index]
        chart_rows = self.chart_rows(rows)
        visible_rows = max(height - 3, 0)
        self.chart_page_size = max(visible_rows, 1)
        max_offset = max(len(chart_rows) - visible_rows, 0)
        self.state.chart_offset = max(0, min(self.state.chart_offset, max_offset))
        chart_source = chart_rows[self.state.chart_offset:self.state.chart_offset + visible_rows] if visible_rows else []
        max_value = max((getter(row) for row in chart_rows), default=0)
        width = max(min((self.console.width or 100) - 52, 42), 10)
        text = Text()
        for row in chart_source:
            value = getter(row)
            bar_len = int((value / max_value) * width) if max_value else 0
            bar = "█" * max(bar_len, 1 if value else 0)
            style = "green" if metric_key == "cost" else "cyan"
            text.append(f"{row_date(row)} ", style="dim")
            text.append(f"{bar:<{width}} ", style=style)
            text.append((fmt.format(value) if metric_key != "cost" else fmt_cost(value)) + "\n", style="bold")
        title = f"[bold]{metric_label} Trend[/bold]"
        if self.state.focus == "chart":
            title += " [bold yellow](focused)[/bold yellow]"
        return Panel(text or Text("No data", style="dim"), title=title, border_style="bright_yellow" if self.state.focus == "chart" else "blue")

    def render_detail(self, rows: List[Dict]) -> Panel:
        if not rows:
            return Panel(Text("No rows match the current filter", style="dim"), title="[bold]Token Rates[/bold]")
        row = self.model_row_values(rows[self.state.selected], self.selected_models())
        date_value = row_date(row)
        snapshot = pricing_snapshot_for_date(self.pricing_history, date_value)
        table = Table(show_header=True, header_style="bold cyan", box=None, expand=True)
        table.add_column("Provider")
        table.add_column("Model", overflow="ellipsis", no_wrap=True)
        table.add_column("Input", justify="right", no_wrap=True)
        table.add_column("Output", justify="right", no_wrap=True)
        table.add_column("Cache Write", justify="right", no_wrap=True)
        table.add_column("Cache Read", justify="right", no_wrap=True)
        models = row_model_names(row)
        if not models:
            return Panel(Text("No models available for this row", style="dim"), title=f"[bold]Token Rates: {date_value}[/bold]", border_style="magenta")
        for model in models:
            provider = infer_model_provider(model)
            record = model_pricing_record(snapshot, model)
            table.add_row(
                compact_provider(provider) if provider else "Unknown",
                Text(compact_model(model), style=self.model_colors.get(model, "bright_cyan")),
                fmt_rate(rate_value(record, "input")),
                fmt_rate(rate_value(record, "output")),
                fmt_rate(rate_value(record, "cacheWrite")),
                fmt_rate(rate_value(record, "cacheRead")),
            )
        content = [table]
        if not snapshot:
            content.append(Text("No pricing history available yet", style="dim"))
        subtitle = "$/M tokens"
        if snapshot and snapshot.get("effectiveDate"):
            subtitle += f" • snapshot {snapshot['effectiveDate']}"
        return Panel(
            Group(*content),
            title=f"[bold]Token Rates: {date_value}[/bold]",
            subtitle=subtitle,
            border_style="magenta",
        )

    def render_rtk_gain(self) -> Panel:
        if not self.rtk_gain:
            return Panel(Text("rtk gain unavailable", style="dim"), title="[bold]RTK Gain[/bold]", border_style="bright_blue")

        stats = self.rtk_gain
        summary = Text()
        summary.append("Saved ", style="dim")
        summary.append(str(stats.get("tokens_saved", "Unknown")), style="bold bright_blue")
        if stats.get("total_commands"):
            summary.append(f"\nCommands {stats['total_commands']}", style="white")
        if stats.get("exec_time"):
            summary.append(f"  Time {stats['exec_time']}", style="dim")
        if stats.get("efficiency"):
            summary.append(f"\nEfficiency {stats['efficiency']}", style="bright_green")

        commands = Table(show_header=True, header_style="bold cyan", box=None, expand=True)
        commands.add_column("Command", overflow="ellipsis", no_wrap=True)
        commands.add_column("Saved", justify="right", no_wrap=True)
        commands.add_column("Avg", justify="right", no_wrap=True)
        for command in stats.get("commands", [])[:3]:
            commands.add_row(
                command.get("command", ""),
                command.get("saved", ""),
                command.get("avg", ""),
            )

        content = [summary]
        if stats.get("commands"):
            content.extend([Text(""), commands])
        return Panel(
            Group(*content),
            title="[bold]RTK Gain[/bold]",
            border_style="bright_blue",
        )

    def render_help(self) -> Panel:
        controls = Text()
        controls.append("j/k/↑↓", style="bold cyan")
        controls.append(" move  ")
        controls.append("pgup/pgdn", style="bold cyan")
        controls.append(" page  ")
        controls.append("tab", style="bold cyan")
        controls.append(" focus  ")
        controls.append("a", style="bold cyan")
        controls.append(" provider  ")
        controls.append("m", style="bold cyan")
        controls.append(" models  ")
        controls.append("esc/v", style="bold cyan")
        controls.append(" range  ")
        controls.append("space", style="bold cyan")
        controls.append(" expand  ")
        controls.append("←/→", style="bold cyan")
        controls.append(" sort column  ")
        controls.append("s", style="bold cyan")
        controls.append(" reverse sort  ")
        controls.append("r", style="bold cyan")
        controls.append(" refresh  ")
        controls.append("1-5", style="bold cyan")
        controls.append(" metric  ")
        controls.append("q", style="bold cyan")
        controls.append(" quit")
        return Panel(Align.center(controls), border_style="dim")

    def model_menu_items(self) -> List[Dict]:
        items = [{"type": "all", "label": "All models"}]
        grouped = {}
        for model in self.models:
            provider = infer_model_provider(model) or "other"
            grouped.setdefault(provider, []).append(model)

        for provider in ordered_providers(list(grouped.keys())):
            provider_models = sorted(grouped[provider])
            items.append({
                "type": "provider",
                "provider": provider,
                "models": provider_models,
                "label": compact_provider(provider),
            })
            for model in provider_models:
                items.append({"type": "model", "model": model, "label": compact_model(model)})
        return items

    def model_menu_selectable_indices(self) -> List[int]:
        return [
            idx for idx, item in enumerate(self.model_menu_items())
            if item.get("type") in ("all", "provider", "model")
        ]

    def move_model_menu(self, delta: int):
        selectable = self.model_menu_selectable_indices()
        if not selectable:
            self.state.model_menu_index = 0
            return
        try:
            position = selectable.index(self.state.model_menu_index)
        except ValueError:
            position = 0
        position = max(0, min(position + delta, len(selectable) - 1))
        self.state.model_menu_index = selectable[position]

    def render_model_menu(self) -> Panel:
        items = self.model_menu_items()
        selectable = self.model_menu_selectable_indices()
        if selectable and self.state.model_menu_index not in selectable:
            self.state.model_menu_index = selectable[0]

        visible_rows = max(min((self.console.height or 32) - 10, 16), 6)
        max_offset = max(len(items) - visible_rows, 0)
        if self.state.model_menu_index < self.state.model_menu_viewport:
            self.state.model_menu_viewport = self.state.model_menu_index
        if self.state.model_menu_index >= self.state.model_menu_viewport + visible_rows:
            self.state.model_menu_viewport = self.state.model_menu_index - visible_rows + 1
        self.state.model_menu_viewport = max(0, min(self.state.model_menu_viewport, max_offset))

        all_models = set(self.models)
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("Selected", width=3, no_wrap=True)
        table.add_column("Model", overflow="ellipsis", no_wrap=True)
        for idx in range(self.state.model_menu_viewport, min(len(items), self.state.model_menu_viewport + visible_rows)):
            item = items[idx]
            selected = idx == self.state.model_menu_index
            row_style = "bold black on bright_white" if selected else ""
            if item["type"] == "all":
                marker = model_menu_marker(self.state.model_menu_selection, all_models)
                table.add_row(Text(marker), item["label"], style=row_style)
                continue
            if item["type"] == "provider":
                marker = model_menu_marker(
                    self.state.model_menu_selection,
                    set(item["models"]),
                )
                table.add_row(
                    Text(marker),
                    Text(item["label"], style="bold bright_magenta"),
                    style=row_style,
                )
                continue
            marker = model_menu_marker(
                self.state.model_menu_selection,
                {item["model"]},
            )
            table.add_row(Text(marker), "  " + item["label"], style=row_style)

        subtitle = "space toggle model/provider • a select all • enter apply • esc cancel"
        return Panel(
            table,
            title="[bold]Model Filter[/bold]",
            subtitle=subtitle,
            border_style="magenta",
            expand=False,
            width=min(78, max((self.console.width or 80) - 8, 34)),
        )

    def render_range_menu(self) -> Panel:
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("Range")
        for idx, (key, label, _kind) in enumerate(DATE_RANGES):
            selected = self.state.range_focus == "menu" and idx == self.state.range_menu_index
            active = key == self.state.date_range
            marker = "●" if active else " "
            if key == "custom" and self.state.custom_range_start and self.state.custom_range_end:
                label = f"Custom range  {self.state.custom_range_start} to {self.state.custom_range_end}"
            row_style = "bold black on bright_white" if selected else ""
            table.add_row(f"{marker} {label}", style=row_style)

        fields = Table(show_header=False, box=None, expand=True)
        fields.add_column("Field", width=8, no_wrap=True)
        fields.add_column("Value")
        fields.add_row("Since", self.render_range_field(self.state.range_start_input, 0))
        fields.add_row("Until", self.render_range_field(self.state.range_end_input, 1))

        content = [
            Text("Presets", style="bold cyan"),
            table,
            Text(""),
            Text("Custom", style="bold cyan"),
            fields,
        ]
        if self.state.range_error:
            content.append(Text(self.state.range_error, style="bold red"))
        subtitle = "esc close • j/k move • enter apply • tab custom fields"
        if self.state.range_focus == "field":
            subtitle = "tab switch field • enter next/apply • esc close"
        return Panel(
            Group(*content),
            title="[bold]Date Range[/bold]",
            subtitle=subtitle,
            border_style="yellow",
            expand=False,
            width=min(76, max((self.console.width or 80) - 8, 30)),
        )

    def render_range_field(self, value: str, index: int) -> Text:
        focused = self.state.range_focus == "field" and self.state.range_field_index == index
        text = Text(value or "YYYY-MM-DD")
        if value:
            text.stylize("bold black on bright_white" if focused else "bold white on grey23")
        else:
            text.stylize("bold black on bright_white" if focused else "dim")
        return text

    def render_refresh_popup(self) -> Panel:
        source = self.file_path if self.file_path else "ccusage daily/monthly"
        text = Text()
        text.append(f"{self.spinner_frame()} Refreshing usage data\n", style="bold bright_yellow")
        text.append(str(source), style="dim")
        return Panel(
            Align.center(text),
            title="[bold]Refresh[/bold]",
            border_style="bright_yellow",
            expand=False,
            width=min(64, max((self.console.width or 80) - 10, 32)),
        )

    def open_range_menu(self):
        self.state.range_menu_open = True
        self.state.range_menu_index = DATE_RANGE_KEYS.index(self.state.date_range)
        self.state.range_focus = "menu"
        self.state.range_field_index = 0
        self.state.range_start_input = self.state.custom_range_start
        self.state.range_end_input = self.state.custom_range_end
        self.state.range_error = ""

    def close_range_menu(self):
        self.state.range_menu_open = False
        self.state.range_focus = "menu"
        self.state.range_error = ""

    def focus_custom_fields(self):
        self.state.range_menu_index = DATE_RANGE_KEYS.index("custom")
        self.state.range_focus = "field"
        self.state.range_field_index = 0
        self.state.range_error = ""

    def apply_preset_range(self, selected_range: str):
        self.state.date_range = selected_range
        self.state.selected = 0
        self.state.viewport = 0
        self.state.expanded = False
        self.close_range_menu()

    def open_model_menu(self):
        self.state.model_menu_open = True
        # Work against an explicit draft selection so checked rows always mean included.
        applied_models = self.selected_models()
        self.state.model_menu_selection = (
            set(self.models) if applied_models is None else set(applied_models)
        )
        self.state.model_menu_viewport = 0
        items = self.model_menu_items()
        self.state.model_menu_index = 0
        if applied_models:
            first_selected = next(
                (
                    idx for idx, item in enumerate(items)
                    if item.get("type") == "model" and item.get("model") in applied_models
                ),
                0,
            )
            self.state.model_menu_index = first_selected

    def close_model_menu(self):
        self.state.model_menu_open = False

    def toggle_model_menu_selection(self):
        items = self.model_menu_items()
        if not items:
            return
        item = items[self.state.model_menu_index]
        if item["type"] == "all":
            self.state.model_menu_selection = set(self.models)
            return
        if item["type"] == "provider":
            models = set(item["models"])
        elif item["type"] == "model":
            models = {item["model"]}
        else:
            return
        self.state.model_menu_selection = toggle_model_group(
            self.state.model_menu_selection,
            models,
        )

    def apply_model_menu(self):
        selected = self.state.model_menu_selection & set(self.models)
        self.state.selected_models = None if selected == set(self.models) else selected
        self.state.selected = 0
        self.state.viewport = 0
        self.state.chart_offset = 0
        self.state.expanded = False
        self.close_model_menu()

    def fetch_refresh_data(self) -> Dict:
        if self.file_path:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)

        cmd = [self.script_path, "--dump-json", "--since", self.since]
        if self.until:
            cmd.extend(["--until", self.until])
        if self.project:
            cmd.extend(["--project", self.project])
        if self.offline == "0":
            cmd.append("--refresh")
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        with open(self.source_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return data

    def start_refresh(self):
        if self.state.refreshing:
            return
        self.state.refreshing = True
        self.state.spinner_index = 0
        self.state.status = "Refreshing..."

        def worker():
            try:
                self._refresh_queue.put(("ok", self.fetch_refresh_data()))
            except Exception as exc:
                self._refresh_queue.put(("error", exc))

        self._refresh_thread = threading.Thread(target=worker, daemon=True)
        self._refresh_thread.start()

    def apply_refresh_results(self) -> bool:
        updated = False
        while True:
            try:
                status, payload = self._refresh_queue.get_nowait()
            except queue.Empty:
                break
            updated = True
            self.state.refreshing = False
            if status == "ok":
                self.pricing_history = read_pricing_history(self.pricing_history_path)
                self.load_data(payload)
                self.rtk_gain = read_rtk_gain()
                self.state.status = "Refreshed " + datetime.now().strftime("%H:%M:%S")
            else:
                self.rtk_gain = read_rtk_gain()
                self.state.status = f"Refresh failed: {payload}"
        return updated

    def apply_custom_range(self) -> bool:
        start = self.state.range_start_input.strip()
        end = self.state.range_end_input.strip()
        start_date = parse_custom_date(start)
        end_date = parse_custom_date(end)
        if not start or not end:
            self.state.range_error = "Enter both Since and Until dates"
            return False
        if not start_date or not end_date:
            self.state.range_error = "Dates must be YYYY-MM-DD"
            return False
        if start_date > end_date:
            self.state.range_error = "Start date must be before end date"
            return False
        self.state.custom_range_start = start
        self.state.custom_range_end = end
        self.state.date_range = "custom"
        self.state.range_error = ""
        self.state.selected = 0
        self.state.viewport = 0
        self.state.expanded = False
        self.close_range_menu()
        return True

    def render(self) -> Layout:
        rows = self.filtered_rows()
        console_height = self.console.height or 32
        if self.state.refreshing:
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="body", ratio=1),
                Layout(name="help", size=4),
            )
            layout["header"].update(self.render_header(rows))
            layout["body"].update(Align.center(self.render_refresh_popup(), vertical="middle"))
            layout["help"].update(self.render_help())
            return layout

        if self.state.model_menu_open:
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="body", ratio=1),
                Layout(name="help", size=4),
            )
            layout["header"].update(self.render_header(rows))
            layout["body"].update(Align.center(self.render_model_menu(), vertical="middle"))
            layout["help"].update(self.render_help())
            return layout

        if self.state.range_menu_open:
            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="body", ratio=1),
                Layout(name="help", size=4),
            )
            layout["header"].update(self.render_header(rows))
            layout["body"].update(Align.center(self.render_range_menu(), vertical="middle"))
            layout["help"].update(self.render_help())
            return layout

        detail_height = max(10, min(14, console_height // 3))
        body_height = max(3, console_height - 3 - detail_height - 4)
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="detail", size=detail_height),
            Layout(name="help", size=4),
        )
        layout["body"].split_row(Layout(name="days", ratio=3), Layout(name="chart", ratio=2))
        layout["header"].update(self.render_header(rows))
        days_title = "[bold]Daily Usage[/bold]"
        if self.state.focus == "days":
            days_title += " [bold yellow](focused)[/bold yellow]"
        layout["days"].update(Panel(self.render_days(rows, body_height), title=days_title, border_style="bright_yellow" if self.state.focus == "days" else "cyan"))
        layout["chart"].update(self.render_chart(rows, body_height))
        layout["detail"].split_row(Layout(name="rates", ratio=2), Layout(name="rtk", size=42))
        layout["rates"].update(self.render_detail(rows))
        layout["rtk"].update(self.render_rtk_gain())
        layout["help"].update(self.render_help())
        return layout

    def handle_key(self, chars: bytes):
        if self.state.refreshing:
            return

        rows = self.filtered_rows()
        if self.state.model_menu_open:
            if chars in (b"\x03",):
                self.running = False
                return
            if chars in (b"\x1b", b"m"):
                self.close_model_menu()
            elif chars in (b"j", b"\x1b[B"):
                self.move_model_menu(1)
            elif chars in (b"k", b"\x1b[A"):
                self.move_model_menu(-1)
            elif chars == b"\x1b[6~":
                self.move_model_menu(self.page_size)
            elif chars == b"\x1b[5~":
                self.move_model_menu(-self.page_size)
            elif chars == b"a":
                self.state.model_menu_selection = set(self.models)
                self.state.model_menu_index = 0
            elif chars == b" ":
                self.toggle_model_menu_selection()
            elif chars in (b"\r", b"\n"):
                self.apply_model_menu()
            return

        if self.state.range_menu_open:
            if chars in (b"\x03",):
                self.running = False
                return
            if chars in (b"\x1b", b"v"):
                self.close_range_menu()
                return
            if self.state.range_focus == "field":
                current_value = self.state.range_start_input if self.state.range_field_index == 0 else self.state.range_end_input
                if chars == b"\t":
                    self.state.range_field_index = 1 - self.state.range_field_index
                elif chars in (b"\r", b"\n"):
                    if self.state.range_field_index == 0:
                        self.state.range_field_index = 1
                    else:
                        self.apply_custom_range()
                elif chars in (b"\x7f", b"\b"):
                    current_value = current_value[:-1]
                elif chars == b"\x15":
                    current_value = ""
                elif chars in (b"\x1b[B", b"j"):
                    self.state.range_field_index = min(self.state.range_field_index + 1, 1)
                elif chars in (b"\x1b[A", b"k"):
                    if self.state.range_field_index == 0:
                        self.state.range_focus = "menu"
                    else:
                        self.state.range_field_index = 0
                else:
                    try:
                        text = chars.decode()
                    except UnicodeDecodeError:
                        text = ""
                    if text and all(ch.isdigit() or ch == "-" for ch in text):
                        current_value = (current_value + text)[:10]
                        self.state.range_error = ""
                if self.state.range_field_index == 0:
                    self.state.range_start_input = current_value
                else:
                    self.state.range_end_input = current_value
                return
            if chars == b"\t":
                self.focus_custom_fields()
            elif chars in (b"j", b"\x1b[B"):
                self.state.range_menu_index = min(self.state.range_menu_index + 1, len(DATE_RANGES) - 1)
            elif chars in (b"k", b"\x1b[A"):
                self.state.range_menu_index = max(self.state.range_menu_index - 1, 0)
            elif chars == b"\x1b[6~":
                self.state.range_menu_index = min(self.state.range_menu_index + self.page_size, len(DATE_RANGES) - 1)
            elif chars == b"\x1b[5~":
                self.state.range_menu_index = max(self.state.range_menu_index - self.page_size, 0)
            elif chars in (b"\r", b"\n", b" "):
                selected_range = DATE_RANGES[self.state.range_menu_index][0]
                if selected_range == "custom":
                    self.focus_custom_fields()
                    return
                self.apply_preset_range(selected_range)
            return
        if chars in (b"q", b"\x03"):
            self.running = False
        elif chars == b"\x1b":
            self.open_range_menu()
        elif chars == b"\t":
            self.state.focus = "chart" if self.state.focus == "days" else "days"
        elif self.state.focus == "chart" and chars in (b"j", b"\x1b[B"):
            max_offset = max(len(self.chart_rows(rows)) - self.chart_page_size, 0)
            self.state.chart_offset = min(self.state.chart_offset + 1, max_offset)
        elif self.state.focus == "chart" and chars in (b"k", b"\x1b[A"):
            self.state.chart_offset = max(self.state.chart_offset - 1, 0)
        elif self.state.focus == "chart" and chars == b"\x1b[6~":
            max_offset = max(len(self.chart_rows(rows)) - self.chart_page_size, 0)
            self.state.chart_offset = min(self.state.chart_offset + self.chart_page_size, max_offset)
        elif self.state.focus == "chart" and chars == b"\x1b[5~":
            self.state.chart_offset = max(self.state.chart_offset - self.chart_page_size, 0)
        elif self.state.focus == "chart" and chars == b"g":
            self.state.chart_offset = 0
        elif self.state.focus == "chart" and chars == b"G":
            self.state.chart_offset = max(len(self.chart_rows(rows)) - self.chart_page_size, 0)
        elif chars in (b"j", b"\x1b[B"):
            self.state.selected = min(self.state.selected + 1, max(len(rows) - 1, 0))
        elif chars in (b"k", b"\x1b[A"):
            self.state.selected = max(self.state.selected - 1, 0)
        elif chars == b"\x1b[6~":
            self.state.selected = min(self.state.selected + self.page_size, max(len(rows) - 1, 0))
        elif chars == b"\x1b[5~":
            self.state.selected = max(self.state.selected - self.page_size, 0)
        elif chars == b"g":
            self.state.selected = 0
        elif chars == b"G":
            self.state.selected = max(len(rows) - 1, 0)
        elif chars == b"a":
            self.state.provider_index = (self.state.provider_index + 1) % len(self.provider_filter_options())
            self.state.selected = 0
            self.state.viewport = 0
            self.state.chart_offset = 0
            self.state.expanded = False
        elif chars == b"m":
            self.open_model_menu()
        elif chars == b"v":
            self.open_range_menu()
        elif chars in (b" ", b"\r", b"\n"):
            self.state.expanded = not self.state.expanded
        elif chars in (b"\x1b[D", b"\x1bOD"):
            index = SORT_KEYS.index(self.state.sort_key) if self.state.sort_key in SORT_KEYS else 0
            self.state.sort_key = SORT_KEYS[(index - 1) % len(SORT_KEYS)]
        elif chars in (b"\x1b[C", b"\x1bOC"):
            index = SORT_KEYS.index(self.state.sort_key) if self.state.sort_key in SORT_KEYS else 0
            self.state.sort_key = SORT_KEYS[(index + 1) % len(SORT_KEYS)]
        elif chars == b"s":
            self.state.sort_desc = not self.state.sort_desc
        elif chars == b"r":
            self.start_refresh()
        elif chars in (b"1", b"2", b"3", b"4", b"5"):
            self.state.metric_index = int(chars.decode()) - 1

    def flush_pending_input(self):
        if not self._tty:
            return
        try:
            termios.tcflush(self._tty.fileno(), termios.TCIFLUSH)
        except termios.error:
            pass

    def run(self):
        if not self.rows:
            self.console.print("[bold red]No ccusage daily rows found.[/bold red]")
            return
        self._tty = open("/dev/tty", "rb", buffering=0)
        fd = self._tty.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            with Live(self.render(), console=self.console, screen=True, refresh_per_second=8) as live:
                while self.running:
                    was_refreshing = self.state.refreshing
                    changed = self.apply_refresh_results()
                    if was_refreshing and not self.state.refreshing:
                        self.flush_pending_input()
                        changed = True
                    if self.state.refreshing:
                        select.select([], [], [], 0.1)
                        self.state.spinner_index = (self.state.spinner_index + 1) % len(SPINNER_FRAMES)
                        changed = True
                        if self.apply_refresh_results():
                            self.flush_pending_input()
                            changed = True
                        if changed:
                            live.update(self.render())
                        continue

                    ready, _, _ = select.select([fd], [], [], 0.1)
                    if ready:
                        chars = os.read(fd, 8)
                        if chars:
                            self.handle_key(chars)
                            changed = True
                    if self.apply_refresh_results():
                        self.flush_pending_input()
                        changed = True
                    if changed:
                        live.update(self.render())
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            self._tty.close()


def main():
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    UsageExplorer(
        data=data,
        source_path=path,
        script_path=sys.argv[3],
        file_path=sys.argv[4],
        since=sys.argv[5],
        until=sys.argv[6],
        project=sys.argv[7],
        offline=sys.argv[8],
        pricing_history_path=sys.argv[9],
    ).run()


if __name__ == "__main__":
    main()
PYTHON_EOF
