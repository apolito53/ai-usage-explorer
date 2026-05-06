#!/usr/bin/env bash
# Interactive Claude Code usage explorer built on ccusage JSON output.
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

SINCE="20260209"
UNTIL=""
PROJECT=""
JSON_FILE=""
OFFLINE=1
GROUP="day"
DUMP_JSON=0

usage() {
    cat <<'EOF'
Usage: ./ai-usage-explorer.sh [options]

Options:
  --since YYYYMMDD     Start date for ccusage daily data (default: 20260209)
  --until YYYYMMDD     End date for ccusage daily data
  --project NAME       Pass through ccusage --project
  --group day|month    Initial grouping (default: day)
  --refresh            Fetch current model pricing instead of ccusage --offline
  --demo               Load bundled demo data instead of running ccusage
  --file PATH          Load an existing ccusage JSON file
  -h, --help           Show this help

Keyboard:
  j/k or ↑/↓           Move day selection
  pgup/pgdn            Page day selection
  tab                  Switch day list / chart focus
  g/G                  Jump to first/last day
  m                    Cycle model filter
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
        --since) SINCE="$2"; shift 2 ;;
        --until) UNTIL="$2"; shift 2 ;;
        --project) PROJECT="$2"; shift 2 ;;
        --group) GROUP="$2"; shift 2 ;;
        --refresh) OFFLINE=0; shift ;;
        --demo) JSON_FILE="${SCRIPT_DIR}/demo/usage-demo.json"; shift ;;
        --file) JSON_FILE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

case "$GROUP" in
    day|month) ;;
    *) echo "ERROR: --group must be day or month" >&2; exit 1 ;;
esac

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

fetch_usage_data() {
    AI_USAGE_SINCE="$SINCE" \
    AI_USAGE_UNTIL="$UNTIL" \
    AI_USAGE_PROJECT="$PROJECT" \
    AI_USAGE_OFFLINE="$OFFLINE" \
    bash -lic '
        nvm use 22 >/dev/null
        build_args() {
            local period="$1"
            args=("$period" -b -s "$AI_USAGE_SINCE" --json)
            if [ -n "$AI_USAGE_UNTIL" ]; then
                args+=(-u "$AI_USAGE_UNTIL")
            fi
            if [ -n "$AI_USAGE_PROJECT" ]; then
                args+=(-p "$AI_USAGE_PROJECT")
            fi
            if [ "$AI_USAGE_OFFLINE" -eq 1 ]; then
                args+=(--offline)
            fi
        }

        daily_file="$(mktemp)"
        monthly_file="$(mktemp)"
        trap "rm -f \"$daily_file\" \"$monthly_file\"" EXIT

        build_args daily
        pnpm dlx ccusage "${args[@]}" > "$daily_file"
        build_args monthly
        pnpm dlx ccusage "${args[@]}" > "$monthly_file"

        python3 - "$daily_file" "$monthly_file" <<'"'"'PY'"'"'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    daily = json.load(f)
with open(sys.argv[2], "r", encoding="utf-8") as f:
    monthly = json.load(f)

print(json.dumps({
    "daily": daily.get("daily", []),
    "monthly": monthly.get("monthly", []),
    "totals": daily.get("totals") or monthly.get("totals") or {},
}))
PY
    '
}

if [ "$DUMP_JSON" -eq 1 ]; then
    fetch_usage_data
    exit 0
fi

DATA_FILE="$JSON_FILE"
if [ -z "$DATA_FILE" ]; then
    DATA_FILE="$(mktemp)"
    trap 'rm -f "$DATA_FILE"' EXIT

    echo "Loading usage data..." >&2
    fetch_usage_data > "$DATA_FILE"
fi

"$PYTHON_BIN" - "$DATA_FILE" "$GROUP" "$0" "$JSON_FILE" "$SINCE" "$UNTIL" "$PROJECT" "$OFFLINE" <<'PYTHON_EOF'
import json
import os
import queue
import select
import subprocess
import sys
import termios
import threading
import tty
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    from rich.align import Align
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("ERROR: rich is required. Run ./kafka-event-visualizer.sh once to create .venv, or install rich.", file=sys.stderr)
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

