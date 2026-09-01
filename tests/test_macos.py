from __future__ import annotations

import importlib.util
import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MAC_APP_PATH = ROOT / "macos" / "ai-usage-macos.py"
SPEC = importlib.util.spec_from_file_location("ai_usage_macos", MAC_APP_PATH)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("Could not load macOS menu-bar module")
MAC_APP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MAC_APP
SPEC.loader.exec_module(MAC_APP)


class MacOSMenuBarTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
