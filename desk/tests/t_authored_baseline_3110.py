"""Exact authored-source eligibility and immutable A; private files only.

No installed profile, OS registration, process inventory, updater or build is
used. Coherent hostile archives have refreshed outer hash bindings so refusal
must come from validation of the actual archive members.
"""
import copy
import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
import warnings
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import authored_baseline as baseline, code_transition
from localharness.package_notice import read, write


REFUSAL = (ValueError, OSError, KeyError, zipfile.BadZipFile)


class AuthoredBaseline(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='authored-baseline-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.program = self.root/'Programme — Юзер'
        self.original_tree = {'agent.py': b'answer = 1\r\n',
                              'nested/feature.py': b'feature = "upstream"\n'}
        self.original_app = {
            'python_app.py': b'window = "published"\r\n',
            'localharness/feature.py': b'answer = 1\n',
            'resources/soul/seed.md': b'Published seed skill, not private memory.\n',
            'resources/icon.bin': b'\x00\xff\r\n\x80published-icon',
            'static/index.html': b'<!doctype html><title>Published</title>\r\n',
        }
        for surface, files in (('tree', self.original_tree), ('app', self.original_app)):
            for name, content in files.items():self.put(surface+'/'+name, content)
        write(self.program/'python-app.json', {'format': 'helene-python-app.v1',
                                               'version': 'first-normal-release'})
        code_transition.seal(self.program)

    def put(self, name, content):
        path = self.program/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def seal(self):return baseline.seal(self.program)

    def files(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file()}

    def immutable_bytes(self):
        return {name: (self.program/name).read_bytes() for name in (
            'code-upstream.zip', 'code-upstream.json', 'app-upstream.zip',
            'app-upstream.json', 'source-update-baseline.json')}

    def snapshot(self):
        return {**self.immutable_bytes(),
                'python-app.json': (self.program/'python-app.json').read_bytes()}

    def restore(self, snapshot):
        for name, content in snapshot.items():self.put(name, content)

    def refuse_mode(self):
        for request in (None, 'preserve'):
            with self.subTest(request=request), self.assertRaises(REFUSAL):
                baseline.mode(self.program, request)

    def archive_entries(self, surface):
        stem = 'code-upstream' if surface=='tree' else 'app-upstream'
        with zipfile.ZipFile(self.program/(stem+'.zip')) as z:
            return [(copy.copy(info), z.read(info)) for info in z.infolist()]

    def publish_coherent_archive(self, surface, entries, *, files=None):
        """Rebind the archive all the way to python-app, never merely break SHA."""
        stem = 'code-upstream' if surface=='tree' else 'app-upstream'
        buffer = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
                for info, content in entries:z.writestr(info, content)
        archive = self.put(stem+'.zip', buffer.getvalue())
        manifest_path = self.program/(stem+'.json')
        manifest = read(manifest_path)
        manifest['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        if files is not None:manifest['files'] = files
        write(manifest_path, manifest)
        declaration_path = self.program/'source-update-baseline.json'
        declaration = read(declaration_path)
        declaration['surfaces'][surface] = {
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        }
        write(declaration_path, declaration)
        meta = read(self.program/'python-app.json')
        meta['source_update_baseline_sha256'] = hashlib.sha256(declaration_path.read_bytes()).hexdigest()
        write(self.program/'python-app.json', meta)

    def test_protocol_eligibility_requires_complete_exact_two_surface_baseline(self):
        self.assertEqual(baseline.PROTOCOL, 'helene-authored-source.v2')
        self.seal()
        meta = read(self.program/'python-app.json')
        self.assertEqual(meta['source_update_protocol'], baseline.PROTOCOL)
        surfaces = baseline.validate(self.program)
        self.assertEqual(set(surfaces), {'tree', 'app'})
        self.assertEqual(set(surfaces['tree']['files']), set(self.original_tree))
        self.assertEqual(set(surfaces['app']['files']), set(self.original_app))
        self.assertEqual(baseline.mode(self.program), 'preserve')
        self.assertEqual(baseline.mode(self.program, 'preserve'), 'preserve')
        self.assertEqual(baseline.mode(self.program, 'clean'), 'clean')

    def test_version_and_python_label_do_not_make_legacy_preserve_eligible(self):
        for version in ('0.0', '999.999.999', 'first-normal-release'):
            with self.subTest(version=version):
                write(self.program/'python-app.json', {'format': 'helene-python-app.v1',
                                                       'version': version, 'delivery': 'portable'})
                self.assertEqual(baseline.mode(self.program), 'clean')
                self.assertEqual(baseline.mode(self.program, 'clean'), 'clean')
                with self.assertRaises(REFUSAL):baseline.mode(self.program, 'preserve')

    def test_unknown_nonempty_protocol_is_not_silently_treated_as_legacy(self):
        for protocol in ('helene-authored-source.v999', 'claimed-compatible', True):
            with self.subTest(protocol=protocol):
                write(self.program/'python-app.json', {'version': '999', 'source_update_protocol': protocol})
                self.refuse_mode()
                self.assertEqual(baseline.mode(self.program, 'clean'), 'clean')

    def test_recognized_marker_without_baseline_refuses_instead_of_falling_back(self):
        write(self.program/'python-app.json', {'version': '999', 'source_update_protocol': baseline.PROTOCOL})
        self.refuse_mode()
        self.assertEqual(baseline.mode(self.program, 'clean'), 'clean')

    def test_invalid_requested_mode_is_rejected(self):
        for request in ('automatic', '', True):
            with self.subTest(request=request), self.assertRaises(ValueError):
                baseline.mode(self.program, request)

    def test_live_app_modification_addition_deletion_do_not_reseal_pristine_a(self):
        self.seal()
        frozen = self.immutable_bytes()
        self.put('app/localharness/feature.py', b'owner_modified = True\n')
        self.put('app/localharness/authored.py', b'owner_added = True\n')
        (self.program/'app/static/index.html').unlink()
        baseline.validate(self.program)
        self.assertEqual(baseline.mode(self.program), 'preserve')
        self.assertEqual(self.immutable_bytes(), frozen)
        destination = self.root/'extracted-app'
        baseline.extract(self.program, 'app', destination)
        self.assertEqual(self.files(destination), self.original_app)

    def test_repeated_seal_never_promotes_authored_app_into_new_a(self):
        self.seal()
        frozen = self.immutable_bytes()
        self.put('app/localharness/feature.py', b'authored_later = True\n')
        self.seal()
        self.assertEqual(self.immutable_bytes(), frozen)
        destination = self.root/'still-original-app'
        baseline.extract(self.program, 'app', destination)
        self.assertEqual(self.files(destination), self.original_app)

    def test_tree_and_binary_resource_extraction_preserves_every_original_byte(self):
        self.seal()
        self.put('tree/agent.py', b'owner changed tree\n')
        self.put('tree/owner_added.py', b'authored addition\n')
        (self.program/'tree/nested/feature.py').unlink()
        frozen = self.immutable_bytes()
        for surface, expected in (('tree', self.original_tree), ('app', self.original_app)):
            with self.subTest(surface=surface):
                destination = self.root/('extracted-'+surface)
                baseline.extract(self.program, surface, destination)
                self.assertEqual(self.files(destination), expected)
        self.assertEqual(self.immutable_bytes(), frozen)

    def test_missing_protocol_inputs_refuse_default_and_preserve(self):
        self.seal()
        for name in self.immutable_bytes():
            with self.subTest(missing=name):
                path = self.program/name
                original = path.read_bytes()
                path.unlink()
                try:
                    self.refuse_mode()
                    self.assertEqual(baseline.mode(self.program, 'clean'), 'clean')
                finally:path.write_bytes(original)

    def test_changed_archive_bytes_refuse_even_when_zip_still_opens(self):
        self.seal()
        for name in ('code-upstream.zip', 'app-upstream.zip'):
            with self.subTest(archive=name):
                path = self.program/name
                original = path.read_bytes()
                path.write_bytes(original+b'changed trailing bytes')
                try:
                    with zipfile.ZipFile(path) as z:self.assertIsNone(z.testzip())
                    self.refuse_mode()
                finally:path.write_bytes(original)

    def test_manifest_or_admission_anchor_tampering_is_not_accepted(self):
        self.seal()
        for name in ('code-upstream.json', 'app-upstream.json', 'source-update-baseline.json'):
            with self.subTest(manifest=name):
                path = self.program/name
                original = path.read_bytes()
                value = read(path);value['extra_tampered_field'] = True;write(path, value)
                try:self.refuse_mode()
                finally:path.write_bytes(original)

    def test_coherently_rebound_duplicate_members_are_refused_before_extraction(self):
        self.seal()
        frozen = self.snapshot()
        for surface in ('app', 'tree'):
            with self.subTest(surface=surface):
                self.restore(frozen)
                entries = self.archive_entries(surface)
                entries.append((copy.copy(entries[0][0]), entries[0][1]))
                self.publish_coherent_archive(surface, entries)
                with self.assertRaises(REFUSAL):baseline.validate(self.program)
                destination = self.root/('duplicate-output-'+surface)
                with self.assertRaises(REFUSAL):baseline.extract(self.program, surface, destination)
                self.assertFalse(destination.exists())

    def test_coherently_rebound_unsafe_member_inventory_is_refused(self):
        self.seal()
        original_entries = self.archive_entries('app')
        original_files = copy.deepcopy(read(self.program/'app-upstream.json')['files'])
        outside = self.root/'outside.py';outside.write_bytes(b'owner sentinel')
        for name in ('../outside.py', '/outside.py', 'C:/outside.py',
                     'safe/../../outside.py', 'localharness\\outside.py'):
            with self.subTest(member=name):
                content = b'hostile fixture, never execute'
                entry = zipfile.ZipInfo(name);entry.external_attr = 0o100644 << 16
                files = copy.deepcopy(original_files)
                files[name] = {'sha256': hashlib.sha256(content).hexdigest(), 'mode': '100644'}
                self.publish_coherent_archive('app', [*original_entries, (entry, content)], files=files)
                destination = self.root/'unsafe-output'
                with self.assertRaises(REFUSAL):baseline.extract(self.program, 'app', destination)
                self.assertFalse(destination.exists())
                self.assertEqual(outside.read_bytes(), b'owner sentinel')

    def test_coherently_rebound_missing_member_and_wrong_member_bytes_are_refused(self):
        self.seal()
        frozen = self.snapshot()
        for surface in ('app', 'tree'):
            self.restore(frozen)
            entries = self.archive_entries(surface)
            for mutation in ('missing', 'changed'):
                with self.subTest(surface=surface, mutation=mutation):
                    self.restore(frozen)
                    mutated = entries[1:] if mutation=='missing' else [
                        (entries[0][0], entries[0][1]+b'changed'), *entries[1:]]
                    self.publish_coherent_archive(surface, mutated)
                    with self.assertRaises(REFUSAL):baseline.validate(self.program)

    def test_coherently_rebound_symlink_member_is_refused(self):
        self.seal()
        entries = self.archive_entries('app')
        info, content = entries[0]
        info.external_attr = 0o120777 << 16
        self.publish_coherent_archive('app', [(info, content), *entries[1:]])
        with self.assertRaises(REFUSAL):baseline.validate(self.program)

    def test_unknown_extract_surface_has_no_filesystem_effect(self):
        self.seal()
        destination = self.root/'unknown-surface'
        with self.assertRaises(ValueError):baseline.extract(self.program, 'runtime', destination)
        self.assertFalse(destination.exists())


if __name__=='__main__':unittest.main()
