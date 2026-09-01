#!/usr/bin/env python3
"""macOS menu-bar companion for the AI Usage Explorer."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shlex
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
TRAY_CORE_PATH = ROOT / "ai-usage-tray.py"


def load_tray_core(path: Path = TRAY_CORE_PATH) -> ModuleType:
    spec = importlib.util.spec_from_file_location("ai_usage_tray_core", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load tray core from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CORE = load_tray_core()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=ROOT / "ai-usage-explorer.sh")
    parser.add_argument(
        "--pricing-history",
        type=Path,
        default=ROOT / ".ai-usage-pricing-history.json",
    )
    parser.add_argument("--project", default="")
    parser.add_argument("--online", action="store_true")
    parser.add_argument(
        "--refresh-seconds",
        type=int,
        default=int(
            os.environ.get(
                "AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS",
                CORE.DEFAULT_REFRESH_SECONDS,
            )
        ),
    )
    parser.add_argument(
        "--update-check-seconds",
        type=int,
        default=int(
            os.environ.get(
                "AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS",
                CORE.DEFAULT_UPDATE_CHECK_SECONDS,
            )
        ),
    )
    args = parser.parse_args(argv)
    if args.refresh_seconds < 10:
        parser.error("--refresh-seconds must be at least 10")
    if args.update_check_seconds < CORE.MIN_UPDATE_CHECK_SECONDS:
        parser.error(
            f"--update-check-seconds must be at least "
            f"{CORE.MIN_UPDATE_CHECK_SECONDS}"
        )
    return CORE.TrayConfig(
        script_path=args.script.expanduser().resolve(),
        pricing_history_path=args.pricing_history.expanduser().resolve(),
        project=args.project,
        online=args.online,
        refresh_pricing=True,
        refresh_seconds=args.refresh_seconds,
        update_check_seconds=args.update_check_seconds,
        autostart_action=None,
    )


def applescript_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def explorer_terminal_command(config) -> str:
    args = [str(config.script_path)]
    if config.project:
        args.extend(["--project", config.project])
    if config.online:
        args.append("--refresh")
    return f"cd {shlex.quote(str(config.script_path.parent))} && {shlex.join(args)}"


def updated_time(value: datetime) -> str:
    return value.strftime("%I:%M %p").lstrip("0")


def run_osascript(source: str) -> None:
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", source],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def notify_update_available(revision: str) -> None:
    message = applescript_quote(
        f"Revision {revision} is available. Pull the checkout and rerun "
        "macos/install.sh to update."
    )
    run_osascript(
        f'display notification "{message}" '
        'with title "AI Usage Explorer update available"'
    )


def open_explorer_in_terminal(config) -> None:
    command = applescript_quote(explorer_terminal_command(config))
    run_osascript(
        'tell application "Terminal"\n'
        "activate\n"
        f'do script "{command}"\n'
        "end tell"
    )


def run_app(config) -> int:
    try:
        import objc
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSControlStateValueOff,
            NSControlStateValueOn,
            NSMenu,
            NSMenuItem,
            NSStatusBar,
            NSVariableStatusItemLength,
        )
        from Foundation import NSObject, NSTimer
        from PyObjCTools import AppHelper
    except ImportError as exc:
        print(
            "ERROR: PyObjC Cocoa support is required. Run ./macos/install.sh.",
            file=sys.stderr,
        )
        return 1

    class AppDelegate(NSObject):
        @objc.python_method
        def configure(self, tray_config):
            self.config = tray_config
            self.refreshing = False
            self.checking_for_update = False
            self.has_refreshed_pricing = not tray_config.refresh_pricing
            self.available_revision = None
            self.display_period = CORE.DEFAULT_DISPLAY_PERIOD
            self.month_cost = None
            self.today_cost = None

        def applicationDidFinishLaunching_(self, _notification):
            self._build_menu()
            self.refreshNow_(None)
            self.checkForUpdates_(None)
            self.refresh_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.config.refresh_seconds,
                self,
                "scheduledRefresh:",
                None,
                True,
            )
            self.update_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                self.config.update_check_seconds,
                self,
                "checkForUpdates:",
                None,
                True,
            )

        @objc.python_method
        def _disabled_item(self, title):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title,
                None,
                "",
            )
            item.setEnabled_(False)
            return item

        @objc.python_method
        def _action_item(self, title, selector):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title,
                selector,
                "",
            )
            item.setTarget_(self)
            return item

        @objc.python_method
        def _build_menu(self):
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            self.status_item.button().setTitle_("…")

            menu = NSMenu.alloc().init()
            self.month_item = self._disabled_item(
                "Claude · " + datetime.now().strftime("%B %Y")
            )
            self.month_total_item = self._disabled_item("Month to date: …")
            self.today_total_item = self._disabled_item("Today: …")
            self.updated_item = self._disabled_item("Not refreshed yet")
            self.message_item = self._disabled_item("")
            self.message_item.setHidden_(True)
            self.update_item = self._disabled_item("")
            self.update_item.setHidden_(True)
            for item in (
                self.month_item,
                self.month_total_item,
                self.today_total_item,
                self.updated_item,
                self.message_item,
                self.update_item,
            ):
                menu.addItem_(item)

            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(self._disabled_item("Show in menu bar"))
            self.month_choice = self._action_item("Month to date", "showMonth:")
            self.today_choice = self._action_item("Today", "showToday:")
            self.month_choice.setState_(NSControlStateValueOn)
            self.today_choice.setState_(NSControlStateValueOff)
            menu.addItem_(self.month_choice)
            menu.addItem_(self.today_choice)

            menu.addItem_(NSMenuItem.separatorItem())
            self.refresh_item = self._action_item("Refresh now", "refreshNow:")
            menu.addItem_(self.refresh_item)
            menu.addItem_(
                self._action_item("Open AI Usage Explorer", "openExplorer:")
            )
            menu.addItem_(NSMenuItem.separatorItem())
            menu.addItem_(self._action_item("Quit", "quitApp:"))
            self.status_item.setMenu_(menu)

        @objc.python_method
        def _set_message(self, message):
            self.message_item.setTitle_(message)
            self.message_item.setHidden_(not bool(message))

        @objc.python_method
        def _update_title(self):
            if self.month_cost is None or self.today_cost is None:
                title = "…"
            else:
                title = CORE.format_cost(
                    CORE.display_period_cost(
                        self.display_period,
                        self.month_cost,
                        self.today_cost,
                    )
                )
            self.status_item.button().setTitle_(title)

        def showMonth_(self, _sender):
            self.display_period = "month"
            self.month_choice.setState_(NSControlStateValueOn)
            self.today_choice.setState_(NSControlStateValueOff)
            self._update_title()

        def showToday_(self, _sender):
            self.display_period = "today"
            self.month_choice.setState_(NSControlStateValueOff)
            self.today_choice.setState_(NSControlStateValueOn)
            self._update_title()

        def scheduledRefresh_(self, _timer):
            self.refreshNow_(None)

        def refreshNow_(self, _sender):
            if self.refreshing:
                return
            self.refreshing = True
            self.refresh_item.setEnabled_(False)
            self._set_message("Refreshing…")

            def worker():
                try:
                    result = CORE.fetch_usage(
                        self.config,
                        refresh_pricing=not self.has_refreshed_pricing,
                    )
                except Exception as exc:
                    AppHelper.callAfter(self._finish_refresh_error, str(exc))
                else:
                    AppHelper.callAfter(self._finish_refresh_success, *result)

            threading.Thread(target=worker, daemon=True).start()

        @objc.python_method
        def _finish_refresh_success(self, month_cost, today_cost, refreshed_at):
            self.has_refreshed_pricing = True
            self.month_cost = month_cost
            self.today_cost = today_cost
            self._update_title()
            self.month_item.setTitle_(
                "Claude · " + refreshed_at.strftime("%B %Y")
            )
            self.month_total_item.setTitle_(
                f"Month to date: {CORE.format_cost(month_cost)}"
            )
            self.today_total_item.setTitle_(
                f"Today: {CORE.format_cost(today_cost)}"
            )
            self.updated_item.setTitle_("Updated " + updated_time(refreshed_at))
            self._set_message("")
            self.refreshing = False
            self.refresh_item.setEnabled_(True)

        @objc.python_method
        def _finish_refresh_error(self, message):
            one_line = " ".join(message.split())
            self._set_message("Refresh failed: " + one_line[:100])
            self.refreshing = False
            self.refresh_item.setEnabled_(True)

        def checkForUpdates_(self, _sender):
            if self.checking_for_update:
                return
            self.checking_for_update = True

            def worker():
                revision = CORE.available_update_revision(self.config.script_path)
                AppHelper.callAfter(self._finish_update_check, revision)

            threading.Thread(target=worker, daemon=True).start()

        @objc.python_method
        def _finish_update_check(self, revision):
            self.checking_for_update = False
            if not revision or revision == self.available_revision:
                return
            self.available_revision = revision
            self.update_item.setTitle_(f"Update available · {revision}")
            self.update_item.setHidden_(False)
            notify_update_available(revision)

        def openExplorer_(self, _sender):
            open_explorer_in_terminal(self.config)

        def quitApp_(self, _sender):
            NSApplication.sharedApplication().terminate_(None)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    delegate.configure(config)
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()
    return 0


def main(argv=None) -> int:
    if sys.platform != "darwin":
        print("ERROR: The macOS menu-bar app can only run on macOS.", file=sys.stderr)
        return 1
    return run_app(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
