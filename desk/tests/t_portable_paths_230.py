"""Actual portable-folder move, external overrides and durable data identity."""
from pathlib import Path
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import portable_profile as portable, windows_installation as setup
from localharness.package_notice import read,write


class PortablePaths(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='helene-portable-test-');self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.root=self.base/'program with spaces';self.root.mkdir()
        self.data=self.base/'existing data';self.data.mkdir()

    def config(self,tree=None):
        value={'mode':'local','tree':str(tree or self.data),'code':str(self.root/'code/authored'),
            'python':str(self.root/'runtime/python.exe'),'app':str(self.root/'app/deskapp.py'),
            'runner':str(self.root/'app/localharness/runner.py'),
            'backup':{'dir':str(self.base/'external backups')},
            'relay':{'executable':str(self.root/'relay-product/praxis-relay.exe')}}
        write(self.root/'helene.json',value)

    def test_real_folder_move_keeps_profile_and_only_named_internal_paths_change(self):
        self.config();first=portable.bind_program(self.root)
        config=read(self.root/'helene.json')
        self.assertFalse(Path(config['code']).is_absolute());self.assertEqual(config['tree'],str(self.data))
        moved=self.base/'moved program';self.root.rename(moved)
        second=portable.bind_program(moved)
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(first['profiles']['helene.json']['id'],second['profiles']['helene.json']['id'])
        value=read(moved/'helene.json')
        self.assertEqual((moved/value['code']).resolve(),moved/'code/authored')
        self.assertEqual(value['backup']['dir'],str(self.base/'external backups'))
        self.assertEqual(value['relay']['executable'],'relay-product/praxis-relay.exe')

    def test_missing_pointer_or_missing_old_data_never_creates_new_identity(self):
        self.config();portable.bind_program(self.root)
        (self.root/'helene.json').unlink()
        with self.assertRaisesRegex(ValueError,'pointer is missing'):portable.bind_program(self.root)
        self.assertFalse((self.root/'helene.json').exists())
        self.config();self.data.rename(self.base/'temporarily missing')
        with self.assertRaisesRegex(ValueError,'unavailable'):portable.bind_program(self.root)
        self.assertFalse(self.data.exists())

    def test_two_agents_cannot_accidentally_share_one_writable_profile(self):
        self.config();write(self.root/'agents/second/helene.json',{'mode':'local','tree':str(self.data),'code':'../../tree'})
        with self.assertRaisesRegex(ValueError,'same writable profile'):portable.bind_program(self.root)

    def test_new_default_is_home_Helene_existing_default_is_untouched(self):
        with patch.object(Path,'home',return_value=self.base):
            self.assertEqual(setup.default_data(None),str(self.base/'Helene'))
        self.config();self.assertEqual(setup.default_data(self.root),str(self.data))
        value=setup.blank_config(self.root,self.data)
        for key in ('code','python','app','runner'):self.assertFalse(Path(value[key]).is_absolute())


if __name__=='__main__':unittest.main()
