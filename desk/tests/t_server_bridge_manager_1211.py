"""Profile route sharing, stale bindings and verification without live displacement."""
import copy,json,sys,tempfile,threading,types,unittest
from pathlib import Path
from unittest import mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness import server_bridge as bridge,server_connections as connections,agents


class Runtime:
    instances=[]
    def __init__(self,raw,configuration,**kwargs):
        self.ports=configuration;self.stopped=False;self.started=False;self.kwargs=kwargs;self.__class__.instances.append(self)
    def start(self):self.started=True
    def stop(self):self.stopped=True
    def state(self):return {'state':'connected','message':'fixture'}


class Manager(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.folder=Path(folder.name)
        self.patches=[]
        def patch(*args,**kwargs):
            item=mock.patch.object(*args,**kwargs);self.patches.append(item);return item.start()
        self.addCleanup(lambda:[item.stop() for item in reversed(self.patches)])
        patch(connections,'root',return_value=self.folder);patch(connections,'resolve',return_value={'fixture':True})
        patch(bridge,'Bridge',Runtime);Runtime.instances=[]
        self.rows=[];patch(agents,'roster',side_effect=lambda root:self.rows)
        patch(agents,'read_config',side_effect=lambda path:self.configs[path])
        self.configs={};self.identity='a'*32
        self.definition={'format':bridge.FORMAT,'id':self.identity,'name':'Fixture','server_profile':'b'*32,
                         'external':'https://fixture.example.org','ports':{'local_proxy_port':18888,'remote_proxy_port':8888,'local_app_port':18765,'remote_app_port':18766}}
        bridge.path(self.identity).parent.mkdir(parents=True);bridge.path(self.identity).write_text(json.dumps(self.definition))
        bridge.install()
        self.owner=types.SimpleNamespace(root=self.folder,lock=threading.RLock())
        self.manager=bridge.Manager(self.owner);self.owner.server_bridges=self.manager
        self.owner.current=lambda:self.rows[0];self.addCleanup(self.manager.close)
    def agent(self,number,*,proxy=True,phone=False):
        agent=types.SimpleNamespace(port=18765+number,config=str(number),enabled=True,conflict=False,tree=self.folder/str(number))
        cfg={}
        if proxy:cfg['network']={'bridge_profile':self.identity,'proxy':{'mode':'proxy','protocol':'http','host':'127.0.0.1','port':'18888'}}
        if phone:cfg['phone']={'bridge_profile':self.identity,'mode':'stable','enabled':True,'external':self.definition['external']}
        self.rows.append(agent);self.configs[agent.config]=cfg;return agent
    def test_proxy_is_shared_and_one_agent_can_add_its_https(self):
        self.agent(0,phone=True);self.agent(1);self.manager.sync()
        self.assertFalse(self.manager.errors);self.assertEqual(len(Runtime.instances),1)
        runtime=Runtime.instances[0];self.assertEqual(runtime.ports['local_app_port'],18765)
        self.configs['0']['phone']['enabled']=False;self.manager.sync()
        self.assertTrue(runtime.stopped);self.assertNotIn('local_app_port',Runtime.instances[-1].ports)
    def test_two_phone_destinations_refuse_to_retarget_live_bridge(self):
        self.agent(0,phone=True);self.manager.sync();runtime=Runtime.instances[0]
        self.agent(1,phone=True);self.manager.sync()
        self.assertIn(self.identity,self.manager.errors);self.assertTrue(runtime.stopped)
        self.assertEqual(len(Runtime.instances),1)
    def test_manually_changed_routes_do_not_leave_stale_hidden_tunnel(self):
        self.agent(0);self.manager.sync();runtime=Runtime.instances[0]
        self.configs['0']['network']['proxy']['host']='other.example.org';self.manager.sync()
        self.assertTrue(runtime.stopped);self.assertIn('прокси',self.manager.errors[self.identity])
    def test_check_adds_only_missing_forward_and_retains_active_proxy(self):
        self.agent(0);self.manager.sync();runtime=Runtime.instances[0];seen=[]
        def verify(current,value,agent):
            seen.append((copy.deepcopy(current.ports),value['external']))
            return {'ok':True,'checks':[{'ok':True,'message':'fixture'}]}
        with mock.patch.object(bridge,'verify',side_effect=verify):result=bridge.invoke(self.owner,'check',{'id':self.identity})
        self.assertEqual(len(result['checks']),2);self.assertEqual(len(Runtime.instances),2)
        self.assertFalse(runtime.stopped);self.assertTrue(Runtime.instances[1].stopped)
        self.assertNotIn('local_proxy_port',Runtime.instances[1].ports)
        self.assertEqual(seen[0][1],'');self.assertEqual(seen[1][1],self.definition['external'])
    def test_check_never_repoints_reverse_forward_owned_by_another_agent(self):
        self.agent(0,phone=True);second=self.agent(1);self.manager.sync();self.owner.current=lambda:second
        with mock.patch.object(bridge,'verify') as verify:
            with self.assertRaisesRegex(ValueError,'другого агента'):bridge.invoke(self.owner,'check',{'id':self.identity})
            verify.assert_not_called()
        self.assertFalse(Runtime.instances[0].stopped);self.assertEqual(len(Runtime.instances),1)


if __name__=='__main__':unittest.main()
