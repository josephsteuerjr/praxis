"""2.8.11: bridge setup never collides with Tray Bridge and never leaves dead records.

Owner report 20.11: the default local port 8888 was already held by Tray Bridge,
the check failed, the broken bridge stayed saved and could not be removed.
"""
import json,socket,sys,tempfile,threading,types,unittest
from pathlib import Path
from unittest import mock
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness import server_bridge as bridge,server_connections as connections,agents,server_transfer


class Runtime:
    instances=[]
    def __init__(self,raw,configuration,**kwargs):
        self.ports=configuration;self.stopped=False;self.__class__.instances.append(self)
    def start(self):pass
    def stop(self):self.stopped=True
    def state(self):return {'state':'connected','message':'fixture'}


class Stand(unittest.TestCase):
    """Scratch connection root, fixture agent and one busy local port."""
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.folder=Path(folder.name)
        patches=[mock.patch.object(connections,'root',return_value=self.folder),
                 mock.patch.object(connections,'resolve',return_value={'fixture':True}),
                 mock.patch.object(connections,'inspect',return_value={'ok':True}),
                 mock.patch.object(connections,'read',return_value={'name':'Fixture server'}),
                 mock.patch.object(bridge,'Bridge',Runtime),
                 mock.patch.object(agents,'roster',side_effect=lambda root:self.rows),
                 mock.patch.object(agents,'read_config',side_effect=lambda path:self.configs.get(path,{}))]
        for item in patches:item.start();self.addCleanup(item.stop)
        Runtime.instances=[];self.rows=[];self.configs={}
        bridge.install()
        self.agent=types.SimpleNamespace(port=18765,name='Джарвис',config='0',enabled=True,conflict=False,tree=self.folder)
        self.rows.append(self.agent)
        self.owner=types.SimpleNamespace(root=self.folder,lock=threading.RLock(),current=lambda:self.agent)
        self.busy=socket.socket();self.addCleanup(self.busy.close)
        self.busy.bind(('127.0.0.1',0));self.busy.listen(1);self.busy_port=self.busy.getsockname()[1]

    def add(self,**ports):
        return bridge.save_existing({'server_profile':'b'*32,'name':'',
                                     'ports':{'remote_proxy_port':8888,**ports}},self.agent)


class Setup(Stand):
    def test_empty_local_port_takes_a_free_one_outside_tray_bridge(self):
        result=self.add(local_proxy_port='')
        port=result['bridge']['ports']['local_proxy_port']
        self.assertIn(port,bridge.AUTO_PORTS);self.assertNotEqual(port,8888)
        second=self.add()
        self.assertNotEqual(second['bridge']['ports']['local_proxy_port'],port,'saved bridges keep their ports apart')

    def test_explicit_busy_port_is_refused_before_anything_is_saved(self):
        with self.assertRaises(bridge.BridgeCheckError) as caught:self.add(local_proxy_port=self.busy_port)
        self.assertEqual(caught.exception.code,'local_port_unavailable')
        self.assertIn('пустым',str(caught.exception))
        self.assertEqual(bridge.listing(),[])

    def test_failed_verification_writes_no_bridge(self):
        with mock.patch.object(bridge,'verify',side_effect=ValueError('прокси не ответил')):
            with self.assertRaisesRegex(ValueError,'прокси'):bridge.save_existing(
                {'server_profile':'b'*32,'ports':{'remote_proxy_port':8888},'verify':True},self.agent)
        self.assertEqual(bridge.listing(),[]);self.assertTrue(Runtime.instances[0].stopped)

    def test_verified_bridge_is_saved_with_its_checks(self):
        with mock.patch.object(bridge,'verify',return_value={'ok':True,'checks':[{'id':'proxy','ok':True,'message':'ok'}]}):
            result=bridge.save_existing({'server_profile':'b'*32,'ports':{'remote_proxy_port':8888},'verify':True},self.agent)
        self.assertEqual(len(result['checks']),1);self.assertEqual(len(bridge.listing()),1)

    def test_remove_and_repair_refuse_a_bridge_an_agent_uses(self):
        identity=self.add()['bridge']['id']
        self.configs['0']={'network':{'bridge_profile':identity}}
        for action in ('remove','repair-port'):
            with self.assertRaisesRegex(ValueError,'Джарвис'):bridge.invoke(self.owner,action,{'id':identity})
        self.assertEqual(len(bridge.listing()),1)

    def test_remove_forgets_only_local_record_and_frees_the_profile(self):
        identity=self.add()['bridge']['id']
        self.assertEqual(connections.users('b'*32),['Fixture server'])
        result=bridge.invoke(self.owner,'remove',{'id':identity})
        self.assertTrue(result['ok']);self.assertEqual(bridge.listing(),[]);self.assertEqual(connections.users('b'*32),[])

    def test_repair_moves_unused_bridge_to_a_free_port(self):
        identity=self.add()['bridge']['id'];value=bridge.read(identity)
        value['ports']['local_proxy_port']=self.busy_port
        bridge.path(identity).write_text(json.dumps(value),encoding='utf-8')
        result=bridge.invoke(self.owner,'repair-port',{'id':identity})
        port=bridge.read(identity)['ports']['local_proxy_port']
        self.assertNotEqual(port,self.busy_port);self.assertTrue(bridge.port_free(port));self.assertIn(str(port),result['message'])


