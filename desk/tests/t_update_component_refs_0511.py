"""Updater consumer seam; store internals belong to the voice test battery."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import update_profile, update_overlay


class ComponentReferences(unittest.TestCase):
    def test_preparation_uses_one_store_and_overlay_can_restore_previous_pins(self):
        with tempfile.TemporaryDirectory(prefix='helene update refs synthetic ') as tmp:
            root=Path(tmp);old=root/'old';new=root/'new';data=root/'data'
            old.mkdir();new.mkdir();data.mkdir()
            (old/'helene.json').write_text(json.dumps({'tree':str(data),'code':str(data),'runner':''}))
            original=b'{"format":"helene-component-pins.v1","pins":{},"synthetic":"old"}'
            (old/'.helene-component-pins.json').write_bytes(original)
            references={'format':'helene-component-pins.v1','pins':{},'synthetic':'new refs'}
            def prepare_update(previous,current):
                self.assertEqual((previous,current),(old,new))
                (new/'.helene-component-pins.json').write_text(json.dumps(references))
                return [{'copied_bytes':0,'reused':True}]
            store=SimpleNamespace(prepare_update=Mock(side_effect=prepare_update),export_references=Mock(return_value=references))
            with patch('importlib.import_module',return_value=store):
                note=update_profile.prepare(old,new)
            store.prepare_update.assert_called_once_with(old,new)
            store.export_references.assert_called_once_with(new)
            self.assertEqual(note['component_store']['references'],references)
            self.assertEqual((old/'.helene-component-pins.json').read_bytes(),original)
            with zipfile.ZipFile(note['backup']) as saved:
                self.assertEqual(saved.read('.helene-component-pins.json'),original)
            recovery=root/'recovery'
            overlay=update_overlay.prepare(new,old,recovery)
            pin=next(row for row in overlay['rows'] if row['path']=='.helene-component-pins.json')
            self.assertTrue(pin['existed'])
            self.assertEqual((recovery/'.helene-component-pins.json').read_bytes(),original)
            # The normal transaction carries only pin metadata, no fake payload.
            self.assertFalse(any('payload' in p.parts for p in new.rglob('*')))
            update_overlay.apply(new,old,recovery)
            self.assertEqual(json.loads((old/'.helene-component-pins.json').read_text()),references)
            from localharness.python_source_updater import rollback
            rollback(old,recovery)
            self.assertEqual((old/'.helene-component-pins.json').read_bytes(),original)

    def test_missing_store_with_existing_pins_is_an_explicit_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            old=Path(tmp)/'old';new=Path(tmp)/'new';old.mkdir();new.mkdir()
            missing=ModuleNotFoundError(name='localharness.component_store')
            with patch('importlib.import_module',side_effect=missing):
                self.assertIsNone(update_profile._component_references(old,new))
                (old/'.helene-component-pins.json').write_text('{}')
                with self.assertRaisesRegex(RuntimeError,'required'):
                    update_profile._component_references(old,new)

    def test_late_component_pin_change_invalidates_the_old_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir()
            (root/'helene.json').write_text(json.dumps({'tree':str(data),'code':str(data),'runner':''}))
            pins=root/'.helene-component-pins.json';pins.write_text('{"synthetic":"one"}')
            before=update_profile.selection(root)
            pins.write_text('{"synthetic":"two"}')
            self.assertNotEqual(before['settings'],update_profile.selection(root)['settings'])

    def test_folder_switch_payload_binds_exact_new_component_pin_bytes(self):
        from localharness import portable_switch
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'tree').mkdir()
            (root/'code-upstream.zip').write_bytes(b'synthetic upstream')
            (root/'python-package-files.json').write_text('{"files":{}}')
            pins=root/'.helene-component-pins.json';pins.write_text('{"synthetic":"one"}')
            before=portable_switch.payload(root,runtime=False)
            pins.write_text('{"synthetic":"two"}')
            self.assertNotEqual(before['component_pins'],portable_switch.payload(root,runtime=False)['component_pins'])


if __name__=='__main__':unittest.main(verbosity=2)
