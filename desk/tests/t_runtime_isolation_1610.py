"""The Windows embedded runtime cannot borrow build-host user packages."""
from __future__ import annotations

import os
import hashlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
import zipfile
from unittest import mock

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK / 'installer'))
import build_dist


class EmbeddedRuntimeIsolation(unittest.TestCase):
    def test_embedded_python_finds_the_shipped_tree_for_forge_workers(self):
        # CPython's Windows embeddable _pth overrides cwd, script directory and
        # PYTHONPATH. Forge's child process imports siblings from tree/.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            cache = root / 'cache'
            cache.mkdir()
            archive = cache / f'python-{build_dist.PY_VERSION}-embed-amd64.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('python314._pth', 'python314.zip\n.\n#import site\n')
                z.writestr('python.exe', b'placeholder')
            (cache / 'get-pip.py').write_text('', encoding='ascii')
            with mock.patch.object(build_dist, 'fetch'), mock.patch.object(build_dist, '_run_timed'):
                build_dist.build_runtime(root / 'Helene', cache)
            pth = (root / 'Helene' / 'runtime' / 'python314._pth').read_text(encoding='utf8')
            digest = build_dist.runtime_path_config_digest(root / 'Helene')
        self.assertIn('import site', pth.splitlines())
        self.assertIn('..\\tree', pth.splitlines())
        self.assertEqual(pth.splitlines().count('..\\tree'), 1)
        self.assertEqual(digest, hashlib.sha256(pth.encode('utf-8')).hexdigest())

    def test_worker_import_smoke_uses_bundled_python_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'runtime').mkdir()
            (root / 'tree').mkdir()
            (root / 'runtime' / 'python.exe').write_bytes(b'launcher')
            for name in ('forge.py', 'forge_worker.py'):
                (root / 'tree' / name).write_text('', encoding='ascii')
            result = types.SimpleNamespace(returncode=1, stderr='ModuleNotFoundError: forge')
            with mock.patch.object(build_dist.subprocess, 'run', return_value=result) as run:
                with self.assertRaisesRegex(SystemExit, 'ModuleNotFoundError: forge'):
                    build_dist.smoke_embedded_forge_worker(root)
            argv = run.call_args.args[0]
            self.assertEqual(argv, [str(root / 'runtime' / 'python.exe'), '-c', 'import forge_worker'])
            self.assertEqual(run.call_args.kwargs['cwd'], root / 'tree')
            self.assertEqual(run.call_args.kwargs['env']['PYTHONNOUSERSITE'], '1')

    def test_pip_and_import_smokes_ignore_ambient_user_site(self):
        seen = []

        def run(argv, **options):
            seen.append((argv, options))
            return types.SimpleNamespace(returncode=0, stdout='voice decode: ok\n', stderr='')

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            runtime = root / 'runtime'
            runtime.mkdir()
            (runtime / 'python.exe').write_bytes(b'fake launcher')
            stage = root / 'voice' / 'site-packages'
            with mock.patch.dict(os.environ, {'PYTHONNOUSERSITE': '0', 'PYTHONUSERBASE': str(root / 'foreign')}):
                with mock.patch.object(build_dist.subprocess, 'run', side_effect=run):
                    build_dist._run_timed([str(runtime / 'python.exe'), '-m', 'pip', 'install', 'PyYAML'],
                                          check=True, timeout=20)
                    build_dist.smoke_runtime(root, imports=('yaml',))
                    build_dist.smoke_voice(root, stage)
                self.assertEqual(os.environ['PYTHONNOUSERSITE'], '0', 'do not change the operator environment')

        self.assertEqual(len(seen), 4)
        self.assertEqual(seen[0][0][-1], 'PyYAML')
        self.assertEqual(seen[2][0][2:], ['pip', 'freeze'])
        for _argv, options in seen:
            self.assertEqual(options['env']['PYTHONNOUSERSITE'], '1')
            self.assertTrue(options['env']['PYTHONUSERBASE'].endswith('foreign'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