SORT_COLUMNS = [
    ("date", "Date"),
    ("cost", "Cost"),
    ("tokens", "Total"),
    ("input", "Input"),
    ("output", "Output"),
    ("cache", "Cache"),
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


def compact_model(name: str) -> str:
    name = name.replace("claude-", "")
    for suffix in ("-20251001", "-20250929"):
        name = name.replace(suffix, "")
    return name


def fmt_int(value: float) -> str:
    return f"{int(value):,}"


def fmt_cost(value: float) -> str:
    return f"${value:,.2f}"


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


@dataclass
class State:
    selected: int = 0
    model_index: int = 0
    sort_key: str = "date"
    sort_desc: bool = False
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
    def __init__(self, data: Dict, source_path: str, script_path: str, file_path: str, since: str, until: str, project: str, offline: str):
        self.console = Console()
        self.source_path = source_path
        self.script_path = script_path
        self.file_path = file_path
        self.since = since
        self.until = until
        self.project = project
        self.offline = offline
        self.state = State()
        self.load_data(data)
        self.running = True
        self._tty = None
        self.page_size = 10
        self.chart_page_size = 10
        self._refresh_queue = queue.Queue()
        self._refresh_thread = None

    def load_data(self, data: Dict):
        self.rows = data.get("daily", [])
        self.totals = data.get("totals", {})
        self.models = self._models()
        self.model_colors = {m: MODEL_COLORS[i % len(MODEL_COLORS)] for i, m in enumerate(self.models)}
        self.state.model_index = min(self.state.model_index, len(self.models))
        self.state.selected = 0
        self.state.viewport = 0
        self.state.chart_offset = 0
        self.state.expanded = False

    def _models(self) -> List[str]:
        seen = []
        for row in self.rows:
            for model in row.get("modelsUsed", []):
                if model not in seen:
                    seen.append(model)
        return sorted(seen)

    def selected_model(self) -> Optional[str]:
        if self.state.model_index == 0:
            return None
        return self.models[self.state.model_index - 1]

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
        model = self.selected_model()
        rows = self.rows
        start = self.date_range_start()
        end = self.date_range_end()
        if start or end:
            rows = [
                row for row in rows
                if (parsed := parse_row_date(str(row.get("date", ""))))
                and (not start or parsed >= start)
                and (not end or parsed <= end)
            ]
        if model:
            rows = [
                row for row in rows
                if any(m.get("modelName") == model for m in row.get("modelBreakdowns", []))
            ]

        key = self.state.sort_key
        def sorter(row: Dict):
            effective = self.model_row_values(row, model)
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
            if key == "models":
                return ", ".join(compact_model(name) for name in effective.get("modelsUsed", []))
            return effective.get("date", "")

        return sorted(rows, key=sorter, reverse=self.state.sort_desc)

    def model_row_values(self, row: Dict, model: Optional[str]) -> Dict:
        if not model:
            return row
        for item in row.get("modelBreakdowns", []):
            if item.get("modelName") == model:
                return {
                    "date": row.get("date"),
                    "inputTokens": item.get("inputTokens", 0),
                    "outputTokens": item.get("outputTokens", 0),
                    "cacheCreationTokens": item.get("cacheCreationTokens", 0),
                    "cacheReadTokens": item.get("cacheReadTokens", 0),
                    "totalTokens": (
                        int(item.get("inputTokens", 0))
                        + int(item.get("outputTokens", 0))
                        + int(item.get("cacheCreationTokens", 0))
                        + int(item.get("cacheReadTokens", 0))
                    ),
                    "totalCost": item.get("cost", 0.0),
                    "modelsUsed": [model],
                    "modelBreakdowns": [item],
                }
        return row

    def summary(self, rows: List[Dict]) -> Dict:
        model = self.selected_model()
        effective = [self.model_row_values(row, model) for row in rows]
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
        model = self.selected_model()
        metric = METRICS[self.state.metric_index][1]
        sort_label = SORT_LABELS.get(self.state.sort_key, self.state.sort_key).lower()
        sort_arrow = "▼" if self.state.sort_desc else "▲"
        range_label = DATE_RANGE_LABELS.get(self.state.date_range, self.state.date_range)
        if self.state.date_range == "custom":
            range_label = f"{self.state.custom_range_start}..{self.state.custom_range_end}"
        text = Text()
        text.append(" AI Usage Explorer ", style="bold white on blue")
        text.append(f"  Model: {compact_model(model) if model else 'All'}  ", style="magenta")
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
        estimate = Text()
        estimate.append(" EST ACTUAL ", style="bold black on bright_yellow")
        estimate.append(
            f"  {fmt_cost(summary['cost'] * 1.3)}",
            style="bold yellow",
        )
        if summary["peak"]:
            peak = summary["peak"]
            totals.append(
                f"Peak: {peak.get('date')} {fmt_cost(float(peak.get('totalCost', 0)))}",
                style="bold red",
            )
        return Group(totals, estimate)

    def render_days(self, rows: List[Dict], height: int) -> Group:
        model = self.selected_model()
        max_lines = max(height - 6, 0)
        self.page_size = max(max_lines, 1)
        if self.state.selected >= len(rows):
            self.state.selected = max(0, len(rows) - 1)
        if self.state.selected < self.state.viewport:
            self.state.viewport = self.state.selected
        selected_row = self.model_row_values(rows[self.state.selected], model) if rows else {}
        selected_breakdown_count = len(selected_row.get("modelBreakdowns", [])) if self.state.expanded else 0
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
            row = self.model_row_values(original, model)
            selected = absolute == self.state.selected
            row_style = "bold black on bright_white" if selected else ""
            models_text = Text()
            model_style = "black" if selected else None
            separator_style = "black" if selected else "dim"
            for n, model_name in enumerate(row.get("modelsUsed", [])):
                if n:
                    models_text.append(", ", style=separator_style)
                models_text.append(compact_model(model_name), style=model_style or self.model_colors.get(model_name, "white"))
            cache_tokens = int(row.get("cacheCreationTokens", 0)) + int(row.get("cacheReadTokens", 0))
            table.add_row(
                str(row.get("date", "")),
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
                for item in row.get("modelBreakdowns", []):
                    if used_lines >= max_lines:
                        break
                    rendered_breakdowns = True
                    breakdown_model = item.get("modelName", "")
                    cache_create = int(item.get("cacheCreationTokens", 0))
                    cache_read = int(item.get("cacheReadTokens", 0))
                    cache_tokens = cache_create + cache_read
                    input_tokens = int(item.get("inputTokens", 0))
                    output_tokens = int(item.get("outputTokens", 0))
                    total_tokens = input_tokens + output_tokens + cache_tokens
                    table.add_row(
                        "",
                        fmt_cost(float(item.get("cost", 0.0))),
                        fmt_int(total_tokens),
                        fmt_int(input_tokens),
                        fmt_int(output_tokens),
                        fmt_int(cache_tokens),
                        Text("  " + compact_model(breakdown_model), style="bold white"),
                        style="white on grey15",
                    )
                    used_lines += 1
                if rendered_breakdowns and used_lines < max_lines:
                    table.add_row("", "", "", "", "", "", "")
                    used_lines += 1
        return Group(table, Text(""), self.render_metrics_footer(rows))

    def chart_rows(self, rows: List[Dict]) -> List[Dict]:
        model = self.selected_model()
        return [
            self.model_row_values(row, model)
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
            text.append(f"{row.get('date', '')} ", style="dim")
            text.append(f"{bar:<{width}} ", style=style)
            text.append((fmt.format(value) if metric_key != "cost" else fmt_cost(value)) + "\n", style="bold")
        title = f"[bold]{metric_label} Trend[/bold]"
        if self.state.focus == "chart":
            title += " [bold yellow](focused)[/bold yellow]"
        return Panel(text or Text("No data", style="dim"), title=title, border_style="bright_yellow" if self.state.focus == "chart" else "blue")

    def render_detail(self, rows: List[Dict]) -> Panel:
        if not rows:
            return Panel(Text("No rows match the current filter", style="dim"), title="[bold]Detail[/bold]")
        row = rows[self.state.selected]
        table = Table(show_header=True, header_style="bold cyan", box=None, expand=True)
        table.add_column("Model")
        table.add_column("Cost", justify="right")
        table.add_column("Input", justify="right")
        table.add_column("Output", justify="right")
        table.add_column("Cache Create", justify="right")
        table.add_column("Cache Read", justify="right")
        for item in row.get("modelBreakdowns", []):
            model = item.get("modelName", "")
            cache_create = int(item.get("cacheCreationTokens", 0))
            cache_read = int(item.get("cacheReadTokens", 0))
            table.add_row(
                Text(compact_model(model), style=self.model_colors.get(model, "white")),
                fmt_cost(float(item.get("cost", 0.0))),
                fmt_int(item.get("inputTokens", 0)),
                fmt_int(item.get("outputTokens", 0)),
                fmt_int(cache_create),
                fmt_int(cache_read),
            )
        return Panel(table, title=f"[bold]Model Breakdown: {row.get('date')}[/bold]", border_style="magenta")

    def render_help(self) -> Panel:
        controls = Text()
        controls.append("j/k/↑↓", style="bold cyan")
        controls.append(" move  ")
        controls.append("pgup/pgdn", style="bold cyan")
        controls.append(" page  ")
        controls.append("tab", style="bold cyan")
        controls.append(" focus  ")
        controls.append("m", style="bold cyan")
        controls.append(" model  ")
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
        note = Text("Estimated actual cost applies a 1.3x multiplier to ccusage cost.", style="dim")
        return Panel(Group(Align.center(controls), Align.center(note)), border_style="dim")

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
                self.load_data(payload)
                self.state.status = "Refreshed " + datetime.now().strftime("%H:%M:%S")
            else:
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

        detail_height = max(8, min(13, console_height // 3))
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
        layout["detail"].update(self.render_detail(rows))
        layout["help"].update(self.render_help())
        return layout

    def handle_key(self, chars: bytes):
        if self.state.refreshing:
            return

        rows = self.filtered_rows()
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
        elif chars == b"m":
            self.state.model_index = (self.state.model_index + 1) % (len(self.models) + 1)
            self.state.selected = 0
            self.state.viewport = 0
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
    ).run()


if __name__ == "__main__":
    main()
PYTHON_EOF
