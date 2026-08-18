from __future__ import annotations

import importlib.util
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


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

    def test_default_poll_interval_is_one_minute(self):
        self.assertEqual(60, TRAY.DEFAULT_REFRESH_SECONDS)

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
            autostart_action=None,
        )
        now = datetime(2026, 8, 18, 12, 0)

        first = TRAY.fetch_command(config, refresh_pricing=True, now=now)
        later = TRAY.fetch_command(config, refresh_pricing=False, now=now)

        self.assertIn("20260801", first)
        self.assertNotIn("--no-pricing-update", first)
        self.assertIn("--no-pricing-update", later)

    def test_autostart_entry_quotes_the_checkout_path(self):
        entry = TRAY.desktop_entry(Path("/tmp/AI Usage/ai-usage-explorer.sh"))

        self.assertIn(
            'Exec="/tmp/AI Usage/ai-usage-explorer.sh" --tray --no-update',
            entry,
        )
        self.assertIn("X-GNOME-Autostart-enabled=true", entry)
        self.assertIn("Terminal=false", entry)

    def test_autostart_install_and_remove_are_scoped_to_one_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_home = Path(temp_dir)
            target = TRAY.install_autostart(
                ROOT / "ai-usage-explorer.sh",
                config_home=config_home,
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


if __name__ == "__main__":
    unittest.main()
