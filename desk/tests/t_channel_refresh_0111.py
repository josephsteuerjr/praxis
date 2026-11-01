"""Real child Jobs: changing only the phone channel keeps runner/relay births."""
import json,os,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from queue import Queue
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import desktop_supervisor as supervisor,owner_stop
from localharness.package_notice import read,write

class ChannelRefresh(unittest.TestCase):
    def test_only_channel_job_changes_when_ingress_changes(self):
        with tempfile.TemporaryDirectory(prefix='channel-refresh-') as directory:
            root=Path(directory);tree=root/'data';tree.mkdir();config=root/'helene.json';write(config,{'mode':'local','tree':str(tree)})
            stop=threading.Event();commands=Queue();nonce=uuid.uuid4().hex;phase=[0];errors=[]
            def plans(*args,**kwargs):
                return {'main':(tree,{f'main:{role}':supervisor.Spec(role,
                    (sys.executable,'-B','-c','import time;time.sleep(45)'),root,
                    dict(os.environ,HELENE_TEST_CHANNEL=str(phase[0] if role=='channel' else 0)),root/(role+'.log'),0)
                    for role in ('channel','runner','relay')})}
            def run():
                try:supervisor.run(config,stop=stop,desktop_roster=True,status_nonce=nonce,commands=commands)
                except BaseException as error:errors.append(error)
            def wait(predicate):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    value=read(tree/'.python-desktop-status')
                    if predicate(value):return value
                    if errors:raise errors[0]
                    time.sleep(.05)
                self.fail('Supervisor did not publish the expected process receipt')
            with patch.object(supervisor,'_plans',side_effect=plans),patch.object(owner_stop,'stopped',return_value=False):
                thread=threading.Thread(target=run);thread.start()
                try:
                    before=wait(lambda value:len(value.get('agents',{}).get('main',{}).get('pids',{}))==3)
                    phase[0]=1;command=uuid.uuid4().hex;commands.put(command)
                    after=wait(lambda value:value.get('ack')==command and len(value['agents']['main']['pids'])==3)
                    old=before['agents']['main']['pids'];new=after['agents']['main']['pids']
                    self.assertNotEqual(old['channel'],new['channel'])
                    self.assertEqual(old['runner'],new['runner']);self.assertEqual(old['relay'],new['relay'])
                finally:stop.set();thread.join(15)
                self.assertFalse(thread.is_alive());self.assertEqual(errors,[])

if __name__=='__main__':unittest.main()
