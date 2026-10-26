"""Real Git and disposable programs: merge, repeat update, conflicts and rollback."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import source_merge as m, code_transition as c, source_update
from localharness import python_source_updater as updater
from localharness.package_notice import read, write

BASE = 'title = "original"\n\ndef first():\n    return 1\n\n\ndef second():\n    return 2\n'
LOCAL = BASE.replace('return 1', 'return 11')
NEW = BASE.replace('return 2', 'return 22')
BOTH = LOCAL.replace('return 2', 'return 22')


def put(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8', newline='')
    return path


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base, self.local, self.new = [self.root / n for n in ('base', 'local', 'new')]
        for path, text in ((self.base, BASE), (self.local, LOCAL), (self.new, NEW)):
            put(path, 'module.py', text)
        self.plan = self.root / 'plan'

    def create(self):
        return m.create(self.base, self.local, self.new, self.plan)

    def test_same_file_disjoint_edits_merge_with_real_two_parent_commit(self):
        plan = self.create()
        self.assertEqual(plan['state'], 'ready')
        self.assertEqual((self.plan / 'result/module.py').read_text(), BOTH)
        parents = m.git(self.plan / 'repository.git', 'show', '-s', '--format=%P', plan['merged_commit']).stdout.decode().split()
        self.assertEqual(parents, [plan['commits']['local'], plan['commits']['incoming']])
        self.assertEqual((self.local / 'module.py').read_text(), LOCAL)

    def test_overlap_requires_resolution_and_cannot_change_unrelated_file(self):
        put(self.new, 'module.py', BASE.replace('return 1', 'return 99'))
        for root in (self.base, self.local, self.new):
            put(root, 'unchanged.py', 'constant = 1\n')
        plan = self.create()
        self.assertEqual(plan['conflicts'], ['module.py'])
        with self.assertRaisesRegex(ValueError, 'every source conflict'):
            m.finish(self.plan)
        m.resolve(self.plan, 'module.py', LOCAL.replace('return 11', 'return 110'))
        put(self.plan / 'result', 'unchanged.py', 'constant = 2\n')
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            m.finish(self.plan)
        put(self.plan / 'result', 'unchanged.py', 'constant = 1\n')
        self.assertEqual(m.finish(self.plan)['state'], 'ready')

    def test_modify_delete_is_explicit_not_upstream_wins(self):
        (self.new / 'module.py').unlink()
        plan = self.create()
        self.assertEqual(plan['state'], 'conflicts')
        m.resolve(self.plan, 'module.py', delete=True)
        m.finish(self.plan)
        self.assertFalse((self.plan / 'result/module.py').exists())

    def test_rename_carries_local_edit_and_new_files(self):
        put(self.new, 'module.py', BASE)
        (self.new / 'module.py').rename(self.new / 'renamed.py')
        put(self.local, 'authored.py', 'mine = True\n')
        put(self.new, 'upstream.py', 'new = True\n')
        self.create()
        self.assertEqual((self.plan / 'result/renamed.py').read_text(), LOCAL)
        self.assertTrue((self.plan / 'result/authored.py').is_file())
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        m.apply(self.plan, target)
        self.assertFalse((target / 'module.py').exists())
        self.assertTrue((target / 'upstream.py').is_file())

    def test_late_edit_and_tampered_result_never_apply(self):
        self.create()
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        put(target, 'late.py', 'late = True\n')
        with self.assertRaisesRegex(ValueError, 'changed during'):
            m.apply(self.plan, target)
        self.assertEqual((target / 'module.py').read_text(), LOCAL)
        with self.assertRaisesRegex(ValueError, 'running source'):
            m.apply(self.plan, self.local)
        put(self.plan / 'result', 'module.py', 'tampered = True\n')
        with self.assertRaisesRegex(ValueError, 'result changed'):
            m.apply(self.plan, target)

    def test_memory_secrets_and_models_are_not_git_inputs(self):
        for name in ('memory/chat.json', 'models/weights.bin', '.env', 'auth.json', '.git/config'):
            put(self.local, name, 'PRIVATE')
        self.create()
        self.assertEqual(set(m.inventory(self.plan / 'local')), {'module.py'})

    def test_broken_python_and_unresolved_markers_are_rejected(self):
        put(self.new, 'module.py', 'def broken(:\n')
        self.create()
        m.resolve(self.plan, 'module.py', '<<<<<<< mine\ninvalid\n')
        with self.assertRaisesRegex(ValueError, 'markers'):
            m.finish(self.plan)
        m.resolve(self.plan, 'module.py', 'def broken(:\n')
        with self.assertRaises(SyntaxError):
            m.finish(self.plan)

    def test_binary_conflict_requires_an_explicit_side(self):
        for root, content in ((self.base, b'\x00base'), (self.local, b'\x00local'), (self.new, b'\x00incoming')):
            (root / 'image.bin').write_bytes(content)
        plan = self.create()
        self.assertIn('image.bin', plan['conflicts'])
        m.resolve(self.plan, 'image.bin', side='local')
        m.finish(self.plan)
        self.assertEqual((self.plan / 'result/image.bin').read_bytes(), b'\x00local')

    def test_deleting_last_file(self):
        put(self.local, 'module.py', BASE)
        (self.new / 'module.py').unlink()
        self.create()
        self.assertEqual(m.inventory(self.plan / 'result'), {})
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        m.apply(self.plan, target)
        self.assertEqual(m.inventory(target), {})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old, self.new = self.root / 'Helene', self.root / 'incoming'
        self.code, self.data = self.root / 'authored', self.root / 'profile'
        put(self.old / 'tree', 'module.py', BASE)
        put(self.new / 'tree', 'module.py', NEW)
        put(self.code, 'module.py', LOCAL)
        put(self.data, 'memory/chat.json', '{"remember":"owner"}')
        put(self.data, 'models/large.bin', 'model')
        write(self.old / 'python-app.json', {'version': '2.0.0', 'delivery': 'portable'})
        write(self.new / 'python-app.json', {'version': '2.1.0', 'delivery': 'portable'})
        write(self.old / 'helene.json', {'tree': str(self.data), 'code': str(self.code), 'runner': '',
                                        'self_authored': {'value': 123}})

    def test_two_updates_keep_clean_upstream_base_and_profile_then_rollback(self):
        original = (self.old / 'helene.json').read_bytes()
        note, recovery = updater.install(self.old, self.new)
        chosen = Path(read(self.old / 'helene.json')['code'])
        self.assertEqual((chosen / 'module.py').read_text(), BOTH)
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)
        self.assertEqual(read(self.old / 'helene.json')['tree'], str(self.data))
        self.assertEqual(note['copied_data'], [])
        self.assertFalse(list((self.old / 'code').rglob('large.bin')))
        updater.active_update(self.old).unlink()  # disposable successful-start receipt
        put(chosen, 'module.py', BOTH.replace('return 11', 'return 111'))
        second = self.root / 'incoming-22'
        put(second / 'tree', 'module.py', NEW.replace('title = "original"', 'title = "new release"'))
        write(second / 'python-app.json', {'version': '2.2.0', 'delivery': 'portable'})
        before_second = (self.old / 'helene.json').read_bytes()
        note2, recovery2 = updater.install(self.old, second)
        latest = Path(read(self.old / 'helene.json')['code'])
        expected = BOTH.replace('return 11', 'return 111').replace('title = "original"', 'title = "new release"')
        self.assertEqual((latest / 'module.py').read_text(), expected)
        updater.rollback(self.old, recovery2)
        self.assertEqual((self.old / 'helene.json').read_bytes(), before_second)
        self.assertEqual((chosen / 'module.py').read_text(), BOTH.replace('return 11', 'return 111'))
        self.assertEqual((self.data / 'memory/chat.json').read_text(), '{"remember":"owner"}')
        self.assertNotEqual(original, before_second)

    def test_conflict_agent_tool_and_retry_preserve_working_program(self):
        put(self.new / 'tree', 'module.py', BASE.replace('return 1', 'return 99'))
        before = (self.old / 'helene.json').read_bytes()
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'needs_resolution')
        sides = source_update.command(self.data, 'read', path='module.py')
        self.assertEqual(sides['local']['text'], LOCAL)
        source_update.command(self.data, 'resolve', path='module.py', content=BOTH)
        self.assertEqual(source_update.command(self.data, 'finish')['state'], 'ready')
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'ready')
        self.assertEqual((self.old / 'helene.json').read_bytes(), before)
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)

    def test_baseline_is_contents_and_is_never_customized_merge(self):
        c.seal(self.old)
        put(self.old / 'tree', 'module.py', 'later accidental seed edit\n')
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'ready')
        archive = self.old / 'code-upstream.zip'
        archive.write_bytes(archive.read_bytes() + b'tampered')
        with self.assertRaisesRegex(ValueError, 'archive changed'):
            c.preflight(self.old, self.new)

    def test_existing_git_dirty_and_untracked_code_survive_update(self):
        subprocess.run(['git', 'init', '-q', str(self.code)], check=True)
        subprocess.run(['git', '-C', str(self.code), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.code), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@invalid.local', '-c', 'gc.auto=0',
                        'commit', '-qm', 'local history'], check=True)
        head = subprocess.check_output(['git', '-C', str(self.code), 'rev-parse', 'HEAD'])
        put(self.code, 'draft.py', 'unfinished_but_valid = True\n')
        note, recovery = updater.install(self.old, self.new)
        chosen = Path(read(self.old / 'helene.json')['code'])
        self.assertEqual(subprocess.check_output(['git', '-C', str(chosen), 'rev-parse', 'HEAD']), head)
        self.assertEqual((chosen / 'draft.py').read_bytes(), (self.code / 'draft.py').read_bytes())
        self.assertEqual((chosen / 'module.py').read_text(), BOTH)
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)

    def test_conflict_return_releases_desktop_guard_without_exit(self):
        from localharness import python_updates
        owner = SimpleNamespace(root=self.old)
        with patch.object(python_updates, '_update_install', return_value={'state': 'needs_resolution'}):
            for _ in range(2):
                self.assertEqual(python_updates.update_install(owner, 'unused')['state'], 'needs_resolution')

    def test_live_edit_invalidates_agent_resolution_request(self):
        put(self.new / 'tree', 'module.py', BASE.replace('return 1', 'return 99'))
        c.preflight(self.old, self.new)
        put(self.code, 'module.py', LOCAL + '\nnew_work = 123\n')
        with self.assertRaisesRegex(ValueError, 'Live source changed'):
            source_update.command(self.data, 'resolve', path='module.py', content=BOTH)
        self.assertEqual(read(source_update.path(self.data))['state'], 'stale')


if __name__ == '__main__':
    unittest.main(verbosity=2)
