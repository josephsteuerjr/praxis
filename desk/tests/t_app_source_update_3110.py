"""Authored app A/L/B composition using private Git and fake runtime bytes.

These tests grant no canonical verification, review or installation approval.
The old application and incoming coordinator remain inert temporary fixtures.
"""
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import app_source_update as app_update, authored_baseline
from localharness import code_transition, source_merge as merge, source_update as pipeline
from localharness import update_composition as composition
from localharness.package_notice import read, write


FEATURE = 'owner = "factory"\n' + '# unchanged separator\n'*16 + 'release = "first"\n'


class AppSourceUpdate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='app-source-update-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = self.root/'installed/Helene'
        self.new = self.root/'incoming/Helene'
        self.data = self.root/'canonical-data'
        self.data.mkdir()
        self.directory = self.data/'memory/.source-updates'/('a'*64)
        self.app = {
            'python_app.py': b'window = "published"\n',
            'localharness/feature.py': FEATURE.encode(),
            'resources/remove-locally.txt': b'local deletion fixture\n',
            'resources/remove-upstream.txt': b'upstream deletion fixture\n',
            'resources/soul/skills/seed.md': b'Published seed skill.\n',
            'resources/icon.bin': b'\x00base-icon\xff',
            'static/index.html': b'<!doctype html><title>Factory</title>\n',
        }
        for program in (self.old, self.new):self.initial_program(program)
        code_transition.seal(self.old)
        authored_baseline.seal(self.old)
        self.git = merge.executable()
        real_git = merge.git
        def private_git(repo, *args, **kwargs):
            if not Path(repo).resolve().is_relative_to(self.root.resolve()):
                raise AssertionError('Git escaped the private test fixture')
            return real_git(repo, *args, **kwargs)
        self.enterContext(patch.object(merge, 'git', side_effect=private_git))

    def put(self, program, name, content):
        path = Path(program)/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def initial_program(self, program):
        self.put(program, 'tree/agent.py', 'CORE = "pristine"\n')
        for name, content in self.app.items():self.put(program, 'app/'+name, content)
        self.put(program, 'runtime/python.exe', b'inert runtime bytes, never execute')
        write(program/'python-app.json', {'format': 'helene-python-app.v1', 'version': 'fixture'})

    def seal_incoming(self, program):
        code_transition.seal(program)
        authored_baseline.seal(program)
        app = composition.app_inventory(program/'app')
        write(program/'source-inputs.json', {'files': {n: r['sha256'] for n, r in app.items()}})
        package = {p.relative_to(program).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in program.rglob('*') if p.is_file()}
        write(program/'python-package-files.json', {'files': package})

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file()}

    def prepare_pair(self, old=None, new=None, directory=None, *, include_app=True):
        old = old or self.old;new = new or self.new;directory = directory or self.directory
        self.seal_incoming(new)
        base = directory.parent/(directory.name+'-core-A')
        authored_baseline.extract(old, 'tree', base)
        plan = merge.create(base, old/'tree', new/'tree', directory, exe=self.git)
        if include_app:
            plan['app_source'] = app_update.binding(old, new)
            write(directory/'plan.json', plan)
        operation = {'id': directory.name, 'directory': str(directory), 'program': str(old),
            'tree': str(self.data), 'source': str(old/'tree'),
            'input_sha256': merge.input_signature(plan), 'state': 'waiting_agent',
            'resolution_owner': 'incoming', 'attempts': [], 'created': time.time(), 'version': 'fixture',
            'coordinator': {'kind': 'incoming-update-runtime.v1', 'program': str(new), 'test_battery': 1}}
        write(directory/'pipeline.json', operation)
        write(new/'code-transition-plan.json', {'plans': [{'source': str(old/'tree'), 'directory': str(directory)}]})
        composition.prepare(directory, operation)
        return plan, operation

    def owner_edit(self):
        self.put(self.old, 'app/localharness/feature.py', FEATURE.replace('owner = "factory"', 'owner = "authored"'))

    def test_app_only_disjoint_changes_merge_without_touching_old_or_incoming(self):
        self.owner_edit()
        incoming = FEATURE.replace('release = "first"', 'release = "second"')
        self.put(self.new, 'app/localharness/feature.py', incoming)
        old_before = self.snapshot(self.old)
        self.prepare_pair()
        expected = incoming.replace('owner = "factory"', 'owner = "authored"').encode()
        candidate = self.directory/'compound/app/localharness/feature.py'
        self.assertEqual(candidate.read_bytes(), expected)
        self.assertEqual((self.new/'app/localharness/feature.py').read_bytes(), incoming.encode())
        self.assertEqual(self.snapshot(self.old), old_before)
        app_update.finish(self.directory)
        app_update.require_ready(self.directory)
        self.assertEqual(read(self.directory/'app-merge/plan.json')['conflicts'], [])

    def test_app_overlap_blocks_until_an_explicit_recorded_text_resolution(self):
        self.owner_edit()
        self.put(self.new, 'app/localharness/feature.py', FEATURE.replace('owner = "factory"', 'owner = "upstream"'))
        old_before = self.snapshot(self.old)
        self.prepare_pair()
        plan = read(self.directory/'app-merge/plan.json')
        self.assertIn('localharness/feature.py', plan['conflicts'])
        with self.assertRaises(ValueError):app_update.require_ready(self.directory)
        with self.assertRaises(ValueError):app_update.finish(self.directory)
        resolved = FEATURE.replace('owner = "factory"', 'owner = "reconciled"')
        composition.repair(self.directory, 'app/localharness/feature.py', resolved,
            expected=composition.signature(self.directory), reason='Reconcile both authored and upstream changes')
        app_update.finish(self.directory)
        app_update.require_ready(self.directory)
        self.assertEqual((self.directory/'compound/app/localharness/feature.py').read_text(), resolved)
        self.assertIn('localharness/feature.py', read(self.directory/'app-merge/plan.json')['resolved'])
        self.assertTrue(read(self.directory/'composition.json')['repairs'])
        self.assertEqual(self.snapshot(self.old), old_before)

    def test_app_additions_deletions_and_seed_soul_are_in_the_real_application_merge(self):
        self.put(self.old, 'app/localharness/authored.py', 'OWNER_ADDED = True\n')
        self.put(self.old, 'app/resources/soul/skills/authored.md', b'Owner-authored published seed extension.\r\n')
        self.put(self.old, 'app/resources/soul/skills/seed.md', b'Owner revised this seed.\n')
        (self.old/'app/resources/remove-locally.txt').unlink()
        (self.new/'app/resources/remove-upstream.txt').unlink()
        self.put(self.new, 'app/deskd/incoming.py', 'UPSTREAM_ADDED = True\n')
        old_before = self.snapshot(self.old)
        self.prepare_pair()
        app_update.finish(self.directory);app_update.require_ready(self.directory)
        result = self.directory/'compound/app'
        self.assertEqual((result/'localharness/authored.py').read_bytes(), b'OWNER_ADDED = True\n')
        self.assertEqual((result/'deskd/incoming.py').read_bytes(), b'UPSTREAM_ADDED = True\n')
        self.assertEqual((result/'resources/soul/skills/authored.md').read_bytes(), b'Owner-authored published seed extension.\r\n')
        self.assertEqual((result/'resources/soul/skills/seed.md').read_bytes(), b'Owner revised this seed.\n')
        self.assertFalse((result/'resources/remove-locally.txt').exists())
        self.assertFalse((result/'resources/remove-upstream.txt').exists())
        self.assertIn('resources/soul/skills/authored.md', read(self.directory/'app-merge/plan.json')['files']['local'])
        self.assertEqual(self.snapshot(self.old), old_before)

    def test_binary_conflict_chooses_exact_frozen_side_bytes(self):
        versions = {'base': self.app['resources/icon.bin'],
                    'local': b'\x00owner-icon\xfe', 'incoming': b'\x00upstream-icon\xfd'}
        self.put(self.old, 'app/resources/icon.bin', versions['local'])
        self.put(self.new, 'app/resources/icon.bin', versions['incoming'])
        old_before = self.snapshot(self.old)
        self.prepare_pair()
        self.assertIn('resources/icon.bin', read(self.directory/'app-merge/plan.json')['conflicts'])
        with self.assertRaises(ValueError):app_update.require_ready(self.directory)
        for side in ('local', 'incoming', 'base'):
            with self.subTest(side=side):
                app_update.resolve_side(self.directory, 'resources/icon.bin', side,
                    expected=composition.signature(self.directory), reason='Choose exact '+side+' binary resource')
                app_update.finish(self.directory);app_update.require_ready(self.directory)
                self.assertEqual((self.directory/'compound/app/resources/icon.bin').read_bytes(), versions[side])
        self.assertEqual(self.snapshot(self.old), old_before)

    def test_side_resolution_needs_current_composition_reason_and_declared_path(self):
        self.owner_edit();self.prepare_pair()
        before = self.snapshot(self.directory/'compound/app')
        for name, side, expected, reason in (
            ('localharness/feature.py', 'local', '0'*64, 'stale composition'),
            ('localharness/feature.py', 'local', None, ''),
            ('../outside.py', 'local', None, 'unsafe path'),
            ('not-a-declared-file.bin', 'local', None, 'undeclared source'),
            ('localharness/feature.py', 'other', None, 'unknown side'),
        ):
            with self.subTest(name=name, side=side, reason=reason), self.assertRaises(ValueError):
                app_update.resolve_side(self.directory, name, side,
                    expected=expected or composition.signature(self.directory), reason=reason)
        self.assertEqual(self.snapshot(self.directory/'compound/app'), before)

    def test_pristine_core_with_authored_app_still_requires_the_strict_gate(self):
        self.owner_edit();plan, _operation = self.prepare_pair()
        self.assertEqual(plan['files']['local'], plan['files']['base'])
        self.assertFalse(plan['conflicts'])
        self.assertNotIn('shared_app_required', plan)
        self.assertTrue(pipeline.requires_gate(plan, self.directory))

    def test_finished_app_does_not_fabricate_full_verification_or_independent_review(self):
        self.owner_edit();self.prepare_pair()
        app_update.finish(self.directory);app_update.require_ready(self.directory)
        plan = read(self.directory/'plan.json')
        self.assertTrue(pipeline.requires_gate(plan, self.directory))
        target = self.root/'unapproved-target'
        with patch.object(pipeline, 'validate_ready', side_effect=ValueError('Canonical full review deliberately not provided')) as review:
            with self.assertRaisesRegex(ValueError, 'deliberately not provided'):
                merge.apply_upstream(self.directory, target)
        review.assert_called_once_with(self.directory)
        self.assertFalse(target.exists())

    def test_changed_only_reports_actual_owner_delta_not_incoming_upgrade(self):
        self.put(self.new, 'app/localharness/feature.py', FEATURE.replace('release = "first"', 'release = "second"'))
        self.seal_incoming(self.new)
        self.assertIsNone(app_update.changed(self.old, self.new))
        self.owner_edit()
        changed = app_update.changed(self.old, self.new)
        self.assertEqual(changed, app_update.binding(self.old, self.new))
        self.assertNotEqual(changed['local'], changed['base'])

    def test_stale_old_app_after_binding_refuses_seeding(self):
        self.owner_edit()
        self.seal_incoming(self.new)
        selected = app_update.binding(self.old, self.new)
        self.put(self.old, 'app/localharness/later.py', 'LATER_OWNER_CHANGE = True\n')
        base = self.root/'core-A';authored_baseline.extract(self.old, 'tree', base)
        plan = merge.create(base, self.old/'tree', self.new/'tree', self.directory, exe=self.git)
        plan['app_source'] = selected;write(self.directory/'plan.json', plan)
        operation = {'input_sha256': merge.input_signature(plan),
                     'coordinator': {'kind': 'incoming-update-runtime.v1', 'program': str(self.new)}}
        with self.assertRaises(ValueError):composition.prepare(self.directory, operation)
        self.assertEqual((self.old/'app/localharness/later.py').read_bytes(), b'LATER_OWNER_CHANGE = True\n')

    def test_old_app_mutation_after_preparation_invalidates_composition(self):
        self.owner_edit();self.prepare_pair()
        self.put(self.old, 'app/localharness/later.py', 'LATER = True\n')
        with self.assertRaises(ValueError):composition.signature(self.directory)
        with self.assertRaises(ValueError):app_update.require_ready(self.directory)

    def test_old_selected_core_mutation_is_refused_by_pipeline_freshness(self):
        self.owner_edit();_plan, operation = self.prepare_pair()
        pipeline._fresh(operation, self.directory)
        self.put(self.old, 'tree/agent.py', 'CORE = "changed after review input"\n')
        with self.assertRaises(ValueError):pipeline._fresh(operation, self.directory)

    def test_old_baseline_mutation_after_preparation_invalidates_composition(self):
        self.owner_edit();self.prepare_pair()
        archive = self.old/'app-upstream.zip';archive.write_bytes(archive.read_bytes()+b'tampered')
        with self.assertRaises(ValueError):composition.signature(self.directory)

    def test_incoming_app_or_runtime_mutation_invalidates_composition(self):
        self.owner_edit();self.prepare_pair()
        app = self.new/'app/localharness/feature.py';original = app.read_bytes()
        app.write_bytes(original+b'# changed incoming verifier\n')
        with self.assertRaises(ValueError):composition.signature(self.directory)
        app.write_bytes(original)
        self.put(self.new, 'runtime/python.exe', b'changed inert runtime')
        with self.assertRaises(ValueError):composition.signature(self.directory)

    def test_frozen_local_app_side_and_finished_result_cannot_change_unrecorded(self):
        self.owner_edit();self.prepare_pair()
        app_update.finish(self.directory);app_update.require_ready(self.directory)
        frozen = self.directory/'app-merge/local/localharness/feature.py';original = frozen.read_bytes()
        frozen.write_bytes(original+b'# changed frozen L\n')
        with self.assertRaises(ValueError):composition.signature(self.directory)
        frozen.write_bytes(original)
        self.put(self.directory, 'compound/app/localharness/unrecorded.py', 'UNRECORDED = True\n')
        with self.assertRaises(ValueError):app_update.require_ready(self.directory)
        with self.assertRaises(ValueError):app_update.finish(self.directory)

    def test_second_update_uses_pristine_b_as_a_instead_of_previous_merged_app(self):
        self.owner_edit()
        second = FEATURE.replace('release = "first"', 'release = "second"')
        self.put(self.new, 'app/localharness/feature.py', second)
        self.prepare_pair();app_update.finish(self.directory);app_update.require_ready(self.directory)
        pristine_zip = (self.new/'app-upstream.zip').read_bytes()
        candidate = self.snapshot(self.directory/'compound/app')
        # Simulate the installed M source surface only, with no apply approval.
        shutil.rmtree(self.new/'app')
        for name, content in candidate.items():self.put(self.new, 'app/'+name, content)
        authored_baseline.seal(self.new)
        third = self.root/'third-incoming/Helene';self.initial_program(third)
        self.put(third, 'app/localharness/feature.py', FEATURE.replace('release = "first"', 'release = "third"'))
        directory = self.data/'memory/.source-updates'/('b'*64)
        self.prepare_pair(self.new, third, directory)
        app_update.finish(directory);app_update.require_ready(directory)
        expected = FEATURE.replace('owner = "factory"', 'owner = "authored"').replace('release = "first"', 'release = "third"')
        self.assertEqual((directory/'compound/app/localharness/feature.py').read_bytes(), expected.encode())
        self.assertEqual((self.new/'app-upstream.zip').read_bytes(), pristine_zip)
        baseline_files = authored_baseline.validate(self.new)['app']['files']
        frozen_a = read(directory/'app-merge/plan.json')['files']['base']
        self.assertEqual(frozen_a, baseline_files)
        self.assertNotEqual(frozen_a, composition.app_inventory(self.new/'app'))


if __name__=='__main__':unittest.main()
