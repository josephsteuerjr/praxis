"""Temporary work is not an implicit legacy installation or cleanup authority."""
from pathlib import Path
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
from localharness import first_start as start
import t_first_start_3010 as fixtures


class TemporaryScope(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.FirstStart()
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.local = self.fx.local
        self.root = self.fx.root
        self.places = self.fx.places
        self.backend = self.fx.backend

    def prepare(self):
        return self.fx.prepare()

    def marked_fixture(self, container):
        root = container/'helene-synthetic'
        (root/'app').mkdir(parents=True)
        (root/'app/python_app.py').write_bytes(b'inert')
        (root/'app/deskapp.py').write_bytes(b'inert')
        memory = root/'deep/extra/unknown/agent-data/memory'
        memory.mkdir(parents=True)
        (memory/'identity.md').write_bytes(b'synthetic identity; must remain')
        return root

    def test_temp_is_not_scanned_or_adopted_while_real_legacy_install_is_migrated(self):
        fixture = self.marked_fixture(self.local/'Temp')
        old, data = self.fx.old()
        original = {p.relative_to(fixture):p.read_bytes() for p in fixture.rglob('*') if p.is_file()}
        real_scandir = os.scandir
        def scoped(path):
            candidate = Path(path).resolve()
            if candidate == fixture.parent or candidate.is_relative_to(fixture.parent):
                raise AssertionError('Implicit discovery traversed temporary work')
            return real_scandir(path)
        with patch.object(start.os, 'scandir', side_effect=scoped):
            roots, _ = start.discover(self.root, self.places)
        self.assertIn(old, roots)
        self.assertNotIn(fixture, roots)
        self.prepare()
        self.assertFalse(old.exists())
        self.assertTrue((self.places.data/'memory/groups/window.jsonl').exists())
        self.assertEqual({p.relative_to(fixture):p.read_bytes() for p in fixture.rglob('*') if p.is_file()}, original)

    def test_configured_temporary_container_is_excluded_not_similarly_named_vendor(self):
        custom = self.local/'Custom Scratch'
        fixture = self.marked_fixture(custom)
        real = self.local/'Temp-other/Helene'
        (real/'app').mkdir(parents=True)
        (real/'app/python_app.py').write_bytes(b'inert')
        (real/'app/deskapp.py').write_bytes(b'inert')
        with patch.dict(os.environ, {'TEMP':str(custom), 'TMP':str(custom)}), patch('tempfile.gettempdir', return_value=str(custom)):
            roots, _ = start.discover(self.root, self.places)
        self.assertNotIn(fixture, roots)
        self.assertIn(real, roots)

    def test_long_local_cache_and_real_profile_are_independent(self):
        from localharness.windows_paths import Path as FilePath, wide
        import shutil
        old, data=self.fx.old()
        deep=FilePath('segment'*8)/('x'*89)/('y'*89)/'notes.bin'
        private=FilePath(data)/'workspace'/deep
        private.parent.mkdir(parents=True)
        private.write_bytes(b'profile document')
        cache=FilePath(self.local)/'Helene cache'
        marker=cache/deep.parent/'unknown/memory/identity.md'
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b'unbound cache')
        self.addCleanup(lambda:shutil.rmtree(wide(cache)) if cache.exists() else None)
        self.addCleanup(lambda:shutil.rmtree(wide(self.places.data)) if self.places.data.exists() else None)
        result=self.prepare()
        self.assertTrue(result['agent'])
        self.assertEqual((FilePath(self.places.data)/'workspace'/deep).read_bytes(),b'profile document')
        self.assertEqual(marker.read_bytes(),b'unbound cache')
        self.assertIn(str(cache),result['cleanup_errors'])

    def test_bound_profile_can_move_while_ambiguous_sibling_stays_in_place(self):
        from localharness.windows_paths import Path as FilePath, wide
        import shutil
        old,data=self.fx.old()
        memory=FilePath(old)/('a'*95)/('b'*95)/'untagged/notes/memory'
        memory.mkdir(parents=True)
        (memory/'keep.txt').write_bytes(b'unknown sibling')
        self.addCleanup(lambda:shutil.rmtree(wide(old)) if old.exists() else None)
        result=self.prepare()
        self.assertTrue(result['agent'])
        self.assertIn(str(old),result['cleanup_errors'])
        self.assertEqual((memory/'keep.txt').read_bytes(),b'unknown sibling')
        self.assertTrue((self.places.data/'memory/groups/window.jsonl').is_file())

    def test_unbound_local_data_is_preserved_without_blocking_installation(self):
        real = self.local/'Helene unknown'
        memory = real/'deep/agent-data/memory'
        memory.mkdir(parents=True)
        (memory/'identity.md').write_bytes(b'unknown owner data')
        result=self.prepare()
        self.assertIn(str(real),result['cleanup_errors'])
        self.assertEqual((memory/'identity.md').read_bytes(), b'unknown owner data')


if __name__ == '__main__':
    unittest.main()
