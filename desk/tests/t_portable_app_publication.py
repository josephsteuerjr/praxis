"""Real composition publication/recovery; programmes and runtime bytes are inert."""
from contextlib import ExitStack
import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).parent)]
import t_app_source_update_3110 as fixtures
from t_first_start_3010 import Backend
from localharness import portable_switch as switch, first_start, code_transition
from localharness import update_composition as composition, windows_first_start
from localharness import portable_app_publication as publication
from localharness.package_notice import read, write


class PortableAppPublication(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.AppSourceUpdate(); self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.places = first_start.Places(self.fx.root/'User', (), (), self.fx.root/'Downloads')
        self.fx.data = self.places.data; self.fx.data.mkdir(parents=True)
        self.fx.directory = self.fx.data/'memory/.source-updates'/('a'*64)
        self.fx.owner_edit()
        self.fx.put(self.fx.old, 'app/python_app.py', b'window = "authored"\n')
        self.fx.put(self.fx.old, 'app/new/deep/feature.py', b'added = True\n')
        (self.fx.old/'app/resources/remove-locally.txt').unlink()
        self.fx.prepare_pair()
        from localharness.app_source_update import finish
        finish(self.fx.directory)
        self.plans = [{'directory':str(self.fx.directory), 'source':str(self.fx.old/'tree')}]
        composition.overlay(self.fx.new, self.plans)
        self.before = composition.app_inventory(self.fx.new/'app')
        self.desired = composition.app_inventory(self.fx.directory/'compound/app')
        self.original = self.fx.snapshot(self.fx.old)

    def test_real_selector_publishes_existing_nested_added_and_deleted_members_idempotently(self):
        publication.publish(self.fx.new)
        self.assertEqual(composition.app_inventory(self.fx.new/'app'), self.desired)
        composition.selected_files(self.fx.new)
        publication.publish(self.fx.new)
        self.assertEqual(composition.app_inventory(self.fx.new/'app'), self.desired)
        self.assertEqual(self.fx.snapshot(self.fx.old), self.original)
        self.assertFalse(publication._marker(self.fx.new).exists())

    def interrupted(self):
        original = publication._copy
        calls = []
        def copy(source, destination, expected, **kwargs):
            if len(calls) == 1:
                raise OSError('synthetic failure after first replacement')
            calls.append(destination)
            return original(source, destination, expected, **kwargs)
        with patch.object(publication, '_copy', side_effect=copy):
            with self.assertRaisesRegex(OSError, 'after first replacement'):
                publication.publish(self.fx.new)
        self.assertEqual(len(calls), 1)
        self.assertTrue(publication._marker(self.fx.new).exists())
        # This is the actual old retry obstacle, not a mocked selector.
        with self.assertRaisesRegex(ValueError, 'Verified incoming app changed'):
            composition.selected_files(self.fx.new)

    def test_second_member_failure_restores_B_then_real_composition_retry_succeeds(self):
        self.interrupted()
        orphan = self.fx.new.parent/'.portable-app-publish-interrupted'
        orphan.write_bytes(b'incomplete stream outside app; retained after abrupt death')
        self.assertTrue(publication.recover(self.fx.new))
        self.assertTrue(orphan.exists())
        self.assertEqual(composition.app_inventory(self.fx.new/'app'), self.before)
        composition.selected_files(self.fx.new)
        publication.publish(self.fx.new)
        self.assertEqual(composition.app_inventory(self.fx.new/'app'), self.desired)
        self.assertEqual(self.fx.snapshot(self.fx.old), self.original)

    def test_later_destination_edit_is_preserved_before_any_restoration(self):
        self.interrupted()
        changed = self.fx.put(self.fx.new, 'app/python_app.py', b'later_owner_edit = True\n')
        snapshot = self.fx.snapshot(self.fx.new/'app')
        with self.assertRaisesRegex(ValueError, 'destination changed'):
            publication.recover(self.fx.new)
        self.assertEqual(self.fx.snapshot(self.fx.new/'app'), snapshot)
        self.assertEqual(changed.read_bytes(), b'later_owner_edit = True\n')
        self.assertTrue(publication._marker(self.fx.new).exists())

    def test_changed_frozen_B_refuses_before_restoration(self):
        self.interrupted()
        self.fx.put(self.fx.directory/'compound/base-app', 'python_app.py', b'tampered B\n')
        snapshot = self.fx.snapshot(self.fx.new/'app')
        with self.assertRaisesRegex(ValueError, 'frozen B changed'):
            publication.recover(self.fx.new)
        self.assertEqual(self.fx.snapshot(self.fx.new/'app'), snapshot)

    def test_failed_stream_validation_preserves_destination(self):
        # Change after the authoritative selector's read, during stream copy.
        original = publication._copy
        attempted = []
        def mutate(source, destination, expected, **kwargs):
            attempted.append((destination, destination.read_bytes() if destination.exists() else None))
            Path(source).write_bytes(b'changed after admission\n')
            return original(source, destination, expected, **kwargs)
        with patch.object(publication, '_copy', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'stream changed'):
                publication.publish(self.fx.new)
        destination, before = attempted[0]
        self.assertEqual(destination.read_bytes() if destination.exists() else None, before)
        self.assertTrue(publication._marker(self.fx.new).exists())

    def test_portable_prepare_uses_real_composition_publisher(self):
        write(self.fx.old/'helene.json', {'mode':'local','tree':str(self.fx.data),
            'code':str(self.fx.old/'tree'), 'relay':{'enabled':False}})
        write(first_start.receipt(self.fx.old), {'format':first_start.FORMAT,
            'state':'completed','completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'data':str(self.fx.data)})
        def selected(_old, new, note, **kwargs):
            value = read(new/'helene.json'); value['code'] = str(new/'tree')
            write(new/'helene.json', value)
        with ExitStack() as stack:
            for target, name, kwargs in (
                (code_transition,'validate',{}),
                (code_transition,'prepare',{'side_effect':selected}),
                (first_start.Places,'current',{'return_value':self.places}),
                (windows_first_start,'Backend',{'return_value':Backend()}),
                (switch,'payload',{'return_value':{'inert_fixture':True}}),
            ):
                stack.enter_context(patch.object(target,name,**kwargs))
            result = switch.prepare(self.fx.old,self.fx.new)
        self.assertEqual(result['format'],switch.FORMAT)
        self.assertEqual(composition.app_inventory(self.fx.new/'app'),self.desired)


if __name__ == '__main__':
    unittest.main()
