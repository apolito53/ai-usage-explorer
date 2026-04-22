#!/usr/bin/env bash
# Interactive Claude Code usage explorer built on ccusage JSON output.
#
# Examples:
#   ./ai-usage-explorer.sh
#   ./ai-usage-explorer.sh --since 20260401
#   ./ai-usage-explorer.sh --refresh
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

usage() {
    cat <<'EOF'
Usage: ./ai-usage-explorer.sh [options]

Options:
  --since YYYYMMDD     Start date for ccusage daily data (default: 20260209)
  --until YYYYMMDD     End date for ccusage daily data
  --project NAME       Pass through ccusage --project
  --refresh            Fetch current model pricing instead of ccusage --offline
  --file PATH          Load an existing ccusage daily --json file
  -h, --help           Show this help

Keyboard:
  j/k or ↑/↓           Move day selection
  g/G                  Jump to first/last day
  m                    Cycle model filter
  c                    Sort by cost
  t                    Sort by tokens
  d                    Sort by date
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
        --refresh) OFFLINE=0; shift ;;
        --file) JSON_FILE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

DATA_FILE="$JSON_FILE"
if [ -z "$DATA_FILE" ]; then
    DATA_FILE="$(mktemp)"
    trap 'rm -f "$DATA_FILE"' EXIT

    echo "Loading ccusage daily JSON..." >&2
    AI_USAGE_SINCE="$SINCE" \
    AI_USAGE_UNTIL="$UNTIL" \
    AI_USAGE_PROJECT="$PROJECT" \
    AI_USAGE_OFFLINE="$OFFLINE" \
    bash -lic '
        nvm use 22 >/dev/null
        args=(daily -b -s "$AI_USAGE_SINCE" --json)
        if [ -n "$AI_USAGE_UNTIL" ]; then
            args+=(-u "$AI_USAGE_UNTIL")
        fi
        if [ -n "$AI_USAGE_PROJECT" ]; then
            args+=(-p "$AI_USAGE_PROJECT")
        fi
        if [ "$AI_USAGE_OFFLINE" -eq 1 ]; then
            args+=(--offline)
        fi
        pnpm dlx ccusage "${args[@]}"
    ' > "$DATA_FILE"
fi

"$PYTHON_BIN" - "$DATA_FILE" <<'PYTHON_EOF'
import json
import os
import sys
import termios
import tty
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    from rich.align import Align
    from rich.console import Console
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


def compact_model(name: str) -> str:
    name = name.replace("claude-", "")
    for suffix in ("-20251001", "-20250929"):
        name = name.replace(suffix, "")
    return name


def fmt_int(value: float) -> str:
    return f"{int(value):,}"


def fmt_cost(value: float) -> str:
    return f"${value:,.2f}"


