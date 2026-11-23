"""Passive extraction, ready-only admission, tamper rejection and long Windows paths."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import prepared_update as ready, python_updates, parallel_files, python_platform
from localharness.windows_paths import Path as FilePath, wide, walk


class Prepared(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(lambda:shutil.rmtree(wide(self.temp.name)) if os.path.isdir(self.temp.name) else None)
        self.root=FilePath(self.temp.name)/'app'
        self.root.mkdir()
        self.config=self.root/'helene.json'
        self.config.write_text('{}')
        self.owner=SimpleNamespace(root=self.root,current=lambda:SimpleNamespace(config=self.config))
        self.target=python_platform.runtime_target()
        meta={'target':self.target,'format':'helene-python-app.v1','version':'2.9.0'}
        (self.root/'python-app.json').write_text(json.dumps(meta))
        self.name=python_platform.asset_name('2.9.2',self.target,python_platform.distribution(self.root))
        self.archive=FilePath(self.temp.name)/self.name
        self.deep='/'.join(['deep_segment_'+'x'*36]*7)+'.py'
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('Helene/python-app.json',json.dumps(dict(meta,version='2.9.2')))
            z.writestr('Helene/app/python_app.py','print(1)')
            z.writestr('Helene/tree/'+self.deep,b'long path data')
        self.sha=hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.url='https://example.test/'+self.name
        release={'newer':True,'latest':'2.9.2','url':self.url,'sha256':self.sha}
        feed=patch.object(python_updates,'update_check',return_value=release)
        self.feed=feed.start();self.addCleanup(feed.stop)
        downloader=patch.object(python_updates,'update_download',return_value={'path':str(self.archive),'sha256':self.sha,'components':[]})
        self.get=downloader.start();self.addCleanup(downloader.stop)
        self.addCleanup(ready._states.pop,self.root,None)

    def prepare(self):
        ready.status(self.owner)
        ready._states[self.root]['thread'].join(20)
        return ready.status(self.owner)

    def test_preparation_is_passive_and_click_is_bound_to_version(self):
        with patch.object(python_updates,'_prepare_handoff',side_effect=AssertionError('unexpected install')):
            result=self.prepare()
        self.assertEqual(result.get('state'),'ready',result)
        self.assertEqual(ready.restart_args(self.owner,self.sha),dict(url=self.url,sha256=self.sha,sourceMode='clean'))
        with self.assertRaises(ValueError):ready.restart_args(self.owner,'0'*64)
        destination=self.root.parent/'job';destination.mkdir()
        self.assertTrue(ready.take(self.root,destination,[self.archive],self.sha))
        self.assertEqual((destination/'Helene/tree'/self.deep).read_bytes(),b'long path data')
        self.assertFalse(ready.take(self.root,destination,[self.archive],self.sha))
        self.assertEqual(self.get.call_count,1)

    def test_extra_file_or_modified_payload_cannot_be_handed_off(self):
        for extra in (False,True):
            with self.subTest(extra=extra):
                ready._states.pop(self.root,None)
                self.assertEqual(self.prepare()['state'],'ready')
                stage=ready._states[self.root]['cache']/'Helene'
                (stage/('other.py' if extra else 'app/python_app.py')).write_bytes(b'changed')
                destination=self.root.parent/('job'+str(extra));destination.mkdir()
                with self.assertRaises(ValueError):ready.take(self.root,destination,[self.archive],self.sha)

    def test_opt_out_and_failed_download_never_offer_restart(self):
        self.config.write_text('{"update":{"auto_prepare":false}}')
        self.assertEqual(ready.status(self.owner)['state'],'disabled');self.get.assert_not_called()
        self.config.write_text('{}');self.get.side_effect=OSError('temporary failure')
        self.assertEqual(self.prepare()['state'],'error')
        with self.assertRaises(ValueError):ready.restart_args(self.owner,self.sha)

    def test_component_length_and_nested_paths(self):
        archive=self.root/'long.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('Helene/'+self.deep+'/'+('z'*242),b'stock')
        out=self.root/'extract';parallel_files.extract(archive,out)
        target=out/'Helene'/self.deep/('z'*242)
        self.assertGreater(len(str(target)),260)
        self.assertEqual(target.read_bytes(),b'stock')
        self.assertIn(target.name,[n for _,_,names in walk(out) for n in names])
        if os.name=='nt':
            self.assertTrue(str(wide(target)).startswith(chr(92)*2+'?'))
