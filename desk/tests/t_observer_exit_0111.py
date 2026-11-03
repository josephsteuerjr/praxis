"""An explicit session Exit closes every observer without closing on crashes."""
from pathlib import Path
import json,sys,tempfile,threading,unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import session_exit,session_host
import python_app

class Exit(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);base=Path(self.tmp.name)
  self.root=base/'Program';self.root.mkdir();self.data=base/'Profile'
  (self.root/'helene.json').write_text(json.dumps({'tree':str(self.data)}))
  self.descriptor={'program':str(self.root.resolve()),'tree':str(self.data),'pid':4321,'birth':12.5,'generation':'fixture-generation'}
 def test_exact_terminal_intent_closes_but_another_birth_or_generation_does_not(self):
  session_exit.record(self.root,self.descriptor);self.assertTrue(session_exit.matches(self.root,self.descriptor))
  for key,value in (('pid',9876),('birth',99.5),('generation','another-generation')):
   self.assertFalse(session_exit.matches(self.root,{**self.descriptor,key:value}))
 def test_profile_intent_survives_a_missing_programme_marker(self):
  session_exit.record(self.root,self.descriptor);session_exit.path(self.root).unlink()
  self.assertTrue(session_exit.matches(self.root,self.descriptor))
 def test_malformed_intent_blocks_automatic_start_but_does_not_close_a_window(self):
  p=session_exit.path(self.root);p.parent.mkdir();p.write_text('{"format":"unknown"}')
  self.assertTrue(session_exit.requested(self.root));self.assertFalse(session_exit.matches(self.root,self.descriptor))
 def observe_failure(self):
  client=session_host.SessionClient.__new__(session_host.SessionClient);client.root=self.root;client.descriptor=self.descriptor
  client._detached=threading.Event();client._cursor=0;client.observer='fixture-observer';client.event=Mock();client._request=Mock(side_effect=OSError('owner control closed'))
  client._observe();return client.event
 def test_lost_connection_after_explicit_exit_is_terminal(self):
  session_exit.record(self.root,self.descriptor)
  self.observe_failure().assert_called_once_with('session-exited',{'generation':'fixture-generation'})
 def test_unexpected_owner_loss_keeps_the_window_for_diagnosis(self):
  event=self.observe_failure();self.assertEqual(event.call_args.args[0],'session-unavailable')
 def test_hidden_observer_closes_without_broadcasting_another_exit(self):
  app=python_app.Application(self.root,self.root)
  try:
   done=threading.Event();app.window=Mock();app.window.destroy.side_effect=done.set
   app.event('session-exited',{'generation':'fixture-generation'})
   self.assertTrue(done.wait(3));self.assertTrue(app.exiting);self.assertFalse(app.exit_session)
   self.assertTrue(app.stopping.is_set());self.assertEqual(app.events,[])
  finally:app.close()

if __name__=='__main__':unittest.main()
