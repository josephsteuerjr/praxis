"""Local owner permissions remain authoritative even for an authenticated server."""
import asyncio
import base64
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock
sys.path[:0] = [str(Path(__file__).resolve().parent.parent), str(Path(__file__).resolve().parent.parent / 'localharness')]
from localharness.remote_computer_client import Publisher, grant, secure_base


class OwnerDesktop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.config = self.root / 'helene.json'
        self.device = 'a' * 32
        self.cfg = {'mode': 'remote', 'base': 'https://desktop.example.invalid', 'service': {'session0': False},
                    'computer': {'remote': {'enabled': True, 'id': self.device, 'token': 't' * 43,
                    'base': 'https://desktop.example.invalid', 'scopes': ['computer.read']}}}
        self.write()
        self.publisher = Publisher(self.config, self.root, self.root / 'data')
        self.publisher.initial = grant(self.config)
        self.publisher.load_permissions()

    def write(self):
        self.config.write_text(json.dumps(self.cfg), encoding='utf8')

    def request(self, capability='desktop.element.find', **fields):
        value = {'capability': capability, 'request_id': 'req-1', 'execution': 'interactive', 'args': {}}
        value.update(fields)
        return {'kind': 'request', 'id': 'b' * 32, 'device': self.device, 'deadline': time.time() + 60,
                'method': 'POST', 'path': f'/v1/controller/{self.device}/invoke',
                'content_type': 'application/json', 'body': base64.b64encode(json.dumps(value).encode()).decode()}

    def authorize(self, request):
        with mock.patch('localharness.owner_stop.stopped', return_value=False):
            return self.publisher.authorize(request)

    def test_server_cannot_expand_local_permissions(self):
        self.assertEqual(self.authorize(self.request())[-1], 'computer.read')
        for cap in ('desktop.window.read', 'desktop.screen.capture', 'desktop.clipboard.read', 'os.process.list',
                    'desktop.input.perform', 'desktop.window.activate', 'fs.read', 'fs.write_atomic',
                    'process.start', 'desktop.unknown_admin'):
            with self.assertRaises(PermissionError):
                self.authorize(self.request(cap, request_id=cap.replace('.', '-')))
        for cap in ('fs.stat','fs.list'):
            self.assertEqual(self.authorize(self.request(cap))[-1],'computer.read')

    def test_dynamic_revoke_and_bad_config_fail_closed(self):
        self.authorize(self.request())
        self.cfg['computer']['remote']['scopes'] = []; self.write()
        with self.assertRaises(PermissionError): self.authorize(self.request())
        self.config.write_text('{broken', encoding='utf8')
        with self.assertRaises(ValueError): self.authorize(self.request())

    def test_transport_headers_cannot_override_local_native_authority(self):
        for headers in ({'authorization':'Bearer attacker'}, {'host':'evil'},
                        {'x-praxis-mime':'application/json\r\nAuthorization: attack'}):
            request=self.request();request['headers']=headers
            with self.assertRaises(ValueError):self.authorize(request)
        request=self.request();request['headers']={'x-praxis-total-size':'123'}
        self.authorize(request)

    def test_expired_wrong_device_changed_server_and_owner_panic_refused(self):
        old = self.request(); old['deadline'] = time.time() - 1
        wrong = self.request(); wrong['device'] = 'b' * 32
        for request in (old, wrong):
            with self.assertRaises(PermissionError): self.authorize(request)
        with mock.patch('localharness.owner_stop.stopped', return_value=True):
            with self.assertRaises(PermissionError): self.publisher.authorize(self.request())
        self.cfg['base'] = 'https://another.example.invalid'; self.write()
        with self.assertRaises(PermissionError): self.authorize(self.request())

    def test_scope_cannot_be_changed_under_existing_request_identity(self):
        self.authorize(self.request())
        self.cfg['computer']['remote']['scopes'] = list(('computer.read', 'computer.files')); self.write()
        with self.assertRaises(PermissionError): self.authorize(self.request('fs.read'))

    def test_windows_system_is_explicit_local_broker_not_native_or_vps(self):
        self.cfg['computer']['remote']['scopes'] = ['computer.process']; self.write()
        with self.assertRaises(PermissionError): self.authorize(self.request('process.start', execution='system'))
        request = self.request(); request['path'] = f'/v1/controller/{self.device}/broker'
        request['body'] = base64.b64encode(b'{"op":"exec","cmd":"C:\\\\Windows\\\\System32\\\\whoami.exe","args":[],"why":"fixture"}').decode()
        with self.assertRaises(PermissionError): self.authorize(request)
        self.cfg['service']['session0'] = True; self.write()
        self.assertEqual(self.authorize(request)[1], 'broker')

    def test_receipts_and_artifacts_remember_their_original_permission(self):
        self.authorize(self.request())
        request = self.request(); request.update(method='GET', path=f'/v1/controller/{self.device}/requests/req-1', body='')
        self.assertEqual(self.authorize(request)[-1], 'computer.read')
        sha = 'b' * 64
        self.publisher.record_artifacts({'artifacts': [{'sha256': sha}]}, 'computer.read')
        request['path'] = f'/v1/artifacts/{sha}/content?offset=0&length=4096'
        self.assertEqual(self.authorize(request)[-1], 'computer.read')
        self.publisher.permissions['artifacts'][sha] = 'computer.files'
        with self.assertRaises(PermissionError): self.authorize(request)
        request['path'] = f'/v1/controller/{self.device}/requests/unknown'
        with self.assertRaises(PermissionError): self.authorize(request)

    def test_no_cleartext_credentials_arbitrary_hosts_or_changed_pair(self):
        for value in ('http://public.example.invalid', 'https://user:key@host.invalid', 'https://host.invalid/?key=secret'):
            with self.assertRaises(ValueError): secure_base(value)
        self.cfg['computer']['remote']['token'] = 'n' * 43; self.write()
        with self.assertRaises(PermissionError): self.authorize(self.request())

if __name__ == '__main__':
    unittest.main()
