"""The actual 2.1.1 preflight cannot run incoming code; the bridge prepares it."""
from pathlib import Path
import importlib.util
import shutil
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
import t_source_update_pipeline_220 as fixtures
from localharness import source_merge, source_update, source_update_host
from localharness.package_notice import read
import python_update_prepare


def legacy_modules():
    root = Path(__file__).parent / 'fixtures/update211'
    package = types.ModuleType('legacy211')
    package.__path__ = [str(Path(fixtures.__file__).resolve().parents[1] / 'localharness')]
    modules = {'legacy211': package}
    # Dependencies use today's pure discovery/I/O; the old merge/planner/button
    # functions are loaded verbatim from the pinned public 2.1.1 blobs.
    with mock.patch.dict(sys.modules, modules):
        for name in ('source_merge', 'code_transition', 'python_updates'):
            spec = importlib.util.spec_from_file_location('legacy211.' + name, root / (name + '.py'))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module; modules[spec.name] = module
            spec.loader.exec_module(module)
            setattr(package, name, module)
    return modules


class LegacyPreparation(unittest.TestCase):
    def setUp(self):
        f = fixtures.PipelineTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups); self.f = f

    def test_actual_old_button_returns_before_launching_incoming_helper(self):
        f = self.f; modules = legacy_modules(); old = modules['legacy211.python_updates']
        archive = f.old.parent / 'update.zip'; archive.write_bytes(b'fixture')
        source = f.new
        (source / 'app').mkdir(); (source / 'app/python_app.py').write_text('# fixture')
        interpreter = source / ('runtime/python.exe' if sys.platform == 'win32' else 'runtime/bin/python3')
        interpreter.parent.mkdir(parents=True); interpreter.write_bytes(b'fixture')
        owner = types.SimpleNamespace(root=f.old, event=lambda *args: None)
        def extract(path, destination, *args): shutil.copytree(source, destination / 'Helene')
        with mock.patch.dict(sys.modules, modules), mock.patch.object(old, '_extract', side_effect=extract), \
             mock.patch('legacy211.python_platform.require_runtime'), mock.patch('legacy211.update_extensions.check'), \
             mock.patch.object(old.subprocess, 'Popen') as child:
            result = old._update_install(owner, archive)
        self.assertEqual(result['state'], 'needs_resolution')
        child.assert_not_called()
        self.assertFalse(list(f.old.parent.glob('.helene-update-*')))

    def test_bridge_uses_incoming_review_then_old_preflight_accepts_same_candidate(self):
        f = self.f; modules = legacy_modules()
        original_code = source_merge.inventory(f.code)
        original_config = (f.old / 'helene.json').read_bytes()
        def review(old, new, plans, progress, cancelled):
            f.tick(); f.merge_result(); f.tick(); f.review_result(); f.tick()
        with mock.patch.object(source_update_host, 'verified_runtime'), \
             mock.patch.object(source_update_host, 'review', side_effect=review), \
             mock.patch.object(python_update_prepare, '__name__', 'fixture'):
            result = python_update_prepare.prepare_existing(f.old, f.new)
        self.assertEqual(result['state'], 'ready')
        with mock.patch.dict(sys.modules, modules):
            accepted = modules['legacy211.code_transition'].preflight(f.old, f.new)
        self.assertEqual(accepted['state'], 'ready')
        self.assertEqual(source_merge.inventory(f.code), original_code)
        self.assertEqual((f.old / 'helene.json').read_bytes(), original_config)
        self.assertEqual(read(f.old / 'python-app.json')['version'], '2.1.1')
        source_update.validate_ready(f.directory)

    def test_bridge_refuses_nested_installations_and_changed_owner_selection(self):
        f = self.f
        with self.assertRaises(ValueError): python_update_prepare.prepare_existing(f.old, f.old / 'incoming')
        with mock.patch.object(source_update_host, 'verified_runtime'), \
             mock.patch.object(source_update_host, 'review', side_effect=lambda *args: (f.old / 'helene.json').write_text('{}')):
            with self.assertRaises(ValueError): python_update_prepare.prepare_existing(f.old, f.new)

    def test_bridge_rejects_same_or_older_release_and_platform_mismatch(self):
        from localharness.package_notice import write
        f = self.f
        for version in ('2.1.1', '2.0.0'):
            write(f.new / 'python-app.json', {'version': version})
            with self.subTest(version=version), self.assertRaisesRegex(ValueError, 'newer stable'):
                python_update_prepare.prepare_existing(f.old, f.new)
        write(f.new / 'python-app.json', {'version': '2.2.5', 'target': 'linux-x86_64'})
        with self.assertRaisesRegex(ValueError, 'different platforms'):
            python_update_prepare.prepare_existing(f.old, f.new)


if __name__ == '__main__': unittest.main()