class RelayOnlyRoute(Stand):
    """2.8.12: the relay alone keeps a bridge alive; Telegram may use another route."""
    def manager(self,cfg):
        identity=self.add(local_proxy_port='')['bridge']['id'];port=bridge.read(identity)['ports']['local_proxy_port']
        proxy={'mode':'proxy','protocol':'http','host':'127.0.0.1','port':str(port)}
        self.configs['0']=cfg(identity,proxy)
        manager=bridge.Manager(self.owner);self.addCleanup(manager.close);Runtime.instances=[]
        manager.sync();return manager,identity

    def test_relay_route_alone_keeps_the_proxy_forward(self):
        manager,identity=self.manager(lambda i,p:{'network':{'proxy':{'mode':'direct'}},'relay':{'bridge_profile':i,'proxy':p}})
        self.assertFalse(manager.errors);self.assertEqual(len(Runtime.instances),1)
        self.assertIn('local_proxy_port',Runtime.instances[0].ports)
        self.assertEqual(bridge.selected_by(identity,self.owner),['Джарвис'])

    def test_relay_route_that_drifted_from_the_bridge_is_reported(self):
        manager,identity=self.manager(lambda i,p:{'relay':{'bridge_profile':i,'proxy':{**p,'port':'1'}}})
        self.assertIn('прокси',manager.errors[identity]);self.assertEqual(Runtime.instances,[])

    def test_both_routes_share_one_forward(self):
        manager,identity=self.manager(lambda i,p:{'network':{'bridge_profile':i,'proxy':p},'relay':{'bridge_profile':i,'proxy':p}})
        self.assertFalse(manager.errors);self.assertEqual(len(Runtime.instances),1)


class TrayProbe(unittest.TestCase):
    """«Подставить Tray Bridge» reports whether something really proxies HTTPS."""
    def serve(self,reply):
        server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);self.addCleanup(server.close)
        seen=[]
        def answer():
            peer,_=server.accept()
            with peer:seen.append(peer.recv(256));peer.sendall(reply)
        threading.Thread(target=answer,daemon=True).start()
        return server.getsockname()[1],seen

    def test_proxy_that_connects_is_alive(self):
        port,seen=self.serve(b'HTTP/1.0 200 Connection established\r\nProxy-agent: tinyproxy\r\n\r\n')
        self.assertEqual(bridge.probe_local_proxy(port),{'ok':True,'state':'proxy','status':200,'port':port})
        self.assertTrue(seen[0].startswith(b'CONNECT api.telegram.org:443 '),'only a CONNECT line goes out')

    def test_refusing_service_is_not_a_proxy(self):
        port,_=self.serve(b'HTTP/1.1 403 Forbidden\r\n\r\n')
        self.assertEqual(bridge.probe_local_proxy(port)['state'],'other')

    def test_nothing_listening_is_absent(self):
        probe=socket.socket();probe.bind(('127.0.0.1',0));port=probe.getsockname()[1];probe.close()
        self.assertEqual(bridge.probe_local_proxy(port)['state'],'absent')


class Connections(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup);self.folder=Path(folder.name)
        item=mock.patch.object(connections,'root',return_value=self.folder);item.start();self.addCleanup(item.stop)

    def test_copied_windows_path_with_quotes_reads_the_key(self):
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        key=ed25519.Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.OpenSSH,serialization.NoEncryption())
        file=self.folder/'my key';file.write_bytes(key)
        for value in (f'"{file}"',f"'{file}'",f'  "{file}"  '):
            self.assertIsNotNone(connections.authentication({'key_path':value})['pkey'])

    def test_profile_removal_waits_for_its_bridge(self):
        identity='c'*32;connections._path(identity).write_text(json.dumps({'format':connections.FORMAT,'id':identity}),encoding='utf-8')
        (self.folder/'bridges').mkdir();(self.folder/'bridges'/('d'*32+'.json')).write_text(
            json.dumps({'id':'d'*32,'name':'Мост','server_profile':identity}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Мост'):connections.remove(identity)
        (self.folder/'bridges'/('d'*32+'.json')).unlink()
        self.assertTrue(connections.remove(identity)['ok']);self.assertFalse(connections._path(identity).exists())

    def test_transport_timeout_is_retried_once_but_wrong_key_is_not(self):
        seen=[]
        def connect(raw,on_client=None):
            seen.append(1)
            if len(seen)==1:raise server_transfer.TransferError('timeout',code='ssh_unavailable')
            return mock.MagicMock()
        key=mock.MagicMock()
        with mock.patch.object(connections,'_host_key',return_value=(key,'SHA256:pin')), \
             mock.patch.object(connections.paramiko,'HostKeys') as known, \
             mock.patch.object(server_transfer,'connect',side_effect=connect),mock.patch.object(connections.time,'sleep'):
            known.return_value.check.return_value=True
            self.assertTrue(connections.inspect({'host':'fixture.example','login':'root','password':'x'})['ok'])
            self.assertEqual(len(seen),2)
            seen.clear()
            with mock.patch.object(server_transfer,'connect',side_effect=server_transfer.TransferError('no',code='ssh_authentication_failed')) as wrong:
                with self.assertRaises(server_transfer.TransferError):connections.inspect({'host':'fixture.example','login':'root','password':'x'})
                self.assertEqual(wrong.call_count,1)

    def test_first_handshake_loss_is_retried(self):
        calls=[]
        def host_key(host,port,on_client):
            calls.append(1)
            if len(calls)==1:raise TimeoutError()
            return mock.MagicMock(),'SHA256:pin'
        with mock.patch.object(connections,'_host_key',side_effect=host_key),mock.patch.object(connections.time,'sleep'), \
             mock.patch.object(connections.paramiko,'HostKeys') as known:
            known.return_value.check.return_value=False
            self.assertTrue(connections.inspect({'host':'fixture.example','login':'root','password':'x'})['trust_needed'])
        self.assertEqual(len(calls),2)


if __name__=='__main__':unittest.main()
