"""Real SSH local + reverse forwarding, collision, host pin and teardown."""
import contextlib,select,socket,threading,time,sys,tempfile,unittest
from pathlib import Path
import paramiko
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness.server_bridge_runtime import Bridge
from localharness.server_transfer import fingerprint


def free_port():
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


class Forwarding(unittest.TestCase):
    def setUp(self):
        self.stop=threading.Event();self.sockets=[];self.workers=[];self.transports=[];self.auths=[];self.destinations={}
        self.key=paramiko.RSAKey.generate(2048)
        def listen(port=0,*,reuse=False):
            sock=socket.socket()
            if reuse:sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            sock.bind(('127.0.0.1',port));sock.listen(10);sock.settimeout(.1);self.sockets.append(sock);return sock
        self.listen=listen
        def thread(fn):
            worker=threading.Thread(target=fn,daemon=True);self.workers.append(worker);worker.start()
        self.thread=thread
        def copy(left,right):
            self.sockets.extend([left,right])
            try:
                while not self.stop.is_set():
                    ready,_,_=select.select([left,right],[],[],.1)
                    for source in ready:
                        body=source.recv(65536)
                        if not body:return
                        (right if source is left else left).sendall(body)
            except (OSError,EOFError,paramiko.SSHException):pass
            finally:
                for peer in (left,right):
                    with contextlib.suppress(OSError,EOFError,paramiko.SSHException):peer.close()
        self.copy=copy
        self.echo=listen()
        def echo():
            while not self.stop.is_set():
                try:peer,_=self.echo.accept()
                except socket.timeout:continue
                except OSError:return
                self.sockets.append(peer)
                def respond(peer=peer):
                    try:
                        peer.settimeout(3);data=peer.recv(65536)
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 7\r\n\r\nfixture' if data.startswith(b'GET ') else data)
                    except OSError:pass
                    finally:peer.close()
                thread(respond)
        thread(echo);self.ssh=listen();owner=self
        class Server(paramiko.ServerInterface):
            def __init__(self,transport):self.transport=transport
            def get_allowed_auths(self,user):return 'password'
            def check_auth_password(self,user,password):owner.auths.append(user);return paramiko.AUTH_SUCCESSFUL
            def check_channel_direct_tcpip_request(self,chanid,origin,destination):
                owner.destinations[chanid]=destination
                return paramiko.OPEN_SUCCEEDED if destination==owner.echo.getsockname() else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
            def check_port_forward_request(self,address,port):
                if address!='127.0.0.1':return False
                listener=listen(port)
                def reverse():
                    while not owner.stop.is_set() and self.transport.is_active():
                        try:peer,origin=listener.accept()
                        except socket.timeout:continue
                        except OSError:return
                        try:channel=self.transport.open_forwarded_tcpip_channel(origin,(address,port))
                        except (OSError,paramiko.SSHException):peer.close();return
                        thread(lambda peer=peer,channel=channel:copy(peer,channel))
                thread(reverse);return port
        def accept():
            while not self.stop.is_set():
                try:peer,_=self.ssh.accept()
                except socket.timeout:continue
                except OSError:return
                transport=paramiko.Transport(peer);self.transports.append(transport);transport.add_server_key(self.key)
                def session(transport=transport):
                    try:
                        transport.start_server(server=Server(transport))
                        while transport.is_active() and not self.stop.is_set():
                            channel=transport.accept(.1)
                            if channel is None:continue
                            remote=socket.create_connection(self.destinations[channel.get_id()],timeout=3)
                            thread(lambda channel=channel,remote=remote:copy(channel,remote))
                    except (EOFError,OSError,paramiko.SSHException):pass
                thread(session)
        thread(accept);self.bridges=[];self.addCleanup(self.close)
        self.connection={'host':'127.0.0.1','port':self.ssh.getsockname()[1],'login':'fixture',
                         'password':'fixture','fingerprint':fingerprint(self.key)}
    def close(self):
        for bridge in self.bridges:bridge.stop()
        self.stop.set()
        for transport in self.transports:transport.close()
        for sock in self.sockets:
            try:sock.close()
            except OSError:pass
        for worker in self.workers:worker.join(2)
    def bridge(self,connection=None,**ports):
        bridge=Bridge(connection or self.connection,ports);self.bridges.append(bridge);bridge.start();return bridge
    def wait(self,bridge,state):
        deadline=time.monotonic()+8
        while bridge.state()['state']!=state and time.monotonic()<deadline:time.sleep(.02)
        self.assertEqual(bridge.state()['state'],state,bridge.state())
    def test_real_local_and_reverse_http_then_release(self):
        local,reverse=free_port(),free_port()
        bridge=self.bridge(local_proxy_port=local,remote_proxy_port=self.echo.getsockname()[1],
                           local_app_port=self.echo.getsockname()[1],remote_app_port=reverse)
        self.wait(bridge,'connected')
        for port in (local,reverse):
            with socket.create_connection(('127.0.0.1',port),timeout=3) as peer:
                peer.sendall(b'GET /probe HTTP/1.1\r\nHost: fixture\r\n\r\n')
                self.assertIn(b'fixture',peer.recv(65536))
        bridge.stop();self.assertEqual(bridge.state()['state'],'off')
        with socket.socket() as probe:
            if not hasattr(socket,'SO_EXCLUSIVEADDRUSE'):probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            probe.bind(('127.0.0.1',local))
        self.assertFalse(any(thread.is_alive() for thread in bridge.workers))
    def test_stopped_forward_reopens_same_port_after_actual_http(self):
        port=free_port()
        for _ in range(2):
            bridge=self.bridge(local_proxy_port=port,remote_proxy_port=self.echo.getsockname()[1])
            self.wait(bridge,'connected')
            with socket.create_connection(('127.0.0.1',port),timeout=3) as peer:
                peer.sendall(b'GET /probe HTTP/1.1\r\nHost: fixture\r\n\r\n')
                chunks=[]
                while part:=peer.recv(65536):chunks.append(part)
                self.assertIn(b'fixture',b''.join(chunks))
            bridge.stop();self.assertEqual(bridge.state()['state'],'off')
            self.assertFalse(any(worker.is_alive() for worker in bridge.workers))
    def test_foreign_reusable_listener_still_refuses_before_authentication(self):
        busy=self.listen(reuse=True);port=busy.getsockname()[1]
        bridge=self.bridge(local_proxy_port=port,remote_proxy_port=self.echo.getsockname()[1])
        self.wait(bridge,'retry');self.assertEqual(self.auths,[])
        with socket.create_connection(('127.0.0.1',port),timeout=3) as peer:
            accepted,_=busy.accept();accepted.close()
        self.assertEqual(busy.getsockname()[1],port)
    def test_foreign_port_is_preserved_before_authentication(self):
        busy=self.listen();port=busy.getsockname()[1]
        bridge=self.bridge(local_proxy_port=port,remote_proxy_port=self.echo.getsockname()[1])
        self.wait(bridge,'retry');self.assertEqual(self.auths,[])
        self.assertEqual(busy.getsockname()[1],port)
    def test_changed_host_key_refuses_authentication(self):
        bridge=self.bridge({**self.connection,'fingerprint':'SHA256:different'},
                           local_proxy_port=free_port(),remote_proxy_port=self.echo.getsockname()[1])
        self.wait(bridge,'retry');self.assertEqual(self.auths,[])
    def test_reconnect_recovers_real_forward_after_transport_drop(self):
        port=free_port();bridge=self.bridge(local_proxy_port=port,remote_proxy_port=self.echo.getsockname()[1])
        self.wait(bridge,'connected');before=len(self.auths)
        for transport in list(self.transports):transport.close()
        deadline=time.monotonic()+8
        while (len(self.auths)==before or bridge.state()['state']!='connected') and time.monotonic()<deadline:time.sleep(.02)
        self.assertGreater(len(self.auths),before);self.assertEqual(bridge.state()['state'],'connected')
        with socket.create_connection(('127.0.0.1',port),timeout=3) as peer:
            peer.sendall(b'GET / HTTP/1.1\r\nHost: fixture\r\n\r\n');self.assertIn(b'fixture',peer.recv(65536))
    def test_exclusive_profile_lease_refuses_second_runtime_without_displacing_first(self):
        with tempfile.TemporaryDirectory() as directory:
            first=Bridge(self.connection,{'local_proxy_port':free_port(),'remote_proxy_port':self.echo.getsockname()[1]},state_dir=directory)
            second=Bridge(self.connection,{'local_proxy_port':free_port(),'remote_proxy_port':self.echo.getsockname()[1]},state_dir=directory)
            try:
                first.start();self.wait(first,'connected');before=len(self.auths)
                second.start();self.wait(second,'error');self.assertEqual(len(self.auths),before)
                second.stop();self.assertEqual(first.state()['state'],'connected')
            finally:second.stop();first.stop()
    def test_stop_during_unresponsive_ssh_handshake_releases_local_port(self):
        stalled=self.listen();accepted=threading.Event()
        def accept():
            try:
                peer,_=stalled.accept();self.sockets.append(peer);accepted.set()
                self.stop.wait(4)
            except OSError:pass
        self.thread(accept);port=free_port()
        bridge=self.bridge({**self.connection,'port':stalled.getsockname()[1]},local_proxy_port=port,remote_proxy_port=self.echo.getsockname()[1])
        self.assertTrue(accepted.wait(2));start=time.monotonic();bridge.stop()
        self.assertLess(time.monotonic()-start,3);self.assertEqual(bridge.state()['state'],'off')
        with socket.socket() as probe:probe.bind(('127.0.0.1',port))


if __name__=='__main__':unittest.main()
