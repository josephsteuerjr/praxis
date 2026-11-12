"""Actual TLS proxy + separate TLS origin. No external network or credentials."""
import datetime
import ipaddress
import select
import socket
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deskd.network_route import Route


class HTTPSProxyTest(unittest.TestCase):
    def test_verified_nested_tls_bounded_read_auth_and_redirect_policy(self):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            subject=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'fixture.invalid')])
            now=datetime.datetime.now(datetime.timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                  .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
                  .not_valid_after(now+datetime.timedelta(hours=1))
                  .add_extension(x509.SubjectAlternativeName([x509.DNSName('fixture.invalid'),
                      x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
                  .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
            (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(root/'cert.pem',root/'key.pem')
            trusted=ssl.create_default_context(cafile=str(root/'cert.pem'))
            seen=[];payload=b'bounded fixture '*10000
            class Origin(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_GET(self):
                    seen.append(('origin',self.path,dict(self.headers)))
                    status=302 if self.path=='/redirect' else 403 if self.path=='/refused' else 200
                    self.send_response(status);self.send_header('Content-Length',str(len(payload)))
                    if status==302:self.send_header('Location','https://foreign.invalid/leak')
                    self.end_headers()
                    try:self.wfile.write(payload)
                    except (OSError,ssl.SSLError):pass
            origin=ThreadingHTTPServer(('127.0.0.1',0),Origin);origin.socket=tls.wrap_socket(origin.socket,server_side=True)
            class Proxy(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def do_CONNECT(self):
                    seen.append(('proxy',self.path,dict(self.headers)))
                    if self.path!='fixture.invalid:443':self.send_error(403);return
                    remote=socket.create_connection(origin.server_address,timeout=4)
                    self.send_response(200);self.end_headers();self.wfile.flush()
                    try:
                        while True:
                            readable,_,_=select.select([self.connection,remote],[],[],3)
                            if not readable:break
                            for source in readable:
                                chunk=source.recv(65536)
                                if not chunk:return
                                (remote if source is self.connection else self.connection).sendall(chunk)
                    except (OSError,ssl.SSLError):pass
                    finally:remote.close()
            proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy);proxy.socket=tls.wrap_socket(proxy.socket,server_side=True)
            threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in (origin,proxy)]
            for thread in threads:thread.start()
            route=Route({'network':{'proxy':{'mode':'proxy','protocol':'https','host':'127.0.0.1',
                'port':proxy.server_port,'username':'fixture','password':'local-only'}}})
            try:
                with mock.patch('deskd.network_route.ssl.create_default_context',return_value=trusted):
                    with route.open('https://fixture.invalid/body',timeout=4) as response:
                        self.assertEqual(response.read(7),payload[:7])
                        self.assertEqual(response.read(),payload[7:])
                    for path,status in (('/redirect',302),('/refused',403)):
                        with self.assertRaises(urllib.error.HTTPError) as result:
                            route.open('https://fixture.invalid'+path,timeout=4)
                        self.assertEqual(result.exception.code,status);result.exception.close()
                self.assertTrue(all(row[2].get('Proxy-Authorization') for row in seen if row[0]=='proxy'))
                self.assertTrue(all(not row[2].get('Proxy-Authorization') for row in seen if row[0]=='origin'))
                self.assertEqual([row[1] for row in seen if row[0]=='origin'],['/body','/redirect','/refused'])
                # Untrusted TLS must fail before either CONNECT credentials or the origin request.
                before=len(seen)
                with self.assertRaises(urllib.error.URLError):route.open('https://fixture.invalid/body',timeout=4)
                self.assertEqual(len(seen),before)
            finally:
                for server in (proxy,origin):server.shutdown();server.server_close()
                for thread in threads:thread.join(4)


if __name__=='__main__':unittest.main()
