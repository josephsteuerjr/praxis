"""Real incoming ZIP/map/cache/overlay consumers with tiny pinned payloads."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import component_downloads as cd, python_updates as updates

class Components(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'Helene';self.root.mkdir()
        self.blobs={};artifacts={}
        for name in ('toolchain','xtts','whisper'):
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as z:z.writestr('Helene/'+('voice' if name=='whisper' else 'components/'+name)+'/fixture',name)
            data=buf.getvalue();url='https://example.test/v1.9.2/'+name+'.zip';self.blobs[url]=data
            artifacts[name]={'name':name+'.zip','url':url,'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
        self.manifest={'format':cd.FORMAT,'extensions':{s:[s] for s in artifacts},'artifacts':artifacts}
        (self.root/'app').mkdir();(self.root/'app'/cd.MANIFEST).write_text(json.dumps(self.manifest))
        self.calls=[]
        class Response(io.BytesIO):
            @property
            def headers(self):return {'Content-Length':str(len(self.getvalue()))}
        outer=self
        class HTTP:
            def open(self,url,timeout=None):outer.calls.append(url);return Response(outer.blobs[url])
        self.mock=patch.object(updates,'download_http',return_value=HTTP());self.mock.start();self.addCleanup(self.mock.stop)
    def test_all_selections_fetch_then_reuse_without_payload_network(self):
        for selection in ([],['xtts'],['whisper'],['xtts','whisper']):
            cd.fetch(self.root,self.manifest,selection)
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(r['reused'] for r in cd.fetch(self.root,self.manifest,list(cd.EXTENSIONS))))
        self.assertEqual(len(self.calls),2)
    def test_toolchain_is_not_a_downloadable_extension(self):
        with self.assertRaises(ValueError):cd.select(self.manifest,['toolchain'])

    def test_corrupt_cache_is_preserved_and_retry_repairs(self):
        row=cd.select(self.manifest,['whisper'])[0];p=cd.cache_dir(self.root)/cd.cache_name(row);p.write_bytes(b'bad')
        cd.fetch(self.root,self.manifest,['whisper'])
        self.assertTrue(list(p.parent.glob(p.name+'.invalid-*')))
        self.assertEqual(hashlib.sha256(p.read_bytes()).hexdigest(),row['sha256'])
    def test_cache_survives_moved_program_and_staging_retirement(self):
        cd.fetch(self.root,self.manifest,['whisper']);moved=self.root.parent/'Moved';moved.mkdir()
        cd.preserve_cache(self.root,moved)
        import shutil
        shutil.rmtree(self.root)
        rows=cd.fetch(moved,self.manifest,['whisper'])
        self.assertTrue(rows[0]['reused']);self.assertEqual(len(self.calls),1)
    def test_incoming_map_is_required_for_new_packages(self):
        archive=self.root.parent/'incoming.zip'
        with zipfile.ZipFile(archive,'w') as z:z.writestr('Helene/python-app.json',json.dumps({'version':'1.9.3'}))
        with self.assertRaisesRegex(ValueError,'lacks'):cd.incoming(archive)
        with zipfile.ZipFile(archive,'a') as z:z.writestr('Helene/app/'+cd.MANIFEST,json.dumps(self.manifest))
        self.assertEqual(cd.incoming(archive),self.manifest)
    def test_xtts_failure_rolls_back_overlay_and_retains_archives(self):
        previous=self.root/'components/xtts/fixture';previous.parent.mkdir(parents=True);previous.write_text('previous')
        with patch('localharness.xtts_install.install',side_effect=OSError('model conflict')):
            with self.assertRaisesRegex(OSError,'model conflict'):cd.install(self.root,['xtts'],tree=self.root/'agent')
        self.assertEqual(previous.read_text(),'previous');self.assertTrue(list(cd.cache_dir(self.root).glob('*.zip')))
    def test_missing_xtts_tree_refuses_before_download_or_overlay(self):
        with self.assertRaisesRegex(ValueError,'Select'):cd.install(self.root,['xtts'])
        self.assertEqual(self.calls,[])
    def test_wrong_hash_keeps_installed_bytes(self):
        row=self.manifest['artifacts']['whisper'];self.blobs[row['url']]=b'not-the-archive'
        with self.assertRaisesRegex(ValueError,'SHA256'):cd.install(self.root,['whisper'])
        self.assertFalse((self.root/'components/toolchain').exists())

    def test_cached_prepare_reads_archive_hash_once_and_moves_extracted_payload(self):
        cd.fetch(self.root,self.manifest,['whisper'])
        hashed=[];original=hashlib.file_digest
        def digest(stream,*args,**kwargs):
            hashed.append(Path(stream.name));return original(stream,*args,**kwargs)
        import shutil
        original_copy=shutil.copy2
        def copy(source,*args,**kwargs):
            if '.component-stage-' in str(source):raise AssertionError('Second full payload copy')
            return original_copy(source,*args,**kwargs)
        with patch.object(hashlib,'file_digest',side_effect=digest),patch.object(shutil,'copy2',side_effect=copy):
            cd.prepare(self.root,['whisper'])
        self.assertEqual((self.root/'voice/fixture').read_text(),'whisper')
        self.assertEqual(hashed,[(cd.cache_dir(self.root)/cd.cache_name(self.manifest['artifacts']['whisper'])).resolve()])
        self.assertEqual(len(self.calls),1)

    def test_ordinary_update_preserves_unchanged_pin_but_changed_pin_is_fetched(self):
        payload=self.root/'voice/site-packages/engine.py';payload.parent.mkdir(parents=True);payload.write_text('installed owner copy')
        self.assertEqual(cd.changed_extensions(self.root,self.manifest,['whisper']),[])
        changed=json.loads(json.dumps(self.manifest));changed['artifacts']['whisper']['sha256']='a'*64
        self.assertEqual(cd.changed_extensions(self.root,changed,['whisper']),['whisper'])
        self.assertEqual(cd.changed_extensions(self.root,self.manifest,['xtts']),['xtts'])
        self.assertEqual(payload.read_text(),'installed owner copy')

    def test_first_migration_keeps_installed_components_without_network(self):
        for name in ('components/toolchain/compiler','voice/site-packages/engine.py','voice-xtts/python.exe'):
            path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(name)
        target=self.root.parent/'Migrated'
        cd.preserve_installed(self.root,target)
        for path in self.root.rglob('*'):
            if path.is_file() and path.parts[-2]!='app':self.assertEqual((target/path.relative_to(self.root)).read_bytes(),path.read_bytes())
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
