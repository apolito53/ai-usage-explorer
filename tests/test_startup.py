from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
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


class TrayLaunchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ai usage launcher ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.script = self.root / SCRIPT_PATH.name
        self.script.write_text(SCRIPT_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "VERSION").write_text("test", encoding="utf-8")
        # Satisfy the UI dependency probe without installing anything in this fixture.
        (self.root / "rich.py").touch()
        python_bin = self.root / ".venv/bin/python"
        python_bin.parent.mkdir(parents=True)
        python_bin.symlink_to(sys.executable)
        self.tray = self.root / "ai-usage-tray.py"
        self.state = self.root / "state"
        self.log = self.state / "ai-usage-explorer/tray.log"
        self.pid_file = self.root / "pid"
        self.addCleanup(self.stop_tray)

    def stop_tray(self):
        if self.pid_file.exists():
            try:
                os.kill(int(self.pid_file.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass

    def launch(self, *args):
        return subprocess.run(
            ["bash", str(self.script), "--no-update", "--no-pricing-update", *args],
            env={
                **os.environ,
                "XDG_STATE_HOME": str(self.state),
                "PYTHONPATH": str(self.root),
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_tray_detaches_preserves_options_and_logs_output(self):
        self.tray.write_text('''
import json
import os
from pathlib import Path
import signal
import sys

root = Path(__file__).parent
(root / "pid").write_text(str(os.getpid()))
try:
    fd = os.open("/dev/tty", os.O_RDONLY)
except OSError:
    has_tty = False
else:
    os.close(fd)
    has_tty = True
(root / "process.json").write_text(json.dumps({
    "pid": os.getpid(), "session": os.getsid(0), "has_tty": has_tty,
    "stdin": sys.stdin.read(), "args": sys.argv[1:],
}))
print("tray stdout", flush=True)
print("tray stderr", file=sys.stderr, flush=True)
signal.pause()
''', encoding="utf-8")

        result = self.launch("--tray", "--project", "project with spaces", "--refresh")

        self.assertEqual(0, result.returncode, result.stderr)
        process = json.loads((self.root / "process.json").read_text())
        os.kill(process["pid"], 0)  # Still alive after the launcher has exited.
        self.assertEqual(process["pid"], process["session"])
        self.assertFalse(process["has_tty"])
        self.assertEqual("", process["stdin"])
        self.assertEqual([
            "--script", str(self.script),
            "--pricing-history", os.environ.get(
                "AI_USAGE_EXPLORER_PRICING_HISTORY",
                str(self.root / ".ai-usage-pricing-history.json"),
            ),
            "--project", "project with spaces", "--online", "--no-pricing-update",
        ], process["args"])
        self.assertIn(f"PID {process['pid']}", result.stdout)
        self.assertIn(str(self.log), result.stdout)
        self.assertEqual("tray stdout\ntray stderr\n", self.log.read_text())
        self.assertEqual(0o600, stat.S_IMODE(self.log.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(self.log.parent.stat().st_mode))

    def test_early_tray_failure_reports_log_and_exit_status(self):
        self.tray.write_text('import sys\nprint("startup failed", file=sys.stderr)\nsys.exit(7)\n')

        result = self.launch("--tray")

        self.assertEqual(7, result.returncode)
        self.assertIn(str(self.log), result.stderr)
        self.assertIn("startup failed", self.log.read_text())
        self.assertNotIn("Tray started", result.stdout)

    def test_foreground_tray_keeps_output_and_exit_status(self):
        self.tray.write_text('import sys\nprint("foreground output", file=sys.stderr)\nsys.exit(7)\n')

        result = self.launch("--tray", "--foreground")

        self.assertEqual(7, result.returncode)
        self.assertIn("foreground output", result.stderr)
        self.assertFalse(self.log.exists())

    def test_foreground_requires_tray(self):
        result = self.launch("--foreground")

        self.assertEqual(1, result.returncode)
        self.assertIn("--foreground requires --tray", result.stderr)


if __name__ == "__main__":
    unittest.main()
