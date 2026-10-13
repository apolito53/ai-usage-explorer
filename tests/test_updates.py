import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

import ai_usage_updates as UPDATES
from test_tray import TRAY


def executable(path, version):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\nprintf "ccusage {version}\\n"\n')
    path.chmod(0o755)
    return path


class CcusageUpdateTests(unittest.TestCase):
    def test_compares_stable_versions_numerically_and_ignores_invalid_releases(self):
        self.assertTrue(UPDATES.newer_version('20.0.30', '20.0.9'))
        self.assertTrue(UPDATES.newer_version('21.0.0', 'ccusage 20.9.9'))
        for candidate in ('20.0.30', '19.9.9', '20.0.31-beta.1', 'latest', '20.0.31;exit', ''):
            self.assertFalse(UPDATES.newer_version(candidate, '20.0.30'))

    def test_registry_checks_are_read_only_and_fail_quietly(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        for payload, expected in (({'version': '20.0.31'}, '20.0.31'), ({'version': []}, None),
                                  ([], None), ({'version': '20.0.31-beta'}, None)):
            response.read.return_value = json.dumps(payload).encode()
            with patch.object(UPDATES.urllib.request, 'urlopen', return_value=response), \
                 patch.object(UPDATES, 'install_ccusage') as install:
                self.assertEqual(expected, UPDATES.latest_ccusage_version())
                install.assert_not_called()
        with patch.object(UPDATES.urllib.request, 'urlopen', side_effect=OSError('offline')):
            self.assertIsNone(UPDATES.latest_ccusage_version())

    def test_pinned_command_survives_new_cache_selection_and_missing_pin_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = executable(root / '20.0.30/node_modules/.bin/ccusage', '20.0.30')
            new = executable(root / '20.0.31/node_modules/.bin/ccusage', '20.0.31')
            (root / 'current').symlink_to(old.parents[2])
            with patch.object(UPDATES, 'ccusage_cache', return_value=root), \
                 patch.dict(os.environ, {'AI_USAGE_EXPLORER_CCUSAGE_BIN': ''}):
                selected = UPDATES.resolve_ccusage()
                self.assertEqual(old, selected)
                (root / 'current').unlink()
                (root / 'current').symlink_to(new.parents[2])
                self.assertEqual('20.0.30', UPDATES.ccusage_version(selected))
                self.assertEqual(new, UPDATES.resolve_ccusage())
                with patch.dict(os.environ, {'AI_USAGE_EXPLORER_CCUSAGE_BIN': str(old)}):
                    self.assertEqual(old, UPDATES.resolve_ccusage())
                    old.unlink()
                    with self.assertRaisesRegex(RuntimeError, 'active ccusage executable'):
                        UPDATES.resolve_ccusage()

    def test_failed_download_or_wrong_version_preserves_previous_selection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = executable(root / '20.0.30/node_modules/.bin/ccusage', '20.0.30')
            (root / 'current').symlink_to(old.parents[2])
            for result in (Mock(returncode=1, stderr='download failed', stdout=''),
                           Mock(returncode=0, stderr='', stdout='')):
                with patch.object(UPDATES, 'ccusage_cache', return_value=root), \
                     patch.object(UPDATES.shutil, 'which', return_value='/fake/npm'), \
                     patch.object(UPDATES, 'ccusage_version', return_value=''), \
                     patch.object(UPDATES.subprocess, 'run', return_value=result):
                    with self.assertRaises(RuntimeError):
                        UPDATES.install_ccusage('20.0.31')
                self.assertEqual(old.parents[2], (root / 'current').resolve())

    def test_verified_cached_install_activates_without_downloading_again(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            command = executable(root / '20.0.31/node_modules/.bin/ccusage', '20.0.31')
            with patch.object(UPDATES, 'ccusage_cache', return_value=root), \
                 patch.object(UPDATES.shutil, 'which', side_effect=AssertionError('package manager queried')):
                self.assertEqual(command, UPDATES.install_ccusage('20.0.31'))
            self.assertEqual(command.parents[2], (root / 'current').resolve())

    def test_install_uses_exact_version_and_verifies_before_activation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with patch.object(UPDATES, 'ccusage_cache', return_value=root), \
                 patch.object(UPDATES, 'ccusage_version', side_effect=['', '20.0.31']), \
                 patch.object(UPDATES.shutil, 'which', return_value='/fake/npm'), \
                 patch.object(UPDATES.subprocess, 'run', return_value=Mock(returncode=0)) as run:
                UPDATES.install_ccusage('20.0.31')
            self.assertIn('ccusage@20.0.31', run.call_args.args[0])
            self.assertIn('--ignore-scripts', run.call_args.args[0])
            self.assertEqual(root / '20.0.31', (root / 'current').resolve())
            with self.assertRaisesRegex(RuntimeError, 'Invalid'):
                UPDATES.install_ccusage('../../untrusted')


class AppUpdateTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.run(['git', '-C', str(root), *args], check=True, text=True,
                              capture_output=True).stdout.strip()

    def repository(self, root):
        self.git(root, 'init', '-q')
        self.git(root, 'config', 'user.name', 'Update test')
        self.git(root, 'config', 'user.email', 'update-test@example.invalid')
        script = root / 'ai-usage-explorer.sh'
        script.write_text('old\n')
        self.git(root, 'add', script.name)
        self.git(root, 'commit', '-qm', 'old')
        old = self.git(root, 'rev-parse', 'HEAD')
        script.write_text('new\n')
        self.git(root, 'commit', '-qam', 'new')
        new = self.git(root, 'rev-parse', 'HEAD')
        self.git(root, 'checkout', '-q', old)
        return script, old, new

    def test_applies_only_the_reviewed_fast_forward_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, _old, new = self.repository(root)
            TRAY.prepare_updates(script, new, '')
            self.assertEqual(new, self.git(root, 'rev-parse', 'HEAD'))
            self.assertEqual('new\n', script.read_text())

    def test_local_staged_and_unstaged_changes_prevent_any_update(self):
        for staged in (False, True):
            with self.subTest(staged=staged), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                script, old, new = self.repository(root)
                script.write_text('user changes\n')
                if staged:
                    self.git(root, 'add', script.name)
                with self.assertRaisesRegex(RuntimeError, 'local changes'):
                    TRAY.prepare_updates(script, new, '20.0.31')
                self.assertEqual(old, self.git(root, 'rev-parse', 'HEAD'))
                self.assertEqual('user changes\n', script.read_text())

    def test_diverged_checkout_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            script, _old, new = self.repository(root)
            script.write_text('local commit\n')
            self.git(root, 'commit', '-qam', 'local commit')
            local = self.git(root, 'rev-parse', 'HEAD')
            with self.assertRaisesRegex(RuntimeError, 'fast-forward'):
                TRAY.prepare_updates(script, new, '')
            self.assertEqual(local, self.git(root, 'rev-parse', 'HEAD'))


if __name__ == '__main__':
    unittest.main()
