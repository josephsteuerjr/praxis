"""Only the three concrete consumer failures from corrective review."""
from pathlib import Path
import json,os,subprocess,sys,tempfile,threading,unittest
from unittest.mock import patch
DESK=Path(__file__).resolve().parents[1];sys.path.insert(0,str(DESK))
from localharness import component_downloads,python_platform,darwin_process
from localharness.python_controller import Controller


class Consumers(unittest.TestCase):
 def test_runner_style_bare_voice_and_package_manifest_consumers(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'app').mkdir()
   value={'format':component_downloads.FORMAT,'target':python_platform.runtime_target(),'extensions':{n:[] for n in component_downloads.EXTENSIONS},'artifacts':{},'unavailable':{n:'No qualified target payload' for n in component_downloads.EXTENSIONS}}
   (root/'app/component-downloads.json').write_text(json.dumps(value));(root/'python-app.json').write_text(json.dumps({'target':python_platform.runtime_target()}))
   code="import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import voice,component_downloads;voice.install_root=lambda:Path(sys.argv[2]);v=voice.speech_library();assert not v['downloadable'];component_downloads.load(Path(sys.argv[2]));print('BARE_VOICE_MANIFEST_PASS')"
   result=subprocess.run([sys.executable,'-I','-c',code,str(DESK/'localharness'),str(root)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr);self.assertIn('BARE_VOICE_MANIFEST_PASS',result.stdout)
   self.assertEqual(component_downloads.load(root),value)

 def test_documented_profile_script_reaches_actual_path_validation(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);old=root/'offline';new=root/'new';old.mkdir();new.mkdir()
   (new/'python-app.json').write_text(json.dumps({'target':python_platform.runtime_target()}))
   (root/'map.json').write_text('{}')
   # A deliberately incomplete offline source must fail in migration discovery,
   # not in relative import. Real preserved-profile tests cover the copy itself.
   result=subprocess.run([sys.executable,'-I',str(DESK/'localharness/profile_paths.py'),'--offline-copy',str(old),'--new',str(new),'--data-home',str(root/'data'),'--path-map',str(root/'map.json')],capture_output=True,text=True)
   self.assertNotIn('ImportError',result.stderr)
   self.assertIn('app_migration',result.stderr)

 def test_actual_controller_helper_json_survives_darwin_terminal_diagnostic(self):
  with tempfile.TemporaryDirectory() as tmp:
   ctrl=Controller.__new__(Controller);ctrl.helpers=[]
   def background(argv,log,env=None):
    log.write_text('{"ok":true,"identity":"fixture"}\n',encoding='utf-8')
    class Child:
     handle=object()
     def poll(self):
      with patch.object(darwin_process._posix,'_emit'):
       darwin_process.report(log,{'state':'empty','code':0,'evidence':{'scope':'fixture'}})
      self.handle=None
      return 0
     def stop(self):self.handle=None
    child=Child();ctrl.helpers.append(child);return child
   ctrl.background=background
   result=ctrl.helper(['synthetic-json'])
   self.assertEqual(result,{'ok':True,'identity':'fixture'})
   self.assertEqual(ctrl.helpers,[])


if __name__=='__main__':unittest.main(verbosity=2)
