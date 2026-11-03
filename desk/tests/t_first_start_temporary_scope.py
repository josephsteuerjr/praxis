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

    def test_unknown_real_legacy_data_still_refuses_before_cleanup(self):
        real = self.local/'Helene unknown'
        memory = real/'deep/agent-data/memory'
        memory.mkdir(parents=True)
        (memory/'identity.md').write_bytes(b'unknown owner data')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertEqual(self.backend.actions, [])
        self.assertEqual((memory/'identity.md').read_bytes(), b'unknown owner data')


if __name__ == '__main__':
    unittest.main()
