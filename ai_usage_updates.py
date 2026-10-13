"""Resolve ccusage locally; install a specific release only when requested."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


REGISTRY_URL = "https://registry.npmjs.org/ccusage/latest"
INSTALL_TIMEOUT_SECONDS = 180


def version_tuple(value):
    """Accept stable releases, without accidentally comparing versions as text."""
    match = re.fullmatch(r"(?:ccusage\s+)?v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value.strip())
    return tuple(map(int, match.groups())) if match else None


def newer_version(candidate, current):
    candidate_version = version_tuple(candidate)
    current_version = version_tuple(current)
    return candidate_version is not None and current_version is not None and candidate_version > current_version


def latest_ccusage_version():
    try:
        request = urllib.request.Request(REGISTRY_URL, headers={"User-Agent": "ai-usage-explorer"})
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(1_000_000))
        version = payload.get("version", "") if isinstance(payload, dict) else ""
        return version if isinstance(version, str) and version_tuple(version) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def ccusage_cache():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "ai-usage-explorer" / "ccusage"


def resolve_ccusage():
    pinned = os.environ.get("AI_USAGE_EXPLORER_CCUSAGE_BIN")
    if pinned:
        if not Path(pinned).is_file() or not os.access(pinned, os.X_OK):
            raise RuntimeError("The active ccusage executable is unavailable. Restart the tray to select it again.")
        return Path(pinned)
    # Resolve the version directory so a later update cannot change a running tray's command.
    managed = (ccusage_cache() / "current").resolve() / "node_modules" / ".bin" / "ccusage"
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed
    installed = shutil.which("ccusage")
    if installed:
        return Path(installed)
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    candidates = list((cache / "pnpm" / "dlx").glob("*/*/node_modules/.bin/ccusage"))
    candidates.extend((Path.home() / ".npm" / "_npx").glob("*/node_modules/.bin/ccusage"))
    candidates = [path for path in candidates if path.is_file() and os.access(path, os.X_OK)]
    if not candidates:
        return None
    selected = max(candidates, key=lambda path: path.stat().st_mtime)
    return selected.parent.resolve() / selected.name


def ccusage_version(command):
    try:
        result = subprocess.run([str(command), "--version"], capture_output=True, text=True,
                                check=False, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    parsed = version_tuple(result.stdout) if result.returncode == 0 else None
    return ".".join(map(str, parsed)) if parsed else ""


def install_ccusage(version):
    if not version_tuple(version) or version != ".".join(map(str, version_tuple(version))):
        raise RuntimeError("Invalid ccusage release version")
    root = ccusage_cache()
    target = root / version
    command = target / "node_modules" / ".bin" / "ccusage"
    if ccusage_version(command) != version:
        npm, pnpm = shutil.which("npm"), shutil.which("pnpm")
        if npm:
            args = [npm, "install", "--prefix", str(target), "--no-audit", "--no-fund",
                    "--ignore-scripts", "--save-exact", f"ccusage@{version}"]
        elif pnpm:
            target.mkdir(parents=True, exist_ok=True)
            args = [pnpm, "--dir", str(target), "add", "--ignore-scripts", "--save-exact", f"ccusage@{version}"]
        else:
            raise RuntimeError("Updating ccusage requires npm or pnpm in your Node environment")
        try:
            result = subprocess.run(args, check=False, capture_output=True, text=True,
                                    timeout=INSTALL_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("The ccusage download timed out; the current tray is still running") from exc
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip()).splitlines()
            raise RuntimeError("Could not install ccusage: " + (detail[-1] if detail else "package manager failed"))
        if ccusage_version(command) != version:
            raise RuntimeError("The downloaded ccusage version could not be verified")
    # Publish the new selection only after the executable has passed its version check.
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".activate-", dir=root) as staging:
        link = Path(staging) / "current"
        link.symlink_to(target)
        os.replace(link, root / "current")
    return command


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("ensure", "version", "install"))
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv)
    try:
        if args.action == "version":
            print(ccusage_version(args.value))
        elif args.action == "install":
            if not args.value:
                raise RuntimeError("A ccusage release version is required")
            print(install_ccusage(args.value))
        else:
            command = resolve_ccusage()
            if command is None:
                version = latest_ccusage_version()
                if not version:
                    raise RuntimeError("No installed ccusage found and the package registry is unavailable")
                command = install_ccusage(version)
            print(command)
    except (OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
