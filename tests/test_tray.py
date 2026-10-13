from __future__ import annotations

import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from ai_usage_intraday import ClaudeLogReader


ROOT = Path(__file__).resolve().parents[1]
TRAY_PATH = ROOT / "ai-usage-tray.py"
SPEC = importlib.util.spec_from_file_location("ai_usage_tray", TRAY_PATH)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("Could not load ai-usage-tray.py")
TRAY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TRAY
SPEC.loader.exec_module(TRAY)


class TraySummaryTests(unittest.TestCase):
    def test_configured_symbolic_icon_exists(self):
        self.assertTrue((ROOT / "assets" / f"{TRAY.ICON_NAME}.svg").is_file())

    def test_native_appindicator_fallback_exposes_required_interface(self):
        class FakeFunction:
            def __init__(self, result=None):
                self.result = result
                self.calls = []

            def __call__(self, *args):
                self.calls.append(args)
                return self.result

        class FakeLibrary:
            app_indicator_new = FakeFunction(123)
            app_indicator_set_icon_theme_path = FakeFunction()
            app_indicator_set_status = FakeFunction()
            app_indicator_set_title = FakeFunction()
            app_indicator_set_label = FakeFunction()
            app_indicator_set_menu = FakeFunction()

        library = FakeLibrary()
        adapter = TRAY.CtypesAppIndicator(
            library,
            pointer_from_gobject=lambda _value: 456,
        )
        indicator = adapter.Indicator.new(
            "test-id",
            "test-icon",
            adapter.IndicatorCategory.SYSTEM_SERVICES,
        )

        indicator.set_icon_theme_path("/tmp/icons")
        indicator.set_status(adapter.IndicatorStatus.ACTIVE)
        indicator.set_title("Usage")
        indicator.set_label("$1.00", "$9,999.99")
        indicator.set_menu(object())

        self.assertEqual((b"test-id", b"test-icon", 2), library.app_indicator_new.calls[0])
        self.assertEqual((123, b"/tmp/icons"), library.app_indicator_set_icon_theme_path.calls[0])
        self.assertEqual((123, 1), library.app_indicator_set_status.calls[0])
        self.assertEqual((123, b"Usage"), library.app_indicator_set_title.calls[0])
        self.assertEqual(
            (123, b"$1.00", b"$9,999.99"),
            library.app_indicator_set_label.calls[0],
        )
        self.assertEqual((123, 456), library.app_indicator_set_menu.calls[0])

    def test_default_poll_interval_is_one_minute(self):
        self.assertEqual(60, TRAY.DEFAULT_REFRESH_SECONDS)

    def test_default_panel_period_is_month_to_date(self):
        self.assertEqual("month", TRAY.DEFAULT_DISPLAY_PERIOD)

    def test_current_month_uses_only_matching_monthly_rows(self):
        usage = {
            "monthly": [
                {"month": "2026-07", "totalCost": 99.0},
                {"month": "2026-08", "totalCost": 12.345},
            ]
        }

        self.assertAlmostEqual(
            12.345,
            TRAY.claude_month_cost(usage, datetime(2026, 8, 18, 12, 0)),
        )

    def test_daily_rows_are_a_fallback_when_monthly_data_is_absent(self):
        usage = {
            "daily": [
                {"date": "2026-08-01", "totalCost": 1.25},
                {"date": "2026-08-18", "totalCost": 2.5},
                {"date": "2026-07-31", "totalCost": 100.0},
            ]
        }

        self.assertAlmostEqual(
            3.75,
            TRAY.claude_month_cost(usage, datetime(2026, 8, 18, 12, 0)),
        )

    def test_today_uses_only_rows_matching_current_date(self):
        usage = {
            "daily": [
                {"date": "2026-08-17", "totalCost": 1.25},
                {"date": "2026-08-18", "totalCost": 2.5},
                {"date": "20260818", "totalCost": 0.75},
                {"date": "2026-07-18", "totalCost": 100.0},
            ]
        }

        self.assertAlmostEqual(
            3.25,
            TRAY.claude_today_cost(usage, datetime(2026, 8, 18, 12, 0)),
        )

    def test_panel_period_selects_the_corresponding_total(self):
        self.assertEqual(12.0, TRAY.display_period_cost("month", 12.0, 3.0))
        self.assertEqual(3.0, TRAY.display_period_cost("today", 12.0, 3.0))

    def test_chart_days_are_sorted_combined_and_zero_filled_through_today(self):
        usage = {"daily": [
            {"date": "2026-08-03", "totalCost": "2.5"},
            {"date": "20260801", "totalCost": 1},
            {"date": "2026-08-03", "totalCost": 0.75},
            {"date": "2026-07-31", "totalCost": 100},
            {"date": "2026-08-04", "totalCost": 100},
            {"date": "2026-08", "totalCost": 100},
            {"date": "bad", "totalCost": 100},
            {"date": "2026-08-02", "totalCost": "NaN"},
            {"date": "2026-08-02", "totalCost": "Infinity"},
            {"date": "2026-08-02", "totalCost": -1},
            None,
        ]}
        self.assertEqual(
            ((date(2026, 8, 1), 1.0), (date(2026, 8, 2), 0.0), (date(2026, 8, 3), 3.25)),
            TRAY.claude_daily_costs(usage, datetime(2026, 8, 3)),
        )

    def test_chart_empty_month_and_leap_day(self):
        for payload in ({}, {"daily": None}, {"daily": []}):
            with self.subTest(payload=payload):
                days = TRAY.claude_daily_costs(payload, datetime(2024, 2, 29))
                self.assertEqual(29, len(days))
                self.assertEqual((date(2024, 2, 29), 0.0), days[-1])
                self.assertEqual(0.0, sum(cost for _day, cost in days))
        self.assertEqual(
            ((date(2026, 9, 1), 0.0),),
            TRAY.claude_daily_costs({}, datetime(2026, 9, 1)),
        )

    def test_snapshot_uses_backfilled_daily_data_and_preserves_shared_totals_api(self):
        config = SimpleNamespace(pricing_history_path=Path("/unused"))
        now = datetime(2026, 8, 3)
        response = SimpleNamespace(returncode=0, stdout='{"daily": [{"date": "2026-08-03", "totalCost": 0}]}')

        def backfill(data, _history):
            data["daily"][0]["totalCost"] = 12.5

        with patch.object(TRAY, "datetime") as clock, \
             patch.object(TRAY, "fetch_command", return_value=["fake"]), \
             patch.object(TRAY.subprocess, "run", return_value=response), \
             patch.object(TRAY, "read_pricing_history", return_value={}), \
             patch.object(TRAY, "backfill_missing_costs", side_effect=backfill):
            clock.now.return_value = now
            clock.strptime.side_effect = datetime.strptime
            snapshot = TRAY.fetch_usage_snapshot(config, False)
            self.assertEqual((date(2026, 8, 3), 12.5), snapshot.daily_costs[-1])
            self.assertEqual(12.5, snapshot.month_cost)
            self.assertEqual(12.5, snapshot.today_cost)
            self.assertEqual((12.5, 12.5, now), TRAY.fetch_usage(config, False))

    def test_refresh_updates_open_chart_and_error_keeps_last_snapshot(self):
        tray = TRAY.ClaudeUsageTray.__new__(TRAY.ClaudeUsageTray)
        tray.graph_window = Mock()
        tray.refreshing = True
        tray.has_checked_updates = True
        tray.applying_updates = False
        tray.display_period = "today"
        for name in ("indicator", "month_item", "month_total_item", "today_total_item",
                     "updated_item", "status_item", "refresh_item"):
            setattr(tray, name, Mock())
        snapshot = TRAY.UsageSnapshot(12.5, 3.0, datetime(2026, 8, 3), ())

        tray._finish_success(snapshot)
        tray.graph_window.set_usage.assert_called_once_with(snapshot)
        tray.indicator.set_label.assert_called_with("$3.00", "$9,999.99")
        tray._finish_error("offline")

        self.assertIs(snapshot, tray.snapshot)
        tray.graph_window.set_usage.assert_called_once()
        tray.graph_window.set_status.assert_called_with("Refresh failed: offline", False)

    def test_intraday_read_failure_does_not_discard_successful_totals(self):
        config = SimpleNamespace(pricing_history_path=Path("/unused"), project="")
        now = datetime(2026, 8, 3)
        response = SimpleNamespace(returncode=0, stdout='{"daily": [{"date": "2026-08-03", "totalCost": 12.5}]}')
        reader = Mock()
        reader.load_days.side_effect = OSError("Session log unavailable")
        with patch.object(TRAY, "datetime") as clock, \
             patch.object(TRAY, "fetch_command", return_value=["fake"]), \
             patch.object(TRAY.subprocess, "run", return_value=response), \
             patch.object(TRAY, "read_pricing_history", return_value={}):
            clock.now.return_value = now
            clock.strptime.side_effect = datetime.strptime
            snapshot = TRAY.fetch_usage_snapshot(config, False, reader)
        self.assertEqual(12.5, snapshot.today_cost)
        self.assertIn("Session log unavailable", snapshot.intraday.error)
        self.assertEqual({}, snapshot.intraday_days)

    def test_historical_intraday_costs_use_pricing_effective_on_each_local_day(self):
        now = datetime(2026, 8, 3, 12)
        history = {"snapshots": [
            {"effectiveDate": "2026-08-01", "models": {"claude-test": {"rates": {"input": 5}}}},
            {"effectiveDate": "2026-08-03", "models": {"claude-test": {"rates": {"input": 10}}}},
        ]}
        response = SimpleNamespace(returncode=0, stdout='{"daily": []}')
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            project = root / "wanted"
            project.mkdir()
            records = [
                {"timestamp": datetime(2026, 8, day, 10).astimezone().isoformat(),
                 "message": {"id": str(day), "model": "claude-test", "usage": {"input_tokens": 1_000_000}}}
                for day in (1, 2, 3)
            ]
            records[1]["costUSD"] = 2.0
            (project / "session.jsonl").write_text('\n'.join(json.dumps(row) for row in records))
            config = SimpleNamespace(pricing_history_path=Path("/unused"), project="wanted")
            with patch.object(TRAY, "datetime") as clock, \
                 patch.object(TRAY, "fetch_command", return_value=["fake"]), \
                 patch.object(TRAY.subprocess, "run", return_value=response), \
                 patch.object(TRAY, "read_pricing_history", return_value=history):
                clock.now.return_value = now
                clock.strptime.side_effect = datetime.strptime
                snapshot = TRAY.fetch_usage_snapshot(config, False, ClaudeLogReader([root]))
            self.assertEqual([5.0, 2.0, 10.0], [
                sum(cost for _time, cost in snapshot.intraday_days[date(2026, 8, day)].costs)
                for day in (1, 2, 3)
            ])
            self.assertIs(snapshot.intraday, snapshot.intraday_days[now.date()])

    def test_zero_cost_model_is_backfilled_before_totaling(self):
        usage = {
            "monthly": [
                {
                    "month": "2026-08",
                    "totalCost": 0.0,
                    "modelBreakdowns": [
                        {
                            "modelName": "claude-opus-5",
                            "inputTokens": 1_000_000,
                            "outputTokens": 1_000_000,
                            "cacheCreationTokens": 0,
                            "cacheReadTokens": 0,
                            "cost": 0.0,
                        }
                    ],
                }
            ]
        }
        history = {
            "snapshots": [
                {
                    "effectiveDate": "2026-08-01",
                    "models": {
                        "claude-opus-5": {
                            "rates": {
                                "input": 5.0,
                                "output": 25.0,
                                "cacheWrite": 6.25,
                                "cacheRead": 0.5,
                            }
                        }
                    },
                }
            ]
        }

        self.assertEqual(1, TRAY.backfill_missing_costs(usage, history))
        self.assertAlmostEqual(30.0, usage["monthly"][0]["totalCost"])
        self.assertAlmostEqual(
            30.0,
            TRAY.claude_month_cost(usage, datetime(2026, 8, 18, 12, 0)),
        )

    def test_fetch_refreshes_pricing_only_when_requested(self):
        config = TRAY.TrayConfig(
            script_path=ROOT / "ai-usage-explorer.sh",
            pricing_history_path=ROOT / ".ai-usage-pricing-history.json",
            project="",
            online=False,
            refresh_pricing=True,
            refresh_seconds=60,
            update_check_seconds=3600,
            autostart_action=None,
        )
        now = datetime(2026, 8, 18, 12, 0)

        first = TRAY.fetch_command(config, refresh_pricing=True, now=now)
        later = TRAY.fetch_command(config, refresh_pricing=False, now=now)

        self.assertIn("--dump-claude-tray-json", first)
        self.assertIn("20260801", first)
        self.assertNotIn("--no-pricing-update", first)
        self.assertIn("--no-pricing-update", later)

    def test_autostart_entry_quotes_the_checkout_path(self):
        entry = TRAY.desktop_entry(Path("/tmp/AI Usage/ai-usage-explorer.sh"))

        self.assertIn(
            'Exec="/tmp/AI Usage/ai-usage-explorer.sh" --tray',
            entry,
        )
        self.assertNotIn("--no-update", entry)
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)
        self.assertIn("Terminal=false", entry)

    def test_tray_poll_skips_the_startup_update_check(self):
        config = TRAY.TrayConfig(
            script_path=ROOT / "ai-usage-explorer.sh",
            pricing_history_path=ROOT / ".ai-usage-pricing-history.json",
            project="",
            online=False,
            refresh_pricing=True,
            refresh_seconds=60,
            update_check_seconds=3600,
            autostart_action=None,
        )

        command = TRAY.fetch_command(
            config,
            refresh_pricing=False,
            now=datetime(2026, 8, 18, 12, 0),
        )

        self.assertIn("--no-update", command)

    def test_fast_forward_update_revision_is_detected(self):
        with patch.object(
            TRAY,
            "git_output",
            side_effect=[
                str(ROOT),
                "origin/master",
                "",
                "0\t2",
                "abc1234",
            ],
        ):
            revision = TRAY.available_update_revision(
                ROOT / "ai-usage-explorer.sh"
            )

        self.assertEqual("abc1234", revision)

    def test_current_or_diverged_checkout_has_no_available_update(self):
        for counts in ("0\t0", "1\t2"):
            with self.subTest(counts=counts), patch.object(
                TRAY,
                "git_output",
                side_effect=[str(ROOT), "origin/master", "", counts],
            ):
                revision = TRAY.available_update_revision(
                    ROOT / "ai-usage-explorer.sh"
                )

            self.assertEqual("", revision)

    def test_update_check_failure_is_silent(self):
        tray = self.update_tray()
        tray.checking_for_update = True
        result = tray._finish_update_check(None)

        self.assertFalse(result)
        tray.update_item.set_label.assert_not_called()
        tray.update_item.show.assert_not_called()
        tray.Gtk.MessageDialog.assert_not_called()

    def test_available_update_is_shown_and_not_repeated(self):
        tray = self.update_tray()
        tray._finish_update_check("abc1234", "20.0.31", "running")
        dialog = tray.update_dialog
        self.assertIsNotNone(dialog)
        self.assertIn("20.0.30 → 20.0.31", dialog.format_secondary_text.call_args.args[0])
        tray._update_response(dialog, tray.Gtk.ResponseType.CANCEL, ("abc1234", "20.0.31"))
        tray._finish_update_check("abc1234", "20.0.31", "running")
        tray.Gtk.MessageDialog.assert_called_once()
        tray._apply_updates.assert_not_called()
        tray._show_update_dialog()
        self.assertEqual(2, tray.Gtk.MessageDialog.call_count)
        tray._update_response(tray.update_dialog, tray.Gtk.ResponseType.OK, ("abc1234", "20.0.31"))
        tray._apply_updates.assert_called_once_with("abc1234", "20.0.31")

    def update_tray(self):
        tray = TRAY.ClaudeUsageTray.__new__(TRAY.ClaudeUsageTray)
        tray.checking_for_update = True
        tray.available_update_revision = None
        tray.available_ccusage_version = ""
        tray.restart_revision = ""
        tray.running_revision = "running"
        tray.ccusage_version = "20.0.30"
        tray.shown_updates = set()
        tray.update_dialog = None
        tray.applying_updates = False
        tray.refreshing = False
        tray.refresh_item = Mock()
        tray.update_item = Mock()
        tray.check_updates_item = Mock()
        tray.Gtk = Mock()
        tray.Gtk.ResponseType = SimpleNamespace(OK=1, CANCEL=0)
        tray._apply_updates = Mock()
        return tray

    def test_external_app_update_prompts_restart_and_current_versions_stay_quiet(self):
        tray = self.update_tray()
        tray._finish_update_check("", "20.0.30", "running")
        tray.Gtk.MessageDialog.assert_not_called()
        tray._finish_update_check("", "20.0.30", "new-on-disk")
        self.assertEqual("new-on-disk", tray.restart_revision)
        tray.update_item.set_label.assert_called_with("Restart to load app update…")
        self.assertIn("updated on disk", tray.update_dialog.format_secondary_text.call_args.args[0])

    def test_failed_check_preserves_existing_offer_and_new_release_prompts_again(self):
        tray = self.update_tray()
        tray._finish_update_check("", "20.0.31", "running")
        tray._update_response(tray.update_dialog, 0, ("", "20.0.31"))
        tray._finish_update_check(None, None, None)
        self.assertEqual("20.0.31", tray.available_ccusage_version)
        tray.Gtk.MessageDialog.assert_called_once()
        tray._finish_update_check("", "20.0.32", "running")
        self.assertEqual(2, tray.Gtk.MessageDialog.call_count)

    def test_failed_install_or_restart_leaves_the_current_tray_running(self):
        tray = self.update_tray()
        tray.config = Mock()
        tray._set_status = Mock()
        with patch.object(TRAY, "restart_tray") as restart:
            tray._finish_apply_updates("download failed")
            restart.assert_not_called()
        with patch.object(TRAY, "restart_tray", side_effect=OSError("cannot execute")):
            tray._finish_apply_updates("")
        tray.Gtk.main_quit.assert_not_called()
        self.assertFalse(tray.applying_updates)
        self.assertIn("cannot execute", tray._set_status.call_args.args[0])

    def test_restart_preserves_configuration_and_releases_the_old_ccusage_selection(self):
        config = TRAY.TrayConfig(ROOT / 'ai-usage-explorer.sh', Path('/tmp/custom-prices.json'),
                                'project with spaces', True, False, 120, 7200, None, '/old/ccusage')
        with patch.dict(TRAY.os.environ, {"AI_USAGE_EXPLORER_CCUSAGE_BIN": "/old/ccusage"}), \
             patch.object(TRAY.os, "execve") as execute:
            TRAY.restart_tray(config)
        program, args, environment = execute.call_args.args
        self.assertEqual(str(config.script_path), program)
        self.assertIn('--tray', args)
        self.assertIn('--refresh', args)
        self.assertIn('--no-pricing-update', args)
        self.assertEqual('project with spaces', args[args.index('--project') + 1])
        self.assertNotIn('AI_USAGE_EXPLORER_CCUSAGE_BIN', environment)
        self.assertEqual('120', environment['AI_USAGE_EXPLORER_TRAY_REFRESH_SECONDS'])
        self.assertEqual('7200', environment['AI_USAGE_EXPLORER_TRAY_UPDATE_SECONDS'])
        self.assertEqual('/tmp/custom-prices.json', environment['AI_USAGE_EXPLORER_PRICING_HISTORY'])

    def test_snapshot_reports_and_uses_the_active_ccusage_executable(self):
        config = SimpleNamespace(pricing_history_path=Path('/unused'), ccusage_bin='/chosen/ccusage')
        response = SimpleNamespace(returncode=0, stdout=json.dumps({
            'daily': [], 'ccusagePath': '/chosen/ccusage', 'ccusageVersion': '20.0.30',
        }))
        with patch.object(TRAY, 'fetch_command', return_value=['fake']), \
             patch.object(TRAY.subprocess, 'run', return_value=response) as run, \
             patch.object(TRAY, 'read_pricing_history', return_value={}):
            snapshot = TRAY.fetch_usage_snapshot(config, False)
        self.assertEqual('/chosen/ccusage', snapshot.ccusage_path)
        self.assertEqual('20.0.30', snapshot.ccusage_version)
        self.assertEqual('/chosen/ccusage', run.call_args.kwargs['env']['AI_USAGE_EXPLORER_CCUSAGE_BIN'])

    def test_tray_schedules_hourly_update_checks(self):
        tray = TRAY.ClaudeUsageTray.__new__(TRAY.ClaudeUsageTray)
        tray.config = SimpleNamespace(
            refresh_seconds=60,
            update_check_seconds=3600,
        )
        tray.GLib = SimpleNamespace(timeout_add_seconds=Mock())
        tray.refresh = Mock()

        tray.start()

        tray.refresh.assert_called_once_with()
        self.assertEqual(
            [
                call(60, tray._scheduled_refresh),
                call(3600, tray._scheduled_update_check),
            ],
            tray.GLib.timeout_add_seconds.call_args_list,
        )

    def test_autostart_install_and_remove_are_scoped_to_one_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir)
            target = TRAY.install_autostart(
                ROOT / "ai-usage-explorer.sh",
                config_home=config_home,
                platform_name="linux",
                os_release={"ID": "ubuntu"},
            )

            self.assertEqual(
                config_home / "autostart" / TRAY.AUTOSTART_FILENAME,
                target,
            )
            self.assertTrue(target.is_file())
            self.assertEqual(0o644, stat.S_IMODE(target.stat().st_mode))

            removed_target, removed = TRAY.remove_autostart(config_home)
            self.assertEqual(target, removed_target)
            self.assertTrue(removed)
            self.assertFalse(target.exists())

    def test_autostart_accepts_ubuntu_derived_linux(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = TRAY.install_autostart(
                ROOT / "ai-usage-explorer.sh",
                config_home=Path(temp_dir),
                platform_name="linux",
                os_release={"ID": "pop", "ID_LIKE": "ubuntu debian"},
            )

            self.assertTrue(target.is_file())

    def test_autostart_rejects_non_linux_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir)

            with self.assertRaisesRegex(RuntimeError, "detected darwin"):
                TRAY.install_autostart(
                    ROOT / "ai-usage-explorer.sh",
                    config_home=config_home,
                    platform_name="darwin",
                    os_release={},
                )

            self.assertFalse(TRAY.autostart_path(config_home).exists())

    def test_autostart_rejects_non_ubuntu_linux_without_writing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir)

            with self.assertRaisesRegex(RuntimeError, "detected Fedora Linux"):
                TRAY.install_autostart(
                    ROOT / "ai-usage-explorer.sh",
                    config_home=config_home,
                    platform_name="linux",
                    os_release={"ID": "fedora", "PRETTY_NAME": "Fedora Linux"},
                )

            self.assertFalse(TRAY.autostart_path(config_home).exists())


if __name__ == "__main__":
    unittest.main()
