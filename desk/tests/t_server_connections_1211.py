"""Real SSH handshake/authentication on loopback, with private temporary profiles."""
import io,json,socket,sys,tempfile,threading,time,unittest
from pathlib import Path
from unittest import mock
import paramiko
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
from localharness import server_connections as profiles


class Credentials(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=paramiko.RSAKey.generate(2048)
    def key_text(self,password=None):
        out=io.StringIO();self.key.write_private_key(out,password=password);return out.getvalue()
    def test_key_pasted_as_password_is_parsed_without_password_auth(self):
        result=profiles.authentication({'password':self.key_text()})
        self.assertIsNone(result['password']);self.assertEqual(result['pkey'],self.key)
    def test_existing_key_path_and_encrypted_key(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'key.pem';path.write_text(self.key_text('fixture passphrase'),encoding='utf-8')
            self.assertEqual(profiles.authentication({'key_path':str(path),'key_password':'fixture passphrase'})['pkey'],self.key)
            with self.assertRaisesRegex(ValueError,'пароль ключа'):profiles.authentication({'key_path':str(path)})
    def test_malformed_ambiguous_and_oversized_inputs_have_safe_errors(self):
        for raw in ({'key_text':'SECRET-INVALID'}, {'key_text':self.key_text(),'key_path':'path'},
                    {'key_text':'-----BEGIN PRIVATE KEY-----\n'+'secret'*20000},{}):
            with self.assertRaises(ValueError) as result:profiles.authentication(raw)
            self.assertNotIn('SECRET-INVALID',str(result.exception));self.assertNotIn('-----BEGIN',str(result.exception))
    def test_generic_pkcs8_export_and_encryption(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        key=ed25519.Ed25519PrivateKey.generate()
        for password in (None,'fixture secret'):
            encryption=serialization.NoEncryption() if password is None else serialization.BestAvailableEncryption(password.encode())
            pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,encryption).decode()
            result=profiles.authentication({'password':pem,'key_password':password})
            expected=key.public_key().public_bytes(serialization.Encoding.OpenSSH,serialization.PublicFormat.OpenSSH).decode().split()[1]
            self.assertIsNone(result['password']);self.assertEqual(result['pkey'].get_base64(),expected)
            if password:
                with self.assertRaisesRegex(ValueError,'пароль ключа'):profiles.authentication({'key_text':pem})


class Connections(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Path(self.temp.name)/'profiles'
        patch=mock.patch.object(profiles,'root',return_value=self.store);patch.start();self.addCleanup(patch.stop)
        self.hostkey=paramiko.RSAKey.generate(2048);self.auths=[];self.commands=[]
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen();self.sock.settimeout(.1)
        self.stop=threading.Event();self.peers=[];self.threads=[]
        owner=self
        class Server(paramiko.ServerInterface):
            def check_auth_password(self,user,password):
                owner.auths.append((user,password));return paramiko.AUTH_SUCCESSFUL if password=='fixture-only' else paramiko.AUTH_FAILED
            def check_channel_exec_request(self,channel,command):
                owner.commands.append(command);return False
        def accept():
            while not self.stop.is_set():
                try:peer,_=self.sock.accept()
                except socket.timeout:continue
                except OSError:return
                transport=paramiko.Transport(peer);self.peers.append(transport);transport.add_server_key(self.hostkey)
                def serve(transport=transport):
                    try:
                        transport.start_server(server=Server())
                        while transport.is_active() and not self.stop.wait(.01):pass
                    except (EOFError,OSError,paramiko.SSHException):pass
                thread=threading.Thread(target=serve,daemon=True);thread.start();self.threads.append(thread)
        self.listener=threading.Thread(target=accept,daemon=True);self.listener.start();self.addCleanup(self.close)
        self.raw={'host':'127.0.0.1','port':self.sock.getsockname()[1],'login':'fixture','password':'fixture-only'}
    def close(self):
        self.stop.set();self.sock.close()
        for peer in self.peers:peer.close()
        self.listener.join(2)
        for thread in self.threads:thread.join(2)
    def confirmed(self):
        first=profiles.inspect(self.raw)
        self.assertTrue(first['trust_needed']);self.assertFalse(self.auths)
        return {**self.raw,'fingerprint':first['fingerprint'],'confirmed_host':True}
    def test_trust_before_auth_private_save_and_reuse_without_exposing_secrets(self):
        result=profiles.save(self.confirmed())
        self.assertTrue(result['ok']);self.assertEqual(self.auths,[('fixture','fixture-only')]);self.assertFalse(self.commands)
        public=profiles.profiles();self.assertEqual(public,[result['profile']]);self.assertNotIn('fixture-only',json.dumps(public))
        request={'server_profile':result['profile']['id']}
        self.assertTrue(profiles.inspect(request)['ok'])
        self.assertEqual(profiles.resolve(request)['password'],'fixture-only')
        with self.assertRaisesRegex(ValueError,'изменился'):profiles.resolve({**request,'host':'elsewhere.invalid'})
        self.hostkey=paramiko.RSAKey.generate(2048);count=len(self.auths)
        with self.assertRaisesRegex(ValueError,'Отпечаток'):profiles.inspect(request)
        self.assertEqual(len(self.auths),count)
    def test_unknown_host_does_not_save_or_authenticate(self):
        self.assertTrue(profiles.save(self.raw)['trust_needed'])
        self.assertFalse(self.store.exists());self.assertFalse(self.auths)

    def test_cancel_before_transport_start_closes_socket_promptly(self):
        started=time.monotonic()
        with self.assertRaisesRegex(ValueError,'SSH'):
            profiles.inspect(self.raw,on_client=lambda pending:pending.close())
        self.assertLess(time.monotonic()-started,2)
        self.assertFalse(self.auths)


if __name__=='__main__':unittest.main()
