#!/usr/bin/env python3
"""Ubuntu AppIndicator showing Claude's current usage cost."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple


APP_ID = "ai-usage-explorer"
ICON_NAME = "ai-usage-claude-symbolic"
DEFAULT_REFRESH_SECONDS = 60
DEFAULT_UPDATE_CHECK_SECONDS = 60 * 60
MIN_UPDATE_CHECK_SECONDS = 5 * 60
DEFAULT_DISPLAY_PERIOD = "month"
FETCH_TIMEOUT_SECONDS = 45
UPDATE_CHECK_TIMEOUT_SECONDS = 20
AUTOSTART_FILENAME = "ai-usage-explorer-tray.desktop"
OS_RELEASE_PATH = Path("/etc/os-release")


def safe_float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def row_date(row: Dict) -> str:
    return str(row.get("date") or row.get("month") or row.get("period") or "")


def parse_row_date(value: str) -> Optional[datetime]:
    for date_format in ("%Y-%m-%d", "%Y%m%d", "%Y-%m"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    return None


def read_pricing_history(path: Path) -> Dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            history = json.load(handle)
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
    for candidate in (simplified, str(model or "").strip().lower()):
        if candidate in models:
            return models[candidate]
    for key, record in models.items():
        normalized_key = simplify_model_name(key)
        if (
            normalized_key == simplified
            or normalized_key.startswith(simplified)
            or simplified.startswith(normalized_key)
        ):
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
    """Mirror the explorer's zero-cost pricing fallback for tray totals."""
    updated = 0
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
                if not isinstance(item, dict) or safe_float(item.get("cost")) != 0.0:
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
                    safe_float(item.get("cost"))
                    for item in breakdowns
                    if isinstance(item, dict)
                )
    return updated


def claude_month_cost(data: Dict, now: Optional[datetime] = None) -> float:
    """Return the Claude cost for the calendar month containing ``now``."""
    current = now or datetime.now()
    month_key = current.strftime("%Y-%m")
    monthly_rows = data.get("monthly", [])
    if isinstance(monthly_rows, list) and monthly_rows:
        return sum(
            safe_float(row.get("totalCost"))
            for row in monthly_rows
            if isinstance(row, dict) and row_date(row).startswith(month_key)
        )

    daily_rows = data.get("daily", [])
    if not isinstance(daily_rows, list):
        return 0.0
    return sum(
        safe_float(row.get("totalCost"))
        for row in daily_rows
        if isinstance(row, dict) and row_date(row).startswith(month_key)
    )


def claude_today_cost(data: Dict, now: Optional[datetime] = None) -> float:
    """Return the Claude cost for the calendar day containing ``now``."""
    current_date = (now or datetime.now()).date()
    daily_rows = data.get("daily", [])
    if not isinstance(daily_rows, list):
        return 0.0
    return sum(
        safe_float(row.get("totalCost"))
        for row in daily_rows
        if isinstance(row, dict)
        and (parsed := parse_row_date(row_date(row))) is not None
        and parsed.date() == current_date
    )


def display_period_cost(period: str, month_cost: float, today_cost: float) -> float:
    return today_cost if period == "today" else month_cost


def format_cost(value: float) -> str:
    return f"${value:,.2f}"


@dataclass(frozen=True)
class TrayConfig:
    script_path: Path
    pricing_history_path: Path
    project: str
    online: bool
    refresh_pricing: bool
    refresh_seconds: int
    update_check_seconds: int
    autostart_action: Optional[str]


