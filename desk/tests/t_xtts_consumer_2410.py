"""Producer -> actual lazy audio facade -> agent speak, with a fake isolated XTTS client."""
from pathlib import Path
import os,sys,types,unittest,wave
from unittest.mock import patch
DESK=Path(__file__).resolve().parents[1];CORE=DESK.parent/'helene/core'
sys.path.insert(0,str(CORE))
import praxis_test  # real sandbox before importing the agent
sys.path.insert(0,str(DESK))
import agent,media_audio
from localharness import voice
from test_multimodal_regressions import RegressionBase

class XttsSpeak(RegressionBase):
 def exercise(self,mode):
  manifest={'status':'running'};calls=[];path=self.root/'xtts.wav'
  def synthesize(text,output_dir,*,stopped=None):
   calls.append((text,output_dir,stopped))
   self.assertFalse(stopped())
   if mode=='during':
    manifest.update(status='paused',control={'action':'cancel'})
    self.assertTrue(stopped());raise InterruptedError('cancelled in client')
   with wave.open(str(path),'wb') as out:
    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(b'\0\0'*80)
   if mode=='late':manifest.update(status='paused',control={'action':'cancel'})
   return path
  state={'ready':True,'why':'','speaker':'fixture','language':'en'}
  with patch.object(voice,'speech_state',return_value=state):env=voice.speech_env_for(self.root,{})
  with patch.dict(os.environ,env,clear=True),patch.dict(sys.modules,xtts_tts_client=types.SimpleNamespace(synthesize=synthesize)):
   backend=media_audio.MediaAudio(stt=types.SimpleNamespace(transcribe=lambda p:'words'))
   media_audio.set_default_backend(backend)
   self.assertEqual(backend.transcribe('unused'),'words');self.assertEqual(calls,[])
   staged=[];channel=agent._TURN_CHANNEL.set(self.owner_ctx);outbound=agent._TURN_OUTBOUND.set(staged)
   try:
    with patch.object(agent.run_context,'current_run',return_value=types.SimpleNamespace(run_id='fixture')),patch.object(agent,'_runs',return_value=types.SimpleNamespace(manifest=lambda rid:manifest)):
     result=agent.tool_speak('Full spoken text',caption='voice')
   finally:agent._TURN_CHANNEL.reset(channel);agent._TURN_OUTBOUND.reset(outbound)
  self.assertEqual(len(calls),1);self.assertEqual(calls[0][0],'Full spoken text');self.assertTrue(callable(calls[0][2]))
  if mode=='ok':
   self.assertEqual(len(staged),1);self.assertFalse(path.exists())
  else:
   self.assertEqual(staged,[]);self.assertIn('отменена',result);self.assertFalse(path.exists())
 def test_producer_facade_speak(self):self.exercise('ok')
 def test_cancel_during_synthesis(self):self.exercise('during')
 def test_cancel_after_client_return_never_stages(self):self.exercise('late')
 def test_xtts_warm_is_lazy(self):
  with patch.dict(os.environ,{'PRAXIS_TTS_BACKEND':'xtts'},clear=True),patch.dict(sys.modules,xtts_tts_client=types.SimpleNamespace(synthesize=lambda *a,**k:self.fail('warm must not synthesize'))):
   media_audio.set_default_backend(media_audio.MediaAudio(stt=types.SimpleNamespace(transcribe=lambda p:'ok')))
   self.assertEqual(media_audio.warm(stt=False,tts=True)['tts'],'lazy-isolated')

if __name__=='__main__':unittest.main()
