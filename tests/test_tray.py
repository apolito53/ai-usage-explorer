from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


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
        tray = TRAY.ClaudeUsageTray.__new__(TRAY.ClaudeUsageTray)
        tray.checking_for_update = True
        tray.available_update_revision = None
        tray.update_item = Mock()

        with patch.object(TRAY, "notify_update_available") as notify:
            result = tray._finish_update_check(None)

        self.assertFalse(result)
        tray.update_item.set_label.assert_not_called()
        tray.update_item.show.assert_not_called()
        notify.assert_not_called()

    def test_available_update_is_shown_and_not_repeated(self):
        tray = TRAY.ClaudeUsageTray.__new__(TRAY.ClaudeUsageTray)
        tray.checking_for_update = True
        tray.available_update_revision = None
        tray.update_item = Mock()

        with patch.object(TRAY, "notify_update_available") as notify:
            tray._finish_update_check("abc1234")
            tray._finish_update_check("abc1234")

        tray.update_item.set_label.assert_called_once_with(
            "Update available · abc1234"
        )
        tray.update_item.show.assert_called_once_with()
        notify.assert_called_once_with("abc1234")

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
