"""Native Intel selection, incompatible update rejection and publisher recipe."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]),
               str(Path(__file__).resolve().parents[1] / 'installer')]
from localharness import python_platform as platform
from prepare_python_posix import runtime_recipe


class IntelMacTests(unittest.TestCase):
    def test_fresh_intel_archive_enters_first_start(self):
        import python_app
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'python-app.json').write_text(json.dumps({'target': 'macos-x86_64'}))
            with patch.object(python_app.sys, 'platform', 'darwin'), \
                    patch('localharness.owner_stop.stopped', return_value=False), \
                    patch('localharness.clean_startup.pending', return_value=None), \
                    patch('localharness.posix_first_start.ready', return_value=False), \
                    patch('first_start_window.main', return_value=0) as window:
                self.assertEqual(python_app._main(['--root', str(root)]), 0)
            window.assert_called_once()

    def test_native_target_and_update_asset(self):
        with patch.object(platform.sys, 'platform', 'darwin'), patch.object(platform, '_machine', return_value='x86_64'):
            self.assertEqual(platform.runtime_target(), 'macos-x86_64')
            self.assertEqual(platform.desktop_identity(), {'platform': 'macos', 'arch': 'x86_64'})
            self.assertEqual(platform.asset_name('2.5.2', platform.runtime_target()), 'Helene-2.5.2-macos-x86_64.zip')

    def test_arm_archive_is_rejected_on_intel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(platform.sys, 'platform', 'darwin'), patch.object(platform, '_machine', return_value='x86_64'):
                (root / 'python-app.json').write_text(json.dumps({'target': 'macos-x86_64'}))
                platform.require_runtime(root)
                (root / 'python-app.json').write_text(json.dumps({'target': 'macos-arm64'}))
                with self.assertRaisesRegex(ValueError, 'another OS/architecture'):
                    platform.require_runtime(root)

    def test_intel_archive_is_rejected_on_arm(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'python-app.json').write_text(json.dumps({'target': 'macos-x86_64'}))
            with patch.object(platform.sys, 'platform', 'darwin'), patch.object(platform, '_machine', return_value='arm64'):
                with self.assertRaisesRegex(ValueError, 'another OS/architecture'):
                    platform.require_runtime(root)

    def test_intel_runtime_uses_same_python_and_gui_with_distinct_publisher_bytes(self):
        arm, intel = runtime_recipe('macos-arm64'), runtime_recipe('macos-x86_64')
        self.assertEqual(arm['version'], intel['version'])
        from packaging.requirements import Requirement
        pins = {r.name.lower().replace('_', '-'): next(iter(r.specifier)).version
                for raw in intel['dependencies'] for r in [Requirement(raw)]}
        for raw in arm['dependencies']:
            requirement = Requirement(raw)
            name = requirement.name.lower().replace('_', '-')
            self.assertIn(name, pins)
            self.assertIn(pins[name], requirement.specifier)
        self.assertTrue({'aiogram', 'soundfile', 'numpy', 'pytest'} <= pins.keys())
        self.assertIn('-x86_64-apple-darwin-install_only.tar.gz', intel['url'])
        self.assertNotEqual(arm['sha256'], intel['sha256'])
        self.assertEqual(len(bytes.fromhex(intel['sha256'])), 32)


if __name__ == '__main__':
    unittest.main()
