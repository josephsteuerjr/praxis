"""Private offline delivery and selected native ABI contracts; no model mocks claimed as inference."""
import hashlib,io,json,sys,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'installer'))
from localharness import component_downloads as cd,python_updates,voice,python_platform
import prepare_whisper as whisper

class Offline(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'Helene';(self.root/'app').mkdir(parents=True)
  b=io.BytesIO()
  with zipfile.ZipFile(b,'w') as z:z.writestr('Helene/voice/site-packages/probe.py','VALUE=1')
  self.data=b.getvalue();self.row={'name':'whisper.zip','sha256':hashlib.sha256(self.data).hexdigest(),'size':len(self.data),'offline_only':True}
  self.manifest={'format':cd.FORMAT,'target':python_platform.runtime_target(),'extensions':{'whisper':['whisper'],'xtts':[],'toolchain':[]},'artifacts':{'whisper':self.row},'unavailable':{'xtts':'not supplied in fixture','toolchain':'not supplied in fixture'}}
  (self.root/'app'/cd.MANIFEST).write_text(json.dumps(self.manifest))
  (self.root/'python-app.json').write_text(json.dumps({'version':'1.9.4','target':python_platform.runtime_target(),'extensions':{'whisper':{}}}))
 def test_empty_selection_is_successful_without_overlay_or_download(self):
  with patch.object(cd,'_overlay',wraps=cd._overlay),patch.object(python_updates,'_download',side_effect=AssertionError('network')):
   self.assertEqual(cd.prepare(self.root,[],offline=self.root.parent/'absent')['state'],'prepared')
   self.assertEqual(cd.install(self.root,[])['state'],'installed')
  self.assertFalse((self.root/'voice').exists())
  self.assertFalse(list(self.root.glob('.component-stage-*')))
 def test_offline_refuses_invented_url(self):
  self.row['url']='https://example.test/whisper.zip'
  with self.assertRaisesRegex(ValueError,'must not claim'):cd.validate(self.manifest)
 def test_cold_offline_requires_supplied_bytes_without_network(self):
  with patch.object(python_updates,'_download',side_effect=AssertionError('network')):
   with self.assertRaisesRegex(FileNotFoundError,'Offline component required'):cd.fetch(self.root,self.manifest,['whisper'])
 def test_real_prepare_then_late_install_and_corrupt_cache_recovery(self):
  source=self.root.parent/'extensions';source.mkdir();(source/'whisper.zip').write_bytes(self.data)
  cache=cd.cache_dir(self.root)/cd.cache_name(self.row);cache.write_bytes(b'broken')
  with patch.object(python_updates,'_download',side_effect=AssertionError('network')):
   cd.prepare(self.root,['whisper'],offline=source)
   self.assertEqual((self.root/'voice/site-packages/probe.py').read_text(),'VALUE=1')
   self.assertTrue(list(cache.parent.glob(cache.name+'.invalid-*')))
   self.assertTrue(cd.fetch(self.root,self.manifest,['whisper'])[0]['reused'])
   cd.install(self.root,['whisper'])
  self.assertEqual(cache.read_bytes(),self.data)
 def test_prepare_targets_spaced_program_name_not_sibling_helene(self):
  renamed=self.root.parent/'Working Folder'/'program';renamed.parent.mkdir();self.root.rename(renamed);self.root=renamed
  source=self.root.parent/'extensions';source.mkdir();(source/'whisper.zip').write_bytes(self.data)
  marker=self.root/'helene.json';marker.write_text('{"keep":"selected profile"}')
  cd.prepare(self.root,['whisper'],offline=source)
  self.assertEqual((self.root/'voice/site-packages/probe.py').read_text(),'VALUE=1')
  self.assertEqual(marker.read_text(),'{"keep":"selected profile"}')
  self.assertFalse((self.root.parent/'Helene').exists())

 def test_corrupt_offline_input_preserves_previous(self):
  source=self.root.parent/'extensions';source.mkdir();(source/'whisper.zip').write_bytes(b'bad')
  previous=self.root/'voice/site-packages/probe.py';previous.parent.mkdir(parents=True);previous.write_text('OLD')
  with self.assertRaisesRegex(ValueError,'Offline component differs'):cd.prepare(self.root,['whisper'],offline=source)
  self.assertEqual(previous.read_text(),'OLD')
 def test_real_voice_card_fetch_uses_offline_cache_and_refuses_missing(self):
  from localharness import python_platform
  (self.root/'python-app.json').write_text(json.dumps({'version':'1.9.4','target':python_platform.runtime_target(),'extensions':{'whisper':{}}}))
  with patch.dict('os.environ',{'HELENE_ROOT':str(self.root)},clear=True),patch.object(python_updates,'_download',side_effect=AssertionError('network')):
   with self.assertRaisesRegex(FileNotFoundError,'Offline component required'):voice.fetch_engine(self.root)
   target=cd.cache_dir(self.root)/cd.cache_name(self.row);target.write_bytes(self.data)
   result=voice.fetch_engine(self.root)
   self.assertEqual(result['sha256'],self.row['sha256'])
   self.assertEqual((self.root/'voice/site-packages/probe.py').read_text(),'VALUE=1')
   self.assertEqual(target.read_bytes(),self.data)
 def test_voice_never_guesses_url_for_offline_map(self):
  with patch.dict('os.environ',{},clear=True):self.assertEqual(voice._pack_url(self.root,self.row),'')

class ABI(unittest.TestCase):
 def test_selected_sites_and_version_not_builder(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);site=root/'lib/python3.13/site-packages';site.mkdir(parents=True)
   info={'python':'3.13','sites':[str(site)],'markers':{'python_version':'3.13','sys_platform':'linux'}}
   with patch.object(whisper.subprocess,'check_output',return_value=json.dumps(info)):
    got=whisper.runtime_info(root)
   self.assertEqual(got['python'],'3.13');self.assertIn(site.resolve(),got['sites'])
 def test_foreign_runtime_site_refused(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(whisper.subprocess,'check_output',return_value=json.dumps({'sites':['/outside']})):
   with self.assertRaisesRegex(ValueError,'outside'):whisper.runtime_info(Path(tmp))
 def test_native_record_closure_and_markers(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);site=root/'stage';site.mkdir();(site/'faster_whisper').mkdir();(site/'faster_whisper/__init__.py').write_text('VALUE=1')
   dist=site/'faster_whisper-1.0.dist-info';dist.mkdir();(dist/'METADATA').write_text('Metadata-Version: 2.1\nName: faster-whisper\nVersion: 1.0\nRequires-Dist: windows-only; sys_platform == "win32"\n')
   (dist/'RECORD').write_text('faster_whisper/__init__.py,,\nfaster_whisper-1.0.dist-info/METADATA,,\nfaster_whisper-1.0.dist-info/RECORD,,\n')
   with patch.object(whisper,'runtime_info',return_value={'python':'3.13','sites':[],'markers':{'sys_platform':'linux'}}):
    r=whisper.build(root,root/'voice.zip',site_packages=site)
   self.assertEqual(r['python'],'3.13');self.assertEqual(r['packages'],{'faster-whisper':'1.0'})

if __name__=='__main__':unittest.main(verbosity=2)
