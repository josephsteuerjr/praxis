"""Actual SSH authentication, host pinning and SFTP; disposable Linux only.

This verifies the network transfer seam. The destination is a local fixture;
it does not deploy a second live agent or use the owner's bot credentials.
"""
import hashlib
import os
from pathlib import Path
import secrets
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

import paramiko
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'localharness')]
import server_transfer as transfer


class Server(paramiko.ServerInterface):
    def __init__(self,owner):self.owner=owner
    def check_auth_password(self,user,password):
        self.owner.auths.append(user)
        return paramiko.AUTH_SUCCESSFUL if user=='root' and password==self.owner.password else paramiko.AUTH_FAILED
    def get_allowed_auths(self,user):return 'password'
    def check_channel_request(self,kind,chanid):
        return paramiko.OPEN_SUCCEEDED if kind=='session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED
    def check_channel_exec_request(self,channel,command):
        def run():
            try:
                reply=subprocess.run(command.decode(),shell=True,executable='/bin/sh',capture_output=True,timeout=10)
                if reply.stdout:channel.sendall(reply.stdout)
                if reply.stderr:channel.sendall_stderr(reply.stderr)
                channel.send_exit_status(reply.returncode)
            finally:channel.close()
        threading.Thread(target=run,daemon=True).start();return True


class Files(paramiko.SFTPServerInterface):
    def open(self,path,flags,attr):
        try:
            if not Path(path).resolve().is_relative_to(Path(self.server.owner.root).resolve()):return paramiko.SFTP_PERMISSION_DENIED
            descriptor=os.open(path,flags,0o600)
            stream=os.fdopen(descriptor,'r+b' if flags&os.O_RDWR else 'wb' if flags&os.O_WRONLY else 'rb')
            handle=paramiko.SFTPHandle(flags);handle.readfile=stream;handle.writefile=stream;return handle
        except OSError as exc:return paramiko.SFTPServer.convert_errno(exc.errno)
    def stat(self,path):
        try:return paramiko.SFTPAttributes.from_stat(os.stat(path))
        except OSError as exc:return paramiko.SFTPServer.convert_errno(exc.errno)
    def __init__(self,server,*args,**kwargs):
        super().__init__(server,*args,**kwargs);self.server=server


@unittest.skipIf(os.name=='nt','SSH/process stand runs only in copied Linux')
class SSH(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=self.temp.name;self.password=secrets.token_urlsafe(24);self.auths=[]
        self.key=paramiko.RSAKey.generate(2048)
        self.sock=socket.socket();self.sock.bind(('127.0.0.1',0));self.sock.listen(8);self.sock.settimeout(.2)
        self.stop=threading.Event();self.peers=[];self.workers=[]
        self.raw={'host':'127.0.0.1','port':self.sock.getsockname()[1],'login':'root','password':self.password}
        def serve():
            while not self.stop.is_set():
                try:peer,_=self.sock.accept()
                except socket.timeout:continue
                except OSError:return
                transport=paramiko.Transport(peer);self.peers.append(transport)
                transport.add_server_key(self.key)
                transport.set_subsystem_handler('sftp',paramiko.SFTPServer,Files)
                def session(transport=transport):
                    try:
                        transport.start_server(server=Server(self))
                        while transport.is_active() and not self.stop.wait(.05):pass
                    except (EOFError,paramiko.SSHException):pass
                worker=threading.Thread(target=session,daemon=True);self.workers.append(worker);worker.start()
        self.thread=threading.Thread(target=serve);self.thread.start();self.addCleanup(self.close)
    def close(self):
        self.stop.set();self.sock.close()
        for peer in self.peers:peer.close()
        self.thread.join(2)
        for worker in self.workers:worker.join(2)
    def test_probe_sends_no_password_before_host_confirmation_and_verifies_real_server(self):
        reply=transfer.probe(self.raw)
        self.assertTrue(reply['trust_needed']);self.assertEqual(self.auths,[])
        result=transfer.probe({**self.raw,'confirmed_host':True,'fingerprint':reply['fingerprint']})
        self.assertTrue(result['ok']);self.assertTrue(result['root']);self.assertTrue(result['supported'])
        self.assertIn('root',self.auths)
    def test_sftp_actual_bytes_hash_and_wrong_host_key_refusal(self):
        checked={**self.raw,'fingerprint':transfer.fingerprint(self.key),'confirmed_host':True}
        client=transfer.connect(checked)
        try:
            original=Path(self.root)/'source.bin';original.write_bytes(os.urandom(18377))
            remote=Path(self.root)/'received.bin'
            with client.open_sftp() as sftp:sftp.put(str(original),str(remote))
            code,value,_=transfer.execute(client,'sha256sum '+shlex.quote(str(remote)))
            self.assertEqual(code,0);self.assertEqual(value.split()[0],hashlib.sha256(original.read_bytes()).hexdigest())
            self.assertEqual(remote.read_bytes(),original.read_bytes())
        finally:client.close()
        with self.assertRaises(transfer.TransferError):transfer.connect({**checked,'fingerprint':'SHA256:changed-key'})


if __name__=='__main__':unittest.main()
