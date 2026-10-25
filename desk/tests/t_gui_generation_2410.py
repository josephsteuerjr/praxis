"""Specific GUI-init/environment and Linux TIME_WAIT generation regressions."""
from pathlib import Path
import json,os,sys,socket,tempfile,threading,time,unittest
from contextlib import contextmanager
from queue import Queue
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import desktop_supervisor as ds

@contextmanager
def hold(_tree):yield lambda:None

class Generation(unittest.TestCase):
 def fixture(self,root):
  tree=root/'data';tree.mkdir();app=root/'channel.py';app.write_text('pass\n')
  cfg=root/'helene.json';cfg.write_text(json.dumps({'mode':'local','tree':'data','python':sys.executable,'app':'channel.py','setup_complete':False,'port':18094}))
  return cfg
 def test_actual_refresh_does_not_retire_for_gui_environment_mutation(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(ds.owner_stop,'stopped',return_value=False),patch.object(ds,'_tree_hold',hold),patch.object(ds,'_port_busy',return_value=False):
   cfg=self.fixture(Path(tmp));stop=threading.Event();commands=Queue();raised=threading.Event();acked=threading.Event();children=[];faults=[]
   class Child:
    def __init__(self):self.handle=True;self.pid=100+len(children);self.stops=0
    @classmethod
    def spawn(cls,*a,**kw):
     child=cls();children.append(child);raised.set();return child
    def poll(self):return None
    def request_stop(self):pass
    def stop(self):self.stops+=1;self.handle=None
   def status(*args):
    if args[3]=='refresh':acked.set()
   def run():
    try:ds.run(cfg,stop=stop,desktop_roster=True,status_nonce='a'*32,commands=commands)
    except BaseException as exc:faults.append(exc)
   with patch.object(ds,'JobChild',Child),patch.object(ds,'_desktop_status',status),patch.dict(os.environ,{'QT_STYLE_OVERRIDE':'before'}):
    worker=threading.Thread(target=run);worker.start()
    try:
     self.assertTrue(raised.wait(5));os.environ['QT_STYLE_OVERRIDE']='after-qt-import';commands.put('refresh');self.assertTrue(acked.wait(5))
     self.assertEqual(len(children),1);self.assertEqual(children[0].stops,0)
    finally:stop.set();worker.join(5)
   self.assertFalse(worker.is_alive());self.assertEqual(faults,[])
 def test_explicit_config_still_changes_desired_spec(self):
  with tempfile.TemporaryDirectory() as tmp:
   cfg=self.fixture(Path(tmp));ambient=dict(os.environ)
   before=ds._plans(cfg,session_host=False,environ=ambient)
   value=json.loads(cfg.read_text());value['port']=18095;cfg.write_text(json.dumps(value))
   after=ds._plans(cfg,session_host=False,environ=ambient)
   self.assertNotEqual(before,after)
 @unittest.skipUnless(sys.platform.startswith('linux'),'Linux bind/TIME_WAIT consumer')
 def test_time_wait_is_free_but_live_listener_remains_busy(self):
  server=socket.socket();server.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);server.bind(('127.0.0.1',0));port=server.getsockname()[1];server.listen()
  client=socket.create_connection(('127.0.0.1',port));peer,_=server.accept()
  try:
   self.assertTrue(ds._port_busy(port));peer.shutdown(socket.SHUT_WR);self.assertEqual(client.recv(1),b'');client.close();peer.close();server.close()
   self.assertFalse(ds._port_busy(port))
  finally:client.close();peer.close();server.close()

if __name__=='__main__':unittest.main()