@dataclass
class State:
    selected: int = 0
    model_index: int = 0
    sort_key: str = "date"
    sort_desc: bool = False
    metric_index: int = 0
    viewport: int = 0


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

    def filtered_rows(self) -> List[Dict]:
        model = self.selected_model()
        rows = self.rows
        if model:
            rows = [
                row for row in rows
                if any(m.get("modelName") == model for m in row.get("modelBreakdowns", []))
            ]

        key = self.state.sort_key
        if key == "cost":
            sorter = lambda row: float(row.get("totalCost", 0.0))
        elif key == "tokens":
            sorter = lambda row: int(row.get("totalTokens", 0))
        else:
            sorter = lambda row: row.get("date", "")
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
        summary = self.summary(rows)
        model = self.selected_model()
        metric = METRICS[self.state.metric_index][1]
        text = Text()
        text.append(" AI Usage Explorer ", style="bold white on blue")
        text.append(f"  Days: {summary['days']}  ", style="bold")
        text.append(f"  Cost: {fmt_cost(summary['cost'])}  ", style="bold green")
        text.append(f"  Tokens: {fmt_int(summary['tokens'])}  ", style="bold cyan")
        text.append(f"  Avg/day: {fmt_cost(summary['avg_cost'])}  ", style="yellow")
        if summary["peak"]:
            text.append(f"  Peak: {summary['peak'].get('date')} {fmt_cost(float(summary['peak'].get('totalCost', 0)))}  ", style="bold red")
        text.append(f"  Model: {compact_model(model) if model else 'All'}  ", style="magenta")
        text.append(f"  Metric: {metric}  ", style="bright_blue")
        return Panel(text, border_style="dim")

    def render_days(self, rows: List[Dict], height: int) -> Table:
        model = self.selected_model()
        max_rows = max(height - 4, 3)
        if self.state.selected >= len(rows):
            self.state.selected = max(0, len(rows) - 1)
        if self.state.selected < self.state.viewport:
            self.state.viewport = self.state.selected
        elif self.state.selected >= self.state.viewport + max_rows:
            self.state.viewport = self.state.selected - max_rows + 1
        self.state.viewport = max(0, min(self.state.viewport, max(0, len(rows) - max_rows)))

        table = Table(show_header=True, header_style="bold cyan", box=None, expand=True)
        table.add_column("Date", no_wrap=True)
        table.add_column("Cost", justify="right", no_wrap=True)
        table.add_column("Total", justify="right", no_wrap=True)
        table.add_column("Input", justify="right", no_wrap=True)
        table.add_column("Output", justify="right", no_wrap=True)
        table.add_column("Cache", justify="right", no_wrap=True)
        table.add_column("Models", overflow="fold")

        visible = rows[self.state.viewport:self.state.viewport + max_rows]
        for idx, original in enumerate(visible):
            absolute = self.state.viewport + idx
            row = self.model_row_values(original, model)
            selected = absolute == self.state.selected
            row_style = "bold black on bright_white" if selected else ""
            models_text = Text()
            for n, model_name in enumerate(row.get("modelsUsed", [])):
                if n:
                    models_text.append(", ", style="dim")
                models_text.append(compact_model(model_name), style=self.model_colors.get(model_name, "white"))
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
        return table

    def render_chart(self, rows: List[Dict], height: int) -> Panel:
        model = self.selected_model()
        metric_key, metric_label, getter, fmt = METRICS[self.state.metric_index]
        chart_rows = [self.model_row_values(row, model) for row in rows[-min(len(rows), max(height - 4, 5)):]]
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
        text = Text()
        text.append("j/k/↑↓", style="bold cyan")
        text.append(" move  ")
        text.append("m", style="bold cyan")
        text.append(" model  ")
        text.append("c/t/d", style="bold cyan")
        text.append(" sort  ")
        text.append("r", style="bold cyan")
        text.append(" reverse  ")
        text.append("1-5", style="bold cyan")
        text.append(" metric  ")
        text.append("q", style="bold cyan")
        text.append(" quit")
        return Panel(Align.center(text), border_style="dim")

    def render(self) -> Layout:
        rows = self.filtered_rows()
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
            Layout(name="detail", size=max(8, min(13, (self.console.height or 32) // 3))),
            Layout(name="help", size=3),
        )
        layout["body"].split_row(Layout(name="days", ratio=3), Layout(name="chart", ratio=2))
        layout["header"].update(self.render_header(rows))
        layout["days"].update(Panel(self.render_days(rows, layout["days"].size or 20), title="[bold]Daily Usage[/bold]", border_style="cyan"))
        layout["chart"].update(self.render_chart(rows, layout["chart"].size or 20))
        layout["detail"].update(self.render_detail(rows))
        layout["help"].update(self.render_help())
        return layout

    def handle_key(self, chars: bytes):
        rows = self.filtered_rows()
        if chars in (b"q", b"\x03"):
            self.running = False
        elif chars in (b"j", b"\x1b[B"):
            self.state.selected = min(self.state.selected + 1, max(len(rows) - 1, 0))
        elif chars in (b"k", b"\x1b[A"):
            self.state.selected = max(self.state.selected - 1, 0)
        elif chars == b"g":
            self.state.selected = 0
        elif chars == b"G":
            self.state.selected = max(len(rows) - 1, 0)
        elif chars == b"m":
            self.state.model_index = (self.state.model_index + 1) % (len(self.models) + 1)
            self.state.selected = 0
            self.state.viewport = 0
        elif chars == b"c":
            self.state.sort_key = "cost"
        elif chars == b"t":
            self.state.sort_key = "tokens"
        elif chars == b"d":
            self.state.sort_key = "date"
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
