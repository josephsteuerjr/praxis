"""Full selected source bytes, explicit omissions, stable cold snapshot."""
import json,os,subprocess,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import carry_payload as payload


class Payload(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.data.mkdir()
        self.project=self.root/'project';self.project.mkdir();self.archive=self.root/'payload.zip'
    def link(self,path,target):
        if os.name=='nt':subprocess.run(['cmd','/c','mklink','/J',str(path),str(target)],check=True,capture_output=True)
        else:path.symlink_to(target,target_is_directory=True)

    def test_large_source_logs_git_and_empty_directories_are_not_silently_filtered(self):
        blob=b'0123456789abcdef'*(600*1024) # Above the legacy 8 MiB code limit.
        (self.project/'model-source.bin').write_bytes(blob);(self.project/'input.log').write_bytes(b'project data')
        (self.project/'.git/objects').mkdir(parents=True);(self.project/'.git/objects/owned').write_bytes(b'git bytes')
        (self.project/'empty').mkdir();(self.data/'memory').mkdir()
        receipt=payload.create(self.archive,{'data':self.data,'project':self.project})
        checked=payload.inspect(self.archive);self.assertTrue(checked['integrity']['verified'])
        self.assertEqual(receipt['roots'],['data','project'])
        with zipfile.ZipFile(self.archive) as archive:
            self.assertEqual(archive.read('roots/project/model-source.bin'),blob)
            self.assertEqual(archive.read('roots/project/input.log'),b'project data')
            self.assertIn('roots/project/empty/',archive.namelist())
            self.assertIn('roots/project/.git/objects/owned',archive.namelist())

    def test_external_link_requires_selected_root_then_has_portable_reference(self):
        (self.project/'work').write_bytes(b'owned');self.link(self.data/'project',self.project)
        with self.assertRaisesRegex(ValueError,'пределы выбранных'):payload.create(self.archive,{'data':self.data})
        self.assertFalse(self.archive.exists())
        payload.create(self.archive,{'data':self.data,'project':self.project})
        checked=payload.inspect(self.archive)
        self.assertEqual(checked['links'],[{'root':'data','path':'project','target':{'root':'project','path':'.','directory':True}}])
        with zipfile.ZipFile(self.archive) as archive:self.assertNotIn('roots/data/project/work',archive.namelist())

    def test_omissions_are_named_and_link_to_omitted_content_is_not_accepted(self):
        (self.data/'host').mkdir();(self.data/'host/token').write_bytes(b'host-only')
        excluded=[{'root':'data','path':'host','reason':'machine channel custody'}]
        payload.create(self.archive,{'data':self.data},exclusions=excluded)
        self.assertEqual(payload.inspect(self.archive)['exclusions'],excluded)
        with zipfile.ZipFile(self.archive) as archive:self.assertNotIn('roots/data/host/token',archive.namelist())
        self.link(self.data/'alias',self.data/'host')
        with self.assertRaises(ValueError):payload.create(self.archive,{'data':self.data},exclusions=excluded)

    def test_addition_during_snapshot_refuses_and_keeps_previous_archive(self):
        (self.data/'file').write_bytes(b'original');self.archive.write_bytes(b'previous snapshot')
        original=zipfile.ZipFile.open;data=self.data
        def changing(archive,name,mode='r',**kwargs):
            if mode=='w' and getattr(name,'filename','')=='roots/data/file':(data/'appeared').write_bytes(b'new')
            return original(archive,name,mode,**kwargs)
        with mock.patch.object(zipfile.ZipFile,'open',changing):
            with self.assertRaisesRegex(ValueError,'изменилось'):payload.create(self.archive,{'data':self.data})
        self.assertEqual(self.archive.read_bytes(),b'previous snapshot')
        self.assertFalse(list(self.root.glob('payload.zip.part-*')))

    def test_archive_cannot_include_itself_or_publish_unexplained_exclusions(self):
        with self.assertRaises(ValueError):payload.create(self.data/'inside.zip',{'data':self.data})
        with self.assertRaises(ValueError):payload.create(self.archive,{'data':self.data},exclusions=[{'root':'data','path':'memory'}])

    def test_staging_preserves_source_and_rebinds_directory_link_inside_new_home(self):
        (self.data/'memory').mkdir();(self.data/'memory/story').write_bytes(b'new life')
        (self.project/'work').write_bytes(b'project bytes');self.link(self.data/'project',self.project)
        archive=payload.create(self.archive,{'data':self.data,'project':self.project})
        incoming=self.root/'returned';receipt=payload.stage(self.archive,incoming,expected_sha256=archive['sha256'])
        self.assertFalse(receipt['activated']);self.assertTrue(receipt['prepared'])
        target=Path(receipt['roots']['data']);self.assertEqual((target/'project/work').read_bytes(),b'project bytes')
        self.assertEqual((target/'project').resolve(),Path(receipt['roots']['project']))
        (target/'project/work').write_bytes(b'changed on new host')
        self.assertEqual((self.project/'work').read_bytes(),b'project bytes')
        with self.assertRaises(ValueError):payload.stage(self.archive,incoming,expected_sha256=archive['sha256'])
        with self.assertRaises(ValueError):payload.stage(self.archive,self.root/'wrong',expected_sha256='0'*64)
        self.assertFalse((self.root/'wrong').exists())


if __name__=='__main__':unittest.main()