def desktop_exec_arg(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in ('"', "`", "$"):
        escaped = escaped.replace(character, "\\" + character)
    return f'"{escaped}"'


def desktop_entry(script_path: Path) -> str:
    command = f"{desktop_exec_arg(str(script_path))} --tray"
    return "\n".join(
        (
            "[Desktop Entry]",
            "Type=Application",
            "Version=1.0",
            "Name=AI Usage Explorer Tray",
            "Comment=Show Claude month-to-date usage in the Ubuntu panel",
            f"Exec={command}",
            "Terminal=false",
            "StartupNotify=false",
            "X-GNOME-Autostart-enabled=true",
            "X-GNOME-Autostart-Delay=3",
            "",
        )
    )


def autostart_path(config_home: Optional[Path] = None) -> Path:
    if config_home is None:
        configured = os.environ.get("XDG_CONFIG_HOME")
        config_home = Path(configured).expanduser() if configured else Path.home() / ".config"
    return config_home / "autostart" / AUTOSTART_FILENAME


def read_os_release(path: Path = OS_RELEASE_PATH) -> Dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    release = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        release[key] = value
    return release


def require_ubuntu_autostart(
    platform_name: Optional[str] = None,
    os_release: Optional[Dict[str, str]] = None,
) -> None:
    detected_platform = platform_name if platform_name is not None else sys.platform
    if not detected_platform.startswith("linux"):
        raise RuntimeError(
            "tray autostart is supported only on Ubuntu or Ubuntu-derived Linux "
            f"(detected {detected_platform})"
        )

    release = os_release if os_release is not None else read_os_release()
    ubuntu_family = {str(release.get("ID", "")).lower()}
    ubuntu_family.update(str(release.get("ID_LIKE", "")).lower().split())
    if "ubuntu" not in ubuntu_family:
        detected_os = release.get("PRETTY_NAME") or release.get("ID") or "unknown Linux"
        raise RuntimeError(
            "tray autostart is supported only on Ubuntu or Ubuntu-derived Linux "
            f"(detected {detected_os})"
        )


def install_autostart(
    script_path: Path,
    config_home: Optional[Path] = None,
    platform_name: Optional[str] = None,
    os_release: Optional[Dict[str, str]] = None,
) -> Path:
    require_ubuntu_autostart(platform_name, os_release)
    target = autostart_path(config_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".ai-usage-explorer-tray-",
            suffix=".desktop",
            dir=str(target.parent),
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(desktop_entry(script_path))
        temp_path.chmod(0o644)
        os.replace(str(temp_path), str(target))
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return target


def remove_autostart(config_home: Optional[Path] = None) -> Tuple[Path, bool]:
    target = autostart_path(config_home)
    try:
        target.unlink()
    except FileNotFoundError:
        return target, False
    return target, True


def fetch_command(config: TrayConfig, refresh_pricing: bool, now: datetime) -> list:
    command = [
        str(config.script_path),
        "--dump-claude-tray-json",
        "--no-update",
        "--since",
        now.strftime("%Y%m01"),
    ]
    if not refresh_pricing:
        command.append("--no-pricing-update")
    if config.project:
        command.extend(["--project", config.project])
    if config.online:
        command.append("--refresh")
    return command


def fetch_usage(config: TrayConfig, refresh_pricing: bool) -> Tuple[float, float, datetime]:
    now = datetime.now()
    result = subprocess.run(
        fetch_command(config, refresh_pricing, now),
        check=False,
        capture_output=True,
        text=True,
        timeout=FETCH_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        stderr_lines = [
            line.strip()
            for line in result.stderr.splitlines()
            if line.strip()
            and line.strip() != "logout"
            and "cannot set terminal process group" not in line
            and "no job control in this shell" not in line
        ]
        detail = stderr_lines[-1] if stderr_lines else f"ccusage exited {result.returncode}"
        raise RuntimeError(detail)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ccusage returned invalid JSON") from exc
    backfill_missing_costs(data, read_pricing_history(config.pricing_history_path))
    return claude_month_cost(data, now), claude_today_cost(data, now), now


def git_output(repo_path: Path, *args: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=UPDATE_CHECK_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def available_update_revision(script_path: Path) -> Optional[str]:
    """Return a fast-forward update revision, or stay quiet when none can be found."""
    repo_path = script_path.resolve().parent
    git_root = git_output(repo_path, "rev-parse", "--show-toplevel")
    if git_root is None or Path(git_root).resolve() != repo_path:
        return None
    if git_output(
        repo_path,
        "rev-parse",
        "--abbrev-ref",
        "--symbolic-full-name",
        "@{u}",
    ) is None:
        return None
    if git_output(repo_path, "fetch", "--quiet") is None:
        return None

    counts = git_output(
        repo_path,
        "rev-list",
        "--left-right",
        "--count",
        "HEAD...@{u}",
    )
    if counts is None:
        return None
    try:
        local_ahead, remote_ahead = (int(value) for value in counts.split())
    except (TypeError, ValueError):
        return None
    if local_ahead != 0 or remote_ahead == 0:
        return ""
    return git_output(repo_path, "rev-parse", "--short", "@{u}")


def notify_update_available(revision: str) -> None:
    notifier = shutil.which("notify-send")
    if not notifier:
        return
    try:
        subprocess.Popen(
            [
                notifier,
                "AI Usage Explorer update available",
                f"Revision {revision} is ready. Restart the tray to install it.",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def load_gtk():
    try:
        import gi
    except ImportError as exc:
        raise RuntimeError(
            "python3-gi is required (sudo apt install python3-gi)"
        ) from exc

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    indicator_module = None
    for namespace in ("AyatanaAppIndicator3", "AppIndicator3"):
        try:
            gi.require_version(namespace, "0.1")
            indicator_module = importlib.import_module(f"gi.repository.{namespace}")
            break
        except (ImportError, ValueError):
            continue
    if indicator_module is None:
        raise RuntimeError(
            "an AppIndicator typelib is required (install "
            "gir1.2-ayatanaappindicator3-0.1 or gir1.2-appindicator3-0.1)"
        )
    return Gtk, GLib, indicator_module


class ClaudeUsageTray:
    def __init__(self, config: TrayConfig, gtk, glib, app_indicator):
        self.config = config
        self.Gtk = gtk
        self.GLib = glib
        self.AppIndicator = app_indicator
        self.refreshing = False
        self.checking_for_update = False
        self.available_update_revision: Optional[str] = None
        self.has_refreshed_pricing = not config.refresh_pricing
        self.display_period = DEFAULT_DISPLAY_PERIOD
        self.month_cost: Optional[float] = None
        self.today_cost: Optional[float] = None

        icon_dir = Path(__file__).resolve().parent / "assets"
        self.indicator = app_indicator.Indicator.new(
            APP_ID,
            ICON_NAME,
            app_indicator.IndicatorCategory.SYSTEM_SERVICES,
        )
        self.indicator.set_icon_theme_path(str(icon_dir))
        self.indicator.set_status(app_indicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("Claude usage")
        self.indicator.set_label("…", "$9,999.99")
        self.indicator.set_menu(self._build_menu())

    def _disabled_item(self, label: str):
        item = self.Gtk.MenuItem(label=label)
        item.set_sensitive(False)
        return item

    def _build_menu(self):
        menu = self.Gtk.Menu()
        self.month_item = self._disabled_item(
            "Claude · " + datetime.now().strftime("%B %Y")
        )
        self.month_total_item = self._disabled_item("Month to date: …")
        self.today_total_item = self._disabled_item("Today: …")
        self.updated_item = self._disabled_item("Not refreshed yet")
        self.status_item = self._disabled_item("")
        self.update_item = self._disabled_item("")
        for item in (
            self.month_item,
            self.month_total_item,
            self.today_total_item,
            self.updated_item,
            self.status_item,
            self.update_item,
        ):
            menu.append(item)
        menu.append(self.Gtk.SeparatorMenuItem())

        menu.append(self._disabled_item("Show in panel"))
        self.month_radio = self.Gtk.RadioMenuItem.new_with_label(
            None,
            "Month to date",
        )
        self.today_radio = self.Gtk.RadioMenuItem.new_with_label_from_widget(
            self.month_radio,
            "Today",
        )
        self.month_radio.set_active(True)
        self.month_radio.connect("toggled", self._select_display_period, "month")
        self.today_radio.connect("toggled", self._select_display_period, "today")
        menu.append(self.month_radio)
        menu.append(self.today_radio)
        menu.append(self.Gtk.SeparatorMenuItem())

        self.refresh_item = self.Gtk.MenuItem(label="Refresh now")
        self.refresh_item.connect("activate", self.refresh)
        menu.append(self.refresh_item)

        open_item = self.Gtk.MenuItem(label="Open AI Usage Explorer")
        open_item.connect("activate", self.open_explorer)
        menu.append(open_item)

        menu.append(self.Gtk.SeparatorMenuItem())
        quit_item = self.Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda _item: self.Gtk.main_quit())
        menu.append(quit_item)
        menu.show_all()
        self.status_item.hide()
        self.update_item.hide()
        return menu

    def _set_status(self, message: str):
        self.status_item.set_label(message)
        if message:
            self.status_item.show()
        else:
            self.status_item.hide()

    def _select_display_period(self, item, period: str):
        if not item.get_active():
            return
        self.display_period = period
        self._update_panel_label()

    def _update_panel_label(self):
        if self.month_cost is None or self.today_cost is None:
            formatted = "…"
        else:
            formatted = format_cost(
                display_period_cost(
                    self.display_period,
                    self.month_cost,
                    self.today_cost,
                )
            )
        self.indicator.set_label(formatted, "$9,999.99")

    def start(self):
        self.refresh()
        self.GLib.timeout_add_seconds(
            self.config.refresh_seconds,
            self._scheduled_refresh,
        )
        self.GLib.timeout_add_seconds(
            self.config.update_check_seconds,
            self._scheduled_update_check,
        )

    def _scheduled_refresh(self):
        self.refresh()
        return True

    def _scheduled_update_check(self):
        self.check_for_updates()
        return True

    def check_for_updates(self):
        if self.checking_for_update:
            return
        self.checking_for_update = True

        def worker():
            revision = available_update_revision(self.config.script_path)
            self.GLib.idle_add(self._finish_update_check, revision)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_check(self, revision: Optional[str]):
        self.checking_for_update = False
        if not revision or revision == self.available_update_revision:
            return False
        self.available_update_revision = revision
        self.update_item.set_label(f"Update available · {revision}")
        self.update_item.show()
        notify_update_available(revision)
        return False

    def refresh(self, _item=None):
        if self.refreshing:
            return
        self.refreshing = True
        self.refresh_item.set_sensitive(False)
        self._set_status("Refreshing…")

        def worker():
            try:
                result = fetch_usage(
                    self.config,
                    refresh_pricing=not self.has_refreshed_pricing,
                )
            except Exception as exc:  # Keep the GTK loop alive on fetch failures.
                self.GLib.idle_add(self._finish_error, str(exc))
            else:
                self.GLib.idle_add(self._finish_success, *result)

        threading.Thread(target=worker, daemon=True).start()

    def _finish_success(
        self,
        month_cost: float,
        today_cost: float,
        refreshed_at: datetime,
    ):
        self.has_refreshed_pricing = True
        self.month_cost = month_cost
        self.today_cost = today_cost
        self._update_panel_label()
        self.month_item.set_label("Claude · " + refreshed_at.strftime("%B %Y"))
        self.month_total_item.set_label(
            f"Month to date: {format_cost(month_cost)}"
        )
        self.today_total_item.set_label(f"Today: {format_cost(today_cost)}")
        self.updated_item.set_label("Updated " + refreshed_at.strftime("%-I:%M %p"))
        self._set_status("")
        self._finish_refresh()
        return False

    def _finish_error(self, message: str):
        one_line = " ".join(message.split())
        self._set_status("Refresh failed: " + one_line[:100])
        self._finish_refresh()
        return False

    def _finish_refresh(self):
        self.refreshing = False
        self.refresh_item.set_sensitive(True)

    def open_explorer(self, _item=None):
        explorer_args = [str(self.config.script_path)]
        if self.config.project:
            explorer_args.extend(["--project", self.config.project])
        if self.config.online:
            explorer_args.append("--refresh")

        gnome_terminal = shutil.which("gnome-terminal")
        fallback_terminal = shutil.which("x-terminal-emulator")
        if gnome_terminal:
            command = [
                gnome_terminal,
                "--title",
                "AI Usage Explorer",
                "--",
                *explorer_args,
            ]
        elif fallback_terminal:
            command = [fallback_terminal, "-T", "AI Usage Explorer", "-e", *explorer_args]
        else:
            self._set_status("Could not find a terminal application")
            return
        try:
            subprocess.Popen(command, start_new_session=True)
        except OSError as exc:
            self._set_status(f"Could not open explorer: {exc}")


def parse_args(argv=None) -> TrayConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--pricing-history", required=True, type=Path)
    parser.add_argument("--project", default="")
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--no-pricing-update", action="store_false", dest="refresh_pricing")
    autostart = parser.add_mutually_exclusive_group()
    autostart.add_argument(
        "--install-autostart",
        action="store_const",
        const="install",
        dest="autostart_action",
    )
    autostart.add_argument(
        "--remove-autostart",
        action="store_const",
        const="remove",
        dest="autostart_action",
    )
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=int(
            os.environ.get(
                "AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS",
                DEFAULT_REFRESH_SECONDS,
            )
        ),
    )
    parser.add_argument(
        "--update-check-seconds",
        type=int,
        default=int(
            os.environ.get(
                "AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS",
                DEFAULT_UPDATE_CHECK_SECONDS,
            )
        ),
    )
    args = parser.parse_args(argv)
    if args.refresh_seconds < 10:
        parser.error("--refresh-seconds must be at least 10")
    if args.update_check_seconds < MIN_UPDATE_CHECK_SECONDS:
        parser.error(
            f"--update-check-seconds must be at least {MIN_UPDATE_CHECK_SECONDS}"
        )
    return TrayConfig(
        script_path=args.script.resolve(),
        pricing_history_path=args.pricing_history.resolve(),
        project=args.project,
        online=args.online,
        refresh_pricing=args.refresh_pricing,
        refresh_seconds=args.refresh_seconds,
        update_check_seconds=args.update_check_seconds,
        autostart_action=args.autostart_action,
    )


def main(argv=None) -> int:
    config = parse_args(argv)
    if config.autostart_action == "install":
        try:
            target = install_autostart(config.script_path)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"Installed Ubuntu startup entry: {target}")
        return 0
    if config.autostart_action == "remove":
        target, removed = remove_autostart()
        if removed:
            print(f"Removed Ubuntu startup entry: {target}")
        else:
            print(f"Ubuntu startup entry was not installed: {target}")
        return 0
    try:
        gtk, glib, app_indicator = load_gtk()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    initialized, _unused = gtk.init_check()
    if not initialized:
        print("ERROR: Could not connect to the Ubuntu desktop session.", file=sys.stderr)
        return 1
    tray = ClaudeUsageTray(config, gtk, glib, app_indicator)
    tray.start()
    gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
