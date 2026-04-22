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
  g/G                  Jump to first/last day
  m                    Cycle model filter
  p                    Toggle day/month grouping
  v                    Open date range filter
  space/enter          Expand selected row model breakdown
  f                    Cycle sort column
  r                    Reverse sort order
  1-5                  Chart metric: cost, total, input, output, cache
  q                    Quit
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
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

DATA_FILE="$JSON_FILE"
if [ -z "$DATA_FILE" ]; then
    DATA_FILE="$(mktemp)"
    trap 'rm -f "$DATA_FILE"' EXIT

    echo "Loading usage data..." >&2
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
    ' > "$DATA_FILE"
fi

"$PYTHON_BIN" - "$DATA_FILE" "$GROUP" <<'PYTHON_EOF'
import json
import os
import sys
import termios
import tty
import time
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
]
DATE_RANGE_LABELS = {key: label for key, label, _kind in DATE_RANGES}
DATE_RANGE_KEYS = [key for key, _label, _kind in DATE_RANGES]


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


class UsageExplorer:
    def __init__(self, data: Dict):
        self.console = Console()
        self.rows = data.get("daily", [])
        self.totals = data.get("totals", {})
        self.models = self._models()
        self.model_colors = {m: MODEL_COLORS[i % len(MODEL_COLORS)] for i, m in enumerate(self.models)}
        self.state = State()
        self.running = True
        self._tty = None
        self.page_size = 10

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
        return None

    def filtered_rows(self) -> List[Dict]:
        model = self.selected_model()
        rows = self.rows
        start = self.date_range_start()
        if start:
            rows = [
                row for row in rows
                if (parsed := parse_row_date(str(row.get("date", "")))) and parsed >= start
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

    def render_header(self, rows: List[Dict]) -> Panel:
        model = self.selected_model()
        metric = METRICS[self.state.metric_index][1]
        sort_label = SORT_LABELS.get(self.state.sort_key, self.state.sort_key).lower()
        sort_arrow = "▼" if self.state.sort_desc else "▲"
        range_label = DATE_RANGE_LABELS.get(self.state.date_range, self.state.date_range)
        text = Text()
        text.append(" AI Usage Explorer ", style="bold white on blue")
        text.append(f"  Model: {compact_model(model) if model else 'All'}  ", style="magenta")
        text.append(f"  Range: {range_label}  ", style="yellow")
        text.append(f"  Metric: {metric}  ", style="bright_blue")
        text.append(f"  Sort: {sort_label} {sort_arrow}  ", style="white")
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
            f"  {fmt_cost(summary['cost'] * 1.2)}-{fmt_cost(summary['cost'] * 1.5)}",
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

    def render_chart(self, rows: List[Dict], height: int) -> Panel:
        model = self.selected_model()
        metric_key, metric_label, getter, fmt = METRICS[self.state.metric_index]
        max_rows = min(len(rows), max(height - 3, 0))
        chart_source = rows[-max_rows:] if max_rows else []
        chart_rows = [self.model_row_values(row, model) for row in chart_source]
        max_value = max((getter(row) for row in chart_rows), default=0)
        width = max(min((self.console.width or 100) - 52, 42), 10)
        text = Text()
        for row in chart_rows:
            value = getter(row)
            bar_len = int((value / max_value) * width) if max_value else 0
            bar = "█" * max(bar_len, 1 if value else 0)
            style = "green" if metric_key == "cost" else "cyan"
            text.append(f"{row.get('date', '')} ", style="dim")
            text.append(f"{bar:<{width}} ", style=style)
            text.append((fmt.format(value) if metric_key != "cost" else fmt_cost(value)) + "\n", style="bold")
        return Panel(text or Text("No data", style="dim"), title=f"[bold]{metric_label} Trend[/bold]", border_style="blue")

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
        controls.append("m", style="bold cyan")
        controls.append(" model  ")
        controls.append("v", style="bold cyan")
        controls.append(" range  ")
        controls.append("space", style="bold cyan")
        controls.append(" expand  ")
        controls.append("f", style="bold cyan")
        controls.append(" sort column  ")
        controls.append("r", style="bold cyan")
        controls.append(" reverse sort  ")
        controls.append("1-5", style="bold cyan")
        controls.append(" metric  ")
        controls.append("q", style="bold cyan")
        controls.append(" quit")
        note = Text("Estimated actual cost applies a 20-50% uplift to ccusage cost.", style="dim")
        return Panel(Group(Align.center(controls), Align.center(note)), border_style="dim")

    def render_range_menu(self) -> Panel:
        table = Table(show_header=False, box=None, expand=True)
        table.add_column("Range")
        for idx, (key, label, _kind) in enumerate(DATE_RANGES):
            selected = idx == self.state.range_menu_index
            active = key == self.state.date_range
            marker = "●" if active else " "
            row_style = "bold black on bright_white" if selected else ""
            table.add_row(f"{marker} {label}", style=row_style)
        return Panel(
            table,
            title="[bold]Date Range[/bold]",
            subtitle="j/k or ↑/↓ move • enter apply • esc close",
            border_style="yellow",
        )

    def render(self) -> Layout:
        rows = self.filtered_rows()
        console_height = self.console.height or 32
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
        layout["days"].update(Panel(self.render_days(rows, body_height), title="[bold]Daily Usage[/bold]", border_style="cyan"))
        layout["chart"].update(self.render_chart(rows, body_height))
        if self.state.range_menu_open:
            layout["detail"].update(self.render_range_menu())
        else:
            layout["detail"].update(self.render_detail(rows))
        layout["help"].update(self.render_help())
        return layout

    def handle_key(self, chars: bytes):
        rows = self.filtered_rows()
        if self.state.range_menu_open:
            if chars in (b"\x1b", b"v"):
                self.state.range_menu_open = False
            elif chars in (b"j", b"\x1b[B"):
                self.state.range_menu_index = min(self.state.range_menu_index + 1, len(DATE_RANGES) - 1)
            elif chars in (b"k", b"\x1b[A"):
                self.state.range_menu_index = max(self.state.range_menu_index - 1, 0)
            elif chars == b"\x1b[6~":
                self.state.range_menu_index = min(self.state.range_menu_index + self.page_size, len(DATE_RANGES) - 1)
            elif chars == b"\x1b[5~":
                self.state.range_menu_index = max(self.state.range_menu_index - self.page_size, 0)
            elif chars in (b"\r", b"\n", b" "):
                self.state.date_range = DATE_RANGES[self.state.range_menu_index][0]
                self.state.selected = 0
                self.state.viewport = 0
                self.state.expanded = False
                self.state.range_menu_open = False
            return
        if chars in (b"q", b"\x03"):
            self.running = False
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
            self.state.range_menu_open = True
            self.state.range_menu_index = DATE_RANGE_KEYS.index(self.state.date_range)
        elif chars in (b" ", b"\r", b"\n"):
            self.state.expanded = not self.state.expanded
        elif chars == b"f":
            index = SORT_KEYS.index(self.state.sort_key) if self.state.sort_key in SORT_KEYS else 0
            self.state.sort_key = SORT_KEYS[(index + 1) % len(SORT_KEYS)]
        elif chars == b"r":
            self.state.sort_desc = not self.state.sort_desc
        elif chars in (b"1", b"2", b"3", b"4", b"5"):
            self.state.metric_index = int(chars.decode()) - 1

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
                    chars = os.read(fd, 8)
                    if chars:
                        self.handle_key(chars)
                        live.update(self.render())
                    else:
                        time.sleep(0.05)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
            self._tty.close()


def main():
    path = sys.argv[1]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    UsageExplorer(data).run()


if __name__ == "__main__":
    main()
PYTHON_EOF
