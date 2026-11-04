from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
MAC_APP_PATH = ROOT / "macos" / "ai-usage-macos.py"
SPEC = importlib.util.spec_from_file_location("ai_usage_macos", MAC_APP_PATH)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("Could not load macOS menu-bar module")
MAC_APP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MAC_APP
SPEC.loader.exec_module(MAC_APP)


class MacOSMenuBarTests(unittest.TestCase):
    def test_refresh_works_outside_checkout_in_fresh_process(self):
        # Isolate imports so the test runner cannot supply the shared helpers.
        probe = """
import json
import runpy
import subprocess
import sys
from datetime import datetime
from unittest.mock import patch

app = runpy.run_path(sys.argv[1])
core = app["CORE"]
config = app["parse_args"]([])
now = datetime(2026, 9, 3, 12)
response = subprocess.CompletedProcess([], 0, stdout=json.dumps({
    "monthly": [{"month": "2026-09", "totalCost": 12.5}],
    "daily": [{"date": "2026-09-03", "totalCost": 3.0}],
}))
with patch.object(core, "datetime") as clock, \\
     patch.object(core.subprocess, "run", return_value=response), \\
     patch.object(core, "read_pricing_history", return_value={}):
    clock.now.return_value = now
    clock.strptime.side_effect = datetime.strptime
    month_cost, today_cost, refreshed_at = core.fetch_usage(config, False)
print(json.dumps([month_cost, today_cost, refreshed_at.isoformat()]))
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-I", "-c", probe, str(MAC_APP_PATH)],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
            )

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            [12.5, 3.0, "2026-09-03T12:00:00"],
            json.loads(result.stdout),
        )

    def test_default_config_points_at_this_checkout(self):
        with patch.dict(
            os.environ,
            {
                "AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS": "60",
                "AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS": "3600",
            },
        ):
            config = MAC_APP.parse_args([])

        self.assertEqual(ROOT / "ai-usage-explorer.sh", config.script_path)
        self.assertEqual(
            ROOT / ".ai-usage-pricing-history.json",
            config.pricing_history_path,
        )
        self.assertEqual(60, config.refresh_seconds)
        self.assertEqual(3600, config.update_check_seconds)

    def test_terminal_command_quotes_checkout_and_project(self):
        config = MAC_APP.parse_args(
            [
                "--script",
                "/tmp/AI Usage/ai-usage-explorer.sh",
                "--project",
                "Project With Spaces",
                "--online",
            ]
        )

        self.assertEqual(
            "cd '/tmp/AI Usage' && '/tmp/AI Usage/ai-usage-explorer.sh' "
            "--project 'Project With Spaces' --refresh",
            MAC_APP.explorer_terminal_command(config),
        )

    def test_applescript_values_are_escaped(self):
        self.assertEqual(
            'a\\\\b\\"c',
            MAC_APP.applescript_quote('a\\b"c'),
        )

    def test_updated_time_has_no_leading_zero(self):
        self.assertEqual(
            "9:05 AM",
            MAC_APP.updated_time(datetime(2026, 9, 1, 9, 5)),
        )

    def test_main_rejects_non_macos_without_importing_cocoa(self):
        if sys.platform == "darwin":
            self.skipTest("non-macOS guard only")
        with redirect_stderr(io.StringIO()) as error:
            self.assertEqual(1, MAC_APP.main([]))
        self.assertIn("can only run on macOS", error.getvalue())


class MacOSGraphIntegrationTests(unittest.TestCase):
    def setUp(self):
        class FakeObject:
            @classmethod
            def alloc(cls):
                return cls()

            def init(self):
                return self

        self.app = Mock()
        self.workspace = Mock()
        self.helper = SimpleNamespace(runEventLoop=Mock(), callAfter=Mock())
        self.foundation = SimpleNamespace(NSObject=FakeObject, NSTimer=Mock(),
                                         NSURL=SimpleNamespace(URLWithString_=lambda value: value))
        appkit = SimpleNamespace(
            NSApplication=SimpleNamespace(sharedApplication=lambda: self.app),
            NSApplicationActivationPolicyAccessory=1, NSControlStateValueOff=0,
            NSControlStateValueOn=1, NSMenu=Mock(), NSMenuItem=Mock(), NSStatusBar=Mock(),
            NSVariableStatusItemLength=-1,
            NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: self.workspace),
        )
        modules = {"objc": SimpleNamespace(python_method=lambda fn: fn), "AppKit": appkit,
                   "Foundation": self.foundation, "PyObjCTools": SimpleNamespace(AppHelper=self.helper)}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        MAC_APP.run_app(MAC_APP.parse_args([]))
        self.delegate = self.app.setDelegate_.call_args.args[0]
        self.delegate._build_menu()

    def test_menu_opens_browser_reuses_server_and_closes_it_on_quit(self):
        snapshot = MAC_APP.CORE.UsageSnapshot(12.5, 3, datetime(2024, 3, 2, 12), ())
        self.delegate._finish_refresh_success(snapshot)
        with patch("ai_usage_web.UsageGraphServer") as server_class:
            server = server_class.return_value
            server.start.return_value = "http://127.0.0.1:12345/private/"
            self.delegate.openGraphs_(None)
            self.delegate.openGraphs_(None)
            server_class.assert_called_once()
            server.set_usage.assert_called_once_with(snapshot)
            self.workspace.openURL_.assert_called_with(server.start.return_value)
            callback = server_class.call_args.kwargs["refresh"]
            callback()
            self.helper.callAfter.assert_called_once_with(self.delegate.refreshNow_, None)
            self.delegate.applicationWillTerminate_(None)
            server.close.assert_called_once_with()

    def test_refresh_fetches_history_and_schedules_snapshot_on_main_thread(self):
        snapshot = MAC_APP.CORE.UsageSnapshot(12.5, 3, datetime(2024, 3, 2, 12), ())
        with patch.object(MAC_APP.CORE, "fetch_usage_snapshot", return_value=snapshot) as fetch, \
             patch.object(MAC_APP.threading, "Thread") as thread:
            self.delegate.refreshNow_(None)
            thread.call_args.kwargs["target"]()
        fetch.assert_called_once_with(self.delegate.config, refresh_pricing=True,
                                      log_reader=self.delegate.log_reader)
        self.helper.callAfter.assert_called_once_with(self.delegate._finish_refresh_success, snapshot)
        self.assertTrue(self.delegate.refreshing)

    def test_error_preserves_snapshot_and_graphs_while_reenabling_refresh(self):
        snapshot = MAC_APP.CORE.UsageSnapshot(12.5, 3, datetime(2024, 3, 2, 12), ())
        self.delegate.graphs = Mock()
        self.delegate._finish_refresh_success(snapshot)
        self.delegate.refreshing = True
        self.delegate._finish_refresh_error("network unavailable")
        self.assertIs(snapshot, self.delegate.snapshot)
        self.delegate.graphs.set_usage.assert_called_once_with(snapshot)
        self.delegate.graphs.set_status.assert_called_with("Refresh failed: network unavailable", False)
        self.assertFalse(self.delegate.refreshing)
        self.delegate.refresh_item.setEnabled_.assert_called_with(True)

    def test_graph_startup_or_browser_failure_is_reported_without_losing_totals(self):
        self.delegate.month_cost = 12.5
        with patch("ai_usage_web.UsageGraphServer") as server_class, redirect_stderr(io.StringIO()):
            server_class.return_value.start.side_effect = OSError("Port unavailable")
            self.delegate.openGraphs_(None)
            self.assertIn("Port unavailable", self.delegate.status_message)
            server_class.return_value.start.side_effect = None
            server_class.return_value.start.return_value = "http://127.0.0.1:12345/private/"
            self.workspace.openURL_.return_value = False
            self.delegate.openGraphs_(None)
            self.assertIn("default browser", self.delegate.status_message)
        self.assertEqual(12.5, self.delegate.month_cost)


if __name__ == "__main__":
    unittest.main()
