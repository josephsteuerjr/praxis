"""Cleanup needs product evidence outside AppData; no real OS authority."""
from contextlib import contextmanager
import datetime as dt
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import first_start as start, windows_first_start, windows_hooks


class _Backend:
    def __init__(self, registered=()):
        self.registrations = list(registered)
        self.actions = []

    def registered(self, places):return list(self.registrations)
    @contextmanager
    def exclusive(self, target):yield
    def stop(self, roots, protected=()):self.actions.append(('stop', tuple(roots)))
    def assert_quiet(self, roots, protected=()):self.actions.append(('quiet', tuple(roots)))
    def retire(self, path):self.actions.append(('retire', path))
    def shortcuts(self, roots, current):self.actions.append(('shortcuts', tuple(roots), current))
    def remove(self, path):
        self.actions.append(('remove', path))
        if path.is_dir():shutil.rmtree(path)
        else:path.unlink()


class CleanupOwnership(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cleanup-ownership-test-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.profile = self.base/'User — Юзер'
        self.local = self.profile/'AppData/Local'
        self.roaming = self.profile/'AppData/Roaming'
        self.pf = self.base/'Program Files'
        self.downloads = self.profile/'Downloads'
        for path in (self.local, self.roaming, self.pf, self.downloads):path.mkdir(parents=True)
        self.root = self.downloads/'Helene current/Helene'
        self.root.mkdir(parents=True)
        self.places = start.Places(self.profile, (self.local, self.roaming), (self.pf,), self.downloads)
        self.backend = _Backend()
        # A future implementation must fail the test rather than touch host
        # processes, services, Task Scheduler, autoruns or installed profiles.
        for name in ('kernel', 'terminate', 'restart_holders', 'user_sid', 'Backend'):
            self.enterContext(patch.object(windows_first_start, name,
                side_effect=AssertionError('Host Windows authority forbidden in ownership tests')))
        self.enterContext(patch.object(windows_hooks, 'retire',
            side_effect=AssertionError('Host hook authority forbidden in ownership tests')))

    def put(self, path, content=b'fixture'):
        path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def document(self, path, value):return self.put(path, json.dumps(value).encode('utf-8'))

    def programme(self, folder):
        folder = Path(folder)
        self.document(folder/'python-app.json', {'format': 'helene-python-app.v1', 'version': 'fixture'})
        self.put(folder/'app/python_app.py', b'published application entry\n')
        self.put(folder/'app/deskapp.py', b'published channel entry\n')
        return folder

    def discovered(self):
        roots, siblings = start.discover(self.root, self.places)
        return set(roots), set(siblings)

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in root.rglob('*') if p.is_file()}

    def test_foreign_branded_downloads_and_program_files_folders_are_preserved(self):
        candidates = []
        for parent in (self.downloads, self.pf):
            for name in ('Helene family photos', 'my-hElEnE-research', 'Hélène notes', 'элен-письма'):
                folder = parent/name
                self.put(folder/'personal.txt', b'owner personal content')
                candidates.append(folder)
        before = {p: self.snapshot(p) for p in candidates}
        roots, siblings = self.discovered()
        self.assertTrue(all(p not in roots|siblings for p in candidates))
        self.assertEqual({p: self.snapshot(p) for p in candidates}, before)
        self.assertEqual(self.backend.actions, [])

    def test_personal_branded_files_and_nonproduct_archives_are_not_cleanup_members(self):
        personal = [self.put(self.downloads/name, b'owner content') for name in (
            'Helene-photo.jpg', 'Hélène-notes.md', 'Helene-personal.exe', 'Helene-broken.zip')]
        self.put(self.downloads/'START-HERE.md', b'Personal notes about Helene, not an application receipt.')
        personal.append(self.downloads/'START-HERE.md')
        photos = self.downloads/'Helene-family.zip'
        with zipfile.ZipFile(photos, 'w') as z:z.writestr('photos/holiday.jpg', b'personal photo')
        personal.append(photos)
        before = {p: p.read_bytes() for p in personal}
        for path in personal:
            with self.subTest(path=path.name):self.assertFalse(start.characteristic(path))
        roots, siblings = self.discovered()
        self.assertTrue(all(p not in roots|siblings for p in personal))
        self.assertEqual({p: p.read_bytes() for p in personal}, before)

    def test_product_distribution_zip_and_exact_known_binary_are_members(self):
        archive = self.downloads/'Helene-fixture-Windows.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('Helene/python-app.json', json.dumps({'format': 'helene-python-app.v1', 'version': 'fixture'}))
            z.writestr('Helene/helene.exe', b'known product binary fixture')
            z.writestr('Helene/app/python_app.py', b'product entry fixture')
            z.writestr('Helene/app/deskapp.py', b'product channel fixture')
        binary = self.put(self.downloads/'helene.exe', b'known product binary fixture')
        self.assertTrue(start.characteristic(archive))
        self.assertTrue(start.characteristic(binary))
        _roots, siblings = self.discovered()
        self.assertIn(archive, siblings);self.assertIn(binary, siblings)

    def test_known_mechanical_prefix_does_not_need_a_product_directory(self):
        files = [self.put(self.downloads/name, b'{}') for name in (
            'update-job-ref-fixture.json', '.python-migrator-fixture.lock')]
        _roots, siblings = self.discovered()
        for path in files:
            with self.subTest(path=path.name):self.assertIn(path, siblings)

    def test_branded_owned_wrapper_is_found_within_three_levels(self):
        outer = self.downloads/'Helene prior release'
        self.programme(outer/'one/two/Helene')
        self.assertTrue(start.programme_container(outer))
        _roots, siblings = self.discovered()
        self.assertIn(outer, siblings)

    def test_marker_beyond_wrapper_depth_limit_does_not_admit_parent(self):
        outer = self.downloads/'Helene unrelated archive collection'
        self.programme(outer/'one/two/three/four/Helene')
        self.assertFalse(start.programme_container(outer))
        _roots, siblings = self.discovered()
        self.assertNotIn(outer, siblings)

    def test_unbranded_downloads_parent_is_not_deleted_for_a_nested_product(self):
        parent = self.downloads/'Personal archive collection'
        self.programme(parent/'Helene prior')
        self.put(parent/'owner.txt', b'unrelated owner file')
        before = self.snapshot(parent)
        roots, siblings = self.discovered()
        self.assertNotIn(parent, roots|siblings)
        self.assertEqual(self.snapshot(parent), before)

    def test_program_files_admits_marked_child_without_admitting_unbranded_parent(self):
        parent = self.pf/'Vendor collection'
        child = self.programme(parent/'Helene native')
        self.put(parent/'owner.txt', b'foreign vendor content')
        roots, _siblings = self.discovered()
        self.assertIn(child, roots)
        self.assertNotIn(parent, roots)

    def test_desktop_programme_uses_its_actual_neighbours(self):
        desktop = self.profile/'Desktop'
        self.root = self.programme(desktop/'Helene active')
        old = self.programme(desktop/'Helene previous')
        foreign = desktop/'Helene personal notes'
        self.put(foreign/'owner.md', b'owner notes')
        roots, siblings = self.discovered()
        self.assertIn(old, siblings)
        self.assertNotIn(self.root, roots|siblings)
        self.assertNotIn(foreign, roots|siblings)

    def test_arbitrary_location_uses_neighbours_without_profile_or_drive_assumptions(self):
        parent = self.base/'Other drive simulation/Portable tools'
        self.root = self.programme(parent/'Helene active')
        old = self.programme(parent/'Helene previous')
        outside = self.programme(self.base/'Other drive simulation/Elsewhere/Helene unrelated')
        roots, siblings = self.discovered()
        self.assertIn(old, siblings)
        self.assertNotIn(self.root, roots|siblings)
        self.assertNotIn(outside, roots|siblings)

    def test_programme_elsewhere_does_not_scan_unrelated_downloads(self):
        self.root = self.programme(self.profile/'Desktop/Helene active')
        foreign = self.programme(self.downloads/'Helene separately downloaded')
        self.put(foreign/'owner.txt', b'not in the active programme neighbourhood')
        before = self.snapshot(foreign)
        roots, siblings = self.discovered()
        self.assertNotIn(foreign, roots|siblings)
        self.assertEqual(self.snapshot(foreign), before)

    def test_wrapped_programme_uses_outer_neighbours_and_protects_root_and_outer(self):
        desktop = self.profile/'Desktop'
        outer = desktop/'Helene active archive'
        self.root = self.programme(outer/'Helene')
        old_outer = desktop/'Helene previous archive'
        self.programme(old_outer/'Helene')
        self.put(outer/'owner.txt', b'current outer folder must remain')
        before = self.snapshot(outer)
        roots, siblings = self.discovered()
        self.assertIn(old_outer, siblings)
        self.assertNotIn(outer, roots|siblings)
        self.assertNotIn(self.root, roots|siblings)
        self.assertEqual(self.snapshot(outer), before)

    def test_parent_with_its_own_marker_is_not_mistaken_for_an_archive_wrapper(self):
        parent = self.programme(self.profile/'Desktop/Helene programme parent')
        self.root = self.programme(parent/'Helene')
        old = self.programme(parent/'Helene previous')
        roots, siblings = self.discovered()
        self.assertIn(old, siblings)
        self.assertNotIn(parent, roots|siblings)
        self.assertNotIn(self.root, roots|siblings)

    def test_marker_without_branded_directory_name_is_not_enough_for_discovery(self):
        folders = [self.programme(parent/'Other application') for parent in (self.downloads, self.pf)]
        roots, siblings = self.discovered()
        self.assertTrue(all(p not in roots|siblings for p in folders))

    def test_single_partial_app_entry_is_not_a_programme_marker(self):
        folder = self.downloads/'Helene partial files'
        first = self.put(folder/'app/python_app.py')
        self.assertFalse(start.programme_marker(folder))
        first.unlink();self.put(folder/'app/deskapp.py')
        self.assertFalse(start.programme_marker(folder))
        self.put(folder/'app/python_app.py')
        self.assertTrue(start.programme_marker(folder))

    def test_marker_files_must_be_regular_files(self):
        folder = self.downloads/'Helene lookalike directories'
        for name in ('helene.exe', 'python-app.json', 'helene.json', 'app/python_app.py', 'app/deskapp.py'):
            (folder/name).mkdir(parents=True, exist_ok=True)
        self.assertFalse(start.programme_marker(folder))

    def test_valid_metadata_or_legacy_configuration_is_product_evidence(self):
        folder = self.downloads/'Helene marker fixture';folder.mkdir()
        self.document(folder/'python-app.json', {'format': 'helene-python-app.v1', 'version': 'fixture'})
        self.assertTrue(start.programme_marker(folder))
        (folder/'python-app.json').unlink()
        for mode in ('local', 'remote'):
            with self.subTest(mode=mode):
                self.document(folder/'helene.json', {'mode': mode, 'tree': 'data', 'code': 'tree'})
                self.assertTrue(start.programme_marker(folder))

    def test_unrelated_or_malformed_json_is_not_product_evidence(self):
        folder = self.downloads/'Helene personal JSON';folder.mkdir()
        for name, values in (
            ('python-app.json', [b'{malformed', b'[]', b'{"format":"other-product"}']),
            ('helene.json', [b'{malformed', b'[]', b'{"name":"Helene"}',
                             b'{"mode":"other-product","tree":"data","code":"tree"}']),
        ):
            for content in values:
                with self.subTest(name=name, content=content):
                    self.put(folder/name, content)
                    self.assertFalse(start.programme_marker(folder))
            (folder/name).unlink()

    def test_structured_first_start_receipt_is_product_evidence(self):
        folder = self.downloads/'Helene receipted'
        self.document(start.receipt(folder), {'format': start.FORMAT, 'state': 'completed',
            'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'data': str(self.places.data)})
        self.assertTrue(start.programme_marker(folder))
        self.assertTrue(start.programme_container(folder))

    def test_receipt_filename_and_person_name_alone_are_not_product_evidence(self):
        folder = self.downloads/'Helene personal receipt'
        self.document(start.receipt(folder), {'name': 'Helene', 'state': 'completed'})
        self.document(folder/'python-migration.json', {'name': 'Helene', 'state': 'completed'})
        self.assertFalse(start.programme_marker(folder))

    def test_source_and_verification_subtrees_do_not_admit_a_wrapper(self):
        outer = self.downloads/'Helene source collection'
        for skip in ('runtime', 'app', 'tree', 'code', '.git', 'verification', 'data', 'memory'):
            self.programme(outer/skip/'Fixture')
        self.assertFalse(start.programme_marker(outer))
        self.assertFalse(start.programme_container(outer))
        _roots, siblings = self.discovered()
        self.assertNotIn(outer, siblings)

    def test_link_policy_outside_appdata_neither_follows_nor_admits_unknown_links(self):
        # Simulate the lstat/reparse classification seam without creating an OS
        # symlink/junction or requiring developer-mode privileges.
        downloads_link = self.programme(self.downloads/'Helene unknown link')
        pf_link = self.programme(self.pf/'Helene unknown link')
        actual_linked = start.linked
        virtual_links = {downloads_link, pf_link}
        def linked(path):return Path(path) in virtual_links or actual_linked(path)
        with patch.object(start, 'linked', side_effect=linked):
            roots, siblings = self.discovered()
            self.assertNotIn(downloads_link, roots|siblings)
            self.assertNotIn(pf_link, roots|siblings)

    def test_linked_marker_file_does_not_grant_ownership(self):
        folder = self.downloads/'Helene linked marker'
        marker = self.put(folder/'helene.exe')
        actual_plain = start.plain
        def plain(path):
            if Path(path)==marker:raise ValueError('Synthetic linked marker path')
            return actual_plain(path)
        with patch.object(start, 'plain', side_effect=plain):
            self.assertFalse(start.programme_marker(folder))

    def test_registry_supplied_native_root_remains_admitted_with_fake_backend(self):
        native = self.pf/'Vendor/Unbranded native directory';native.mkdir(parents=True)
        self.put(native/'native-component.bin', b'registered native fixture')
        self.assertFalse(start.programme_marker(native))
        plan = start.legacy_plan(self.root, self.places, _Backend([native]))
        self.assertIn(native, plan['old_roots'])

    def test_empty_appdata_substring_remnant_still_has_explicit_cleanup_authority(self):
        remnant = self.local/'Vendor/prefix-hElEnE-obsolete';remnant.mkdir(parents=True)
        self.assertFalse(start.programme_marker(remnant))
        plan = start.legacy_plan(self.root, self.places, self.backend)
        self.assertIn(remnant, plan['old_roots'])
        start.retire_legacy(self.root, self.places.data, self.places, self.backend,
            plan, [], self.places.data, lambda row: None, modern=True, shortcuts=False)
        self.assertFalse(remnant.exists())
        self.assertIn(('remove', remnant), self.backend.actions)


if __name__=='__main__':unittest.main()
