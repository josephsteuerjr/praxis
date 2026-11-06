"""Re-extracting Helene must not resume another directory's parent sidecar."""
from pathlib import Path
import hashlib
import unittest
from unittest.mock import patch
import t_clean_switch_0511 as fixture

clean=fixture.clean
updater=fixture.python_source_updater
read,write=fixture.read,fixture.write


class OrphanPreparation(unittest.TestCase):
    setUp=fixture.CleanSwitch.setUp
    prepare=fixture.CleanSwitch.prepare

    def replace_incoming(self):
        self.new.rename(self.base/'previous-incoming')
        (self.new/'app').mkdir(parents=True)
        (self.new/'tree').mkdir()
        write(self.new/'python-app.json',read(self.base/'previous-incoming/python-app.json'))

    def retained(self,raw):
        return self.new/'.helene/recovery'/('orphan-preparation-'+hashlib.sha256(raw).hexdigest()+'.json')

    def test_fresh_same_path_does_not_reuse_previous_operation(self):
        prior=self.prepare();path=updater.prepared_install_path(self.new);raw=path.read_bytes()
        self.replace_incoming()
        current=self.prepare()
        self.assertNotEqual(current['note']['id'],prior['note']['id'])
        self.assertEqual(self.retained(raw).read_bytes(),raw)
        self.assertEqual(read(self.new/'helene.json')['tree'],str(self.data))
        self.assertEqual(read(self.new/'python-migration.json'),current['note'])
        self.assertEqual((self.data/'memory/history').read_bytes(),b'irreplaceable owner history')
        self.assertEqual(self.events,[])

    def test_fresh_archive_uses_selected_owner_instead_of_stale_owner(self):
        prior=self.prepare();path=updater.prepared_install_path(self.new)
        prior['old']=str(self.base/'obsolete-owner');prior['note']['old_root']=prior['old']
        write(path,prior);raw=path.read_bytes();self.replace_incoming()
        current=updater.prepare_install(self.old,self.new,folder_switch=True)
        self.assertEqual(current['old'],str(self.old))
        self.assertNotEqual(current['note']['id'],prior['note']['id'])
        self.assertEqual(self.retained(raw).read_bytes(),raw)
        self.assertTrue(self.old.is_dir());self.assertEqual(self.events,[])

    def test_bound_preparation_retries_without_new_operation(self):
        value=self.prepare()
        self.assertEqual(self.prepare(),value)
        self.assertFalse((self.new/'.helene/recovery').exists())

    def test_partial_preparation_with_settings_still_refuses_another_owner(self):
        value=self.prepare();path=updater.prepared_install_path(self.new)
        value['old']=str(self.base/'other');write(path,value)
        (self.new/'python-migration.json').unlink();before=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'another programme'):self.prepare()
        self.assertEqual(path.read_bytes(),before)
        self.assertFalse((self.new/'.helene/recovery').exists())

    def test_orphan_is_not_adopted_during_settings_refresh(self):
        value=self.prepare();path=updater.prepared_install_path(self.new)
        value['old']=str(self.base/'other');write(path,value);self.replace_incoming()
        with self.assertRaisesRegex(ValueError,'another programme'):
            clean.prepare(self.old,self.new,refresh=True)

    def test_retention_failure_does_not_replace_previous_receipt(self):
        self.prepare();path=updater.prepared_install_path(self.new);raw=path.read_bytes();self.replace_incoming()
        with patch('localharness.private_files.write_private',side_effect=OSError('fixture retention failure')):
            with self.assertRaisesRegex(OSError,'retention failure'):self.prepare()
        self.assertEqual(path.read_bytes(),raw)
        self.assertFalse((self.new/'helene.json').exists())
        self.assertEqual(self.events,[])

    def test_unknown_preparation_is_not_treated_as_clean_orphan(self):
        path=updater.prepared_install_path(self.new)
        write(path,{'format':'unknown','folder_switch':False});raw=path.read_bytes()
        with self.assertRaisesRegex(ValueError,'in-place'):self.prepare()
        self.assertEqual(path.read_bytes(),raw)
        self.assertFalse((self.new/'.helene/recovery').exists())


if __name__=='__main__':unittest.main()
