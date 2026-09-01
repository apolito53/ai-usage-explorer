from __future__ import annotations

import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "ai-usage-explorer.sh"


class StartupTests(unittest.TestCase):
    def test_dependencies_are_checked_before_tray_launch(self):
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        startup = script.rsplit('self_update "${ORIGINAL_ARGS[@]}"\n', 1)[1]

        dependency_check = startup.index("ensure_python_dependencies\n")
        tray_launch = startup.index('if [ "$TRAY" -eq 1 ]; then\n    run_tray\nfi')

        self.assertEqual(0, dependency_check)
        self.assertLess(dependency_check, tray_launch)


if __name__ == "__main__":
    unittest.main()
