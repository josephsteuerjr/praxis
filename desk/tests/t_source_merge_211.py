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


def seal_delivery(program):
    """Construct an incoming test delivery before admission, never rebase M."""
    from localharness import authored_baseline
    program=Path(program)
    for name in ('source-update-baseline.json','app-upstream.json','app-upstream.zip'):
        (program/name).unlink(missing_ok=True)
    meta=read(program/'python-app.json');meta.pop('source_update_protocol',None);meta.pop('source_update_baseline_sha256',None)
    write(program/'python-app.json',meta)
    c.seal(program);authored_baseline.seal(program)


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

    def apply_reviewed_files(self,target):
        # This boundary unit tests filesystem safety after a review. Canonical
        # review, stale composition and full gate receipts are tested separately.
        with patch.object(source_update,'validate_ready',return_value={'state':'unit-fixture-clear'}):
            return m.apply(self.plan,target)

    def test_missing_canonical_review_never_changes_the_target(self):
        self.create();target=self.root/'target';shutil.copytree(self.local,target)
        before=m.inventory(target)
        with self.assertRaisesRegex(ValueError,'canonical independent review'):m.apply(self.plan,target)
        self.assertEqual(m.inventory(target),before)

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
        self.apply_reviewed_files(target)
        self.assertFalse((target / 'module.py').exists())
        self.assertTrue((target / 'upstream.py').is_file())

    def test_late_edit_and_tampered_result_never_apply(self):
        self.create()
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        put(target, 'late.py', 'late = True\n')
        with self.assertRaisesRegex(ValueError, 'changed during'):
            self.apply_reviewed_files(target)
        self.assertEqual((target / 'module.py').read_text(), LOCAL)
        with self.assertRaisesRegex(ValueError, 'running source'):
            self.apply_reviewed_files(self.local)
        put(self.plan / 'result', 'module.py', 'tampered = True\n')
        with self.assertRaisesRegex(ValueError, 'result changed'):
            self.apply_reviewed_files(target)

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

    def test_nested_directory_replaced_by_file_prunes_only_empty_ancestors(self):
        for root in (self.base, self.local, self.new):
            put(root, 'pkg/nested/mod.py', 'OLD = 1\n')
        (self.new / 'pkg/nested/mod.py').unlink()
        (self.new / 'pkg/nested').rmdir()
        (self.new / 'pkg').rmdir()
        put(self.new, 'pkg', 'replacement file\n')
        self.create()
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        merge_target = self.root / 'target-with-unplanned-data'
        shutil.copytree(self.local, merge_target)
        put(merge_target, 'pkg/memory/keep.txt', 'unplanned data')
        with self.assertRaises(OSError):
            self.apply_reviewed_files(merge_target)
        self.assertEqual((merge_target / 'pkg/memory/keep.txt').read_text(), 'unplanned data')
        self.apply_reviewed_files(target)
        self.assertEqual((target / 'pkg').read_text(), 'replacement file\n')
        self.assertTrue((self.local / 'pkg/nested/mod.py').exists())

    def test_deleting_last_file(self):
        put(self.local, 'module.py', BASE)
        (self.new / 'module.py').unlink()
        self.create()
        self.assertEqual(m.inventory(self.plan / 'result'), {})
        target = self.root / 'target'
        shutil.copytree(self.local, target)
        self.apply_reviewed_files(target)
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
        for program in (self.old,self.new):
            put(program/'app','fixture.py','app=True\n');seal_delivery(program)

    def test_repeated_unverified_updates_cannot_change_config_code_or_data(self):
        settings=(self.old/'helene.json').read_bytes();code=(self.code/'module.py').read_bytes()
        history=(self.data/'memory/chat.json').read_bytes()
        for _ in range(2):
            with self.assertRaisesRegex(ValueError,'Verified incoming source snapshot|Incoming distribution has no complete update runtime'):
                updater.install(self.old,self.new)
        self.assertEqual((self.old/'helene.json').read_bytes(),settings)
        self.assertEqual((self.code/'module.py').read_bytes(),code)
        self.assertEqual((self.data/'memory/chat.json').read_bytes(),history)
        self.assertFalse(updater.active_update(self.old).exists())


    def test_conflict_cannot_bypass_mandatory_panel_with_agent_tool(self):
        put(self.new / 'tree', 'module.py', BASE.replace('return 1', 'return 99'))
        seal_delivery(self.new)
        before = (self.old / 'helene.json').read_bytes()
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'needs_resolution')
        with self.assertRaisesRegex(ValueError, 'restricted'):
            source_update.command(self.data, 'resolve', path='module.py', content=BOTH)
        request = read(source_update.path(self.data))
        m.resolve(request['directory'], 'module.py', BOTH)
        m.finish(request['directory'])
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'needs_resolution')
        self.assertEqual((self.old / 'helene.json').read_bytes(), before)
        self.assertEqual((self.code / 'module.py').read_text(), LOCAL)

    def test_baseline_is_contents_and_is_never_customized_merge(self):
        c.seal(self.old)
        put(self.old / 'tree', 'module.py', 'later accidental seed edit\n')
        self.assertEqual(c.preflight(self.old, self.new)['state'], 'needs_resolution')
        archive = self.old / 'code-upstream.zip'
        archive.write_bytes(archive.read_bytes() + b'tampered')
        with self.assertRaisesRegex(ValueError, 'archive changed|baseline identity changed'):
            c.preflight(self.old, self.new)

    def test_unverified_delivery_cannot_change_dirty_git_or_untracked_code(self):
        subprocess.run(['git','init','-q',str(self.code)],check=True)
        subprocess.run(['git','-C',str(self.code),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.code),'-c','user.name=Fixture','-c','user.email=fixture@invalid.local',
                        '-c','commit.gpgsign=false','-c','gc.auto=0','commit','-qm','local history'],check=True)
        head=subprocess.check_output(['git','-C',str(self.code),'rev-parse','HEAD'])
        put(self.code,'draft.py','unfinished_but_valid = True\n')
        with self.assertRaisesRegex(ValueError,'Verified incoming source snapshot|Incoming distribution has no complete update runtime'):
            updater.install(self.old,self.new)
        self.assertEqual(subprocess.check_output(['git','-C',str(self.code),'rev-parse','HEAD']),head)
        self.assertEqual((self.code/'draft.py').read_text(),'unfinished_but_valid = True\n')
        self.assertEqual((self.code/'module.py').read_text(),LOCAL)


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
        with self.assertRaisesRegex(ValueError, 'Local code changed'):
            c.validate(self.new)


if __name__ == '__main__':
    unittest.main(verbosity=2)
