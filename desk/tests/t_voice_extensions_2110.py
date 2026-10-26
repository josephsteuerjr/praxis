"""Voice catalogue/preview seams; no model, network or owner installation."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import voice
from localharness import xtts_install
from localharness.app_components import digest


class VoiceExtensionsTests(unittest.TestCase):
    def test_dead_ready_file_is_not_a_running_worker(self):
        import os
        self.assertFalse(voice.worker_live(Path('unused'),{'ready':True,'pid':0}))
        self.assertFalse(voice.worker_live(Path('unused'),{'ready':True,'pid':os.getpid()}))

    def test_packaged_runtime_is_shared_and_missing_model_metadata_is_added(self):
        with tempfile.TemporaryDirectory() as temp:
            program=Path(temp)/'program';bundle=program/'components/xtts';tree=Path(temp)/'data'
            for relative in ('runtime/python.exe','runtime/bin/python3','model/model.pth','model/voices.json'):
                path=bundle/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(relative)
            files={p.relative_to(bundle).as_posix():digest(p) for p in bundle.rglob('*') if p.is_file()}
            (bundle/'xtts-bundle.json').write_text(json.dumps({'format':'helene-xtts.v1','files':files}))
            model=tree/'models/xtts';model.mkdir(parents=True);(model/'model.pth').write_text('model/model.pth')
            xtts_install.install(tree,program=program)
            self.assertFalse((program/'voice-xtts').exists())
            self.assertEqual(xtts_install.runtime_for(program),bundle/'runtime')
            self.assertTrue((model/'voices.json').is_file())
            xtts_install.install(tree,program=program)
            (model/'model.pth').write_text('authored different model')
            with self.assertRaises(FileExistsError):xtts_install.install(tree,program=program)

    def ready(self,root):
        live=patch.object(voice,'worker_live',return_value=True);live.start();self.addCleanup(live.stop)
        home=root/'memory/.state/xtts';home.mkdir(parents=True)
        (home/'ready.json').write_text(json.dumps({'schema':2,'pid':123,'ready':True,'generation':'a'*32,'speakers':['A','B'],'languages':['ru','en']}))
        return home

    def test_catalogue_is_model_metadata_not_hardcoded_names(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);model=root/'models/xtts';model.mkdir(parents=True)
            (model/'voices.json').write_text(json.dumps({'speakers':['A','B'],'languages':['ru','en']}))
            state=voice.speech_state(root,{'voice':{'tts_speaker':'B','tts_language':'en'}})
            self.assertEqual(state['speakers'],['A','B'])
            self.assertEqual(state['speaker'],'B')
            self.assertEqual(state['languages'],['ru','en'])

    def test_preview_rejects_unknown_voice_without_inbox_write(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=self.ready(root)
            with self.assertRaisesRegex(ValueError,'каталога'):
                voice.preview(root,{'voice':{'speak':True}}, {'speaker':'invented','language':'ru'})
            self.assertFalse((home/'inbox').exists())

    def test_preview_receives_worker_audio_then_retires_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=self.ready(root)
            def worker():
                end=time.monotonic()+3
                while time.monotonic()<end:
                    paths=list((home/'inbox').glob('*.json'))
                    if paths:break
                    time.sleep(.01)
                else:return
                request=paths[0];identity=request.stem;body=json.loads(request.read_text())
                self.assertEqual(body['speaker'],'B')
                output=home/'output';output.mkdir()
                path=output/(identity+'.wav')
                with wave.open(str(path),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\x01\x00'*240)
                (output/(identity+'.json')).write_text(json.dumps({'ok':True,'request_id':identity,'generation':body['generation'],'format':'wav','path':str(path)}))
                request.unlink()
            thread=threading.Thread(target=worker);thread.start()
            result=voice.preview(root,{'voice':{'speak':True}}, {'speaker':'B','language':'en','text':'Test'},timeout=3)
            thread.join(3)
            self.assertEqual(result['mime'],'audio/wav')
            self.assertGreater(len(result['base64']),100)
            self.assertFalse(list((home/'output').iterdir()))

    def test_abandoned_preview_marks_only_its_request_cancelled(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);home=self.ready(root)
            with self.assertRaises(TimeoutError):
                voice.preview(root,{'voice':{'speak':True}}, {'speaker':'A','language':'ru'},timeout=.01)
            self.assertEqual(list((home/'inbox').glob('*.json')),[])
            self.assertEqual(len(list((home/'inbox').glob('*.cancel'))),1)

if __name__=='__main__':unittest.main()
