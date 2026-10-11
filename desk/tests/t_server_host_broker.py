"""Real Linux root exec, bounded pipes/processes, grants, peer and panic gates."""
import json
import os
import socket
import socketserver
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import host_broker
import host_access


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Real host-root process tests run only inside copied Linux Docker')
class HostRoot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'data/memory/.control').mkdir(parents=True)
        (self.root / 'helene.json').write_text('{"service":{"session0":true}}')
        self.policy = self.root / 'policy.json'
        self.row = {'enabled': True, 'install': str(self.root), 'container': 'stand', 'agent_uid': 10001}
        self.write_policy()
        self.peer = (os.getpid(), 10001, 10000)
        self.broker = host_broker.HostBroker(self.policy)
        self.top = patch.object(host_broker.subprocess, 'run',
            return_value=SimpleNamespace(returncode=0, stdout=f'PID\n{os.getpid()}\n'))
        self.top.start(); self.addCleanup(self.top.stop)

    def write_policy(self):
        self.policy.write_text(json.dumps(self.row)); self.policy.chmod(0o600)

    def call(self, argv=None, timeout=2, peer=None):
        return self.broker.handle({'op': 'exec' if argv else 'ping', 'argv': argv, 'timeout_sec': timeout},
                                  peer or self.peer)

    def test_ping_and_exec_identify_host_scope_and_real_root_uid(self):
        ping = self.call(); self.assertTrue(ping['ok'], ping); self.assertEqual(ping['scope'], 'host')
        out = self.call(['/usr/bin/id', '-u'])
        self.assertTrue(out['ok'], out); self.assertEqual(out['out'].strip(), '0')
        self.assertEqual(out['uid'], 0)
        audit = [json.loads(line) for line in self.policy.with_name('audit.jsonl').read_text().splitlines()]
        self.assertEqual([row['phase'] for row in audit], ['start', 'result'])
        self.assertEqual(audit[0]['id'], audit[1]['id'])
        self.assertNotIn('argv', audit[0]); self.assertNotIn('out', audit[1])

    def test_unwritable_or_symlink_audit_prevents_root_execution(self):
        audit = self.policy.with_name('audit.jsonl')
        marker = self.root / 'must-not-exist'
        audit.symlink_to(self.root / 'foreign')
        out = self.call([sys.executable, '-c', f'open({str(marker)!r}, "w").write("bad")'])
        self.assertFalse(out['ok']); self.assertFalse(marker.exists())

    def test_same_uid_outside_container_and_root_channel_peer_are_refused(self):
        with patch.object(host_broker.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='PID\n999999\n')):
            self.assertIn('outside', self.call()['error'])
        self.assertFalse(self.call(peer=(os.getpid(), 0, 0))['ok'])
        self.assertFalse(self.call(peer=(os.getpid(), 10002, 10000))['ok'])

    def test_grant_and_session0_are_independent_required_controls(self):
        self.row['enabled'] = False; self.write_policy()
        self.assertFalse(self.call()['ok'])
        self.row['enabled'] = True; self.write_policy()
        (self.root / 'helene.json').write_text('[]')
        self.assertFalse(self.call()['ok'])
        self.row['enabled'] = True; self.write_policy()
        (self.root / 'helene.json').write_text('{"agent_mode":"interactive","service":{"session0":false}}')
        self.assertFalse(self.call()['ok'])

    def test_non_root_owned_or_writable_policy_cannot_grant_privileges(self):
        self.policy.chmod(0o666); self.assertFalse(self.call()['ok'])
        self.policy.chmod(0o600); os.chown(self.policy, 10001, 10000)
        self.assertFalse(self.call()['ok'])

    def test_panic_latch_and_pending_request_refuse_new_commands(self):
        latch = self.root / 'data/.owner-stop/stop.json'; latch.parent.mkdir()
        latch.write_text('{}'); self.assertFalse(self.call()['ok'])
        latch.unlink()
        (self.root / 'data/memory/.control/panic.json').write_text('{}')
        self.assertFalse(self.call()['ok'])

    def test_timeout_kills_command_and_retains_partial_output(self):
        out = self.call([sys.executable, '-u', '-c', 'import time;print("start");time.sleep(30)'], timeout=0.2)
        self.assertFalse(out['ok'], out); self.assertIn('timed out', out['error'])
        self.assertIn('start', out['out']); self.assertLess(out['ms'], 3000)

    def test_panic_and_revocation_cancel_running_commands(self):
        for revoke in ('panic', 'grant'):
            self.row['enabled'] = True; self.write_policy()
            pending = self.root / 'data/memory/.control/panic.json'; pending.unlink(missing_ok=True)
            def cancel():
                time.sleep(0.15)
                if revoke == 'panic': pending.write_text('{}')
                else: self.row['enabled'] = False; self.write_policy()
            thread = threading.Thread(target=cancel); thread.start()
            out = self.call([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=5)
            thread.join()
            self.assertFalse(out['ok'], out); self.assertIn('revoked', out['error'])
            self.assertLess(out['ms'], 3000)

    def test_nonzero_exit_stderr_and_bounded_output_are_reported(self):
        out = self.call([sys.executable, '-c', 'import sys; print("x"*200000);print("bad",file=sys.stderr);sys.exit(7)'])
        self.assertTrue(out['ok'], out); self.assertEqual(out['code'], 7)
        self.assertEqual(out['err'].strip(), 'bad'); self.assertTrue(out['truncated'])
        self.assertEqual(len(out['out']), host_broker.MAX_OUTPUT)
        self.assertIn('64', out['note'])

    def test_closed_output_pipes_do_not_disable_timeout_or_allow_later_effect(self):
        marker = self.root / 'late-effect'
        code = f'import os,time;os.close(1);os.close(2);time.sleep(0.6);open({str(marker)!r},"w").write("late")'
        out = self.call([sys.executable, '-c', code], timeout=0.15)
        self.assertFalse(out['ok'], out)
        self.assertIn('timed out', out['error'])
        self.assertFalse(marker.exists())
        self.assertLess(out['ms'], 550)

    def test_closed_output_pipes_still_observe_revocation(self):
        def revoke():
            time.sleep(0.15)
            self.row['enabled'] = False; self.write_policy()
        thread = threading.Thread(target=revoke); thread.start()
        out = self.call([sys.executable, '-c', 'import os,time;os.close(1);os.close(2);time.sleep(30)'], timeout=5)
        thread.join()
        self.assertFalse(out['ok'], out); self.assertIn('revoked', out['error'])
        self.assertLess(out['ms'], 1500)

    def test_recycled_peer_pid_or_reassigned_grant_cancels_command(self):
        original = host_broker.process_birth(self.peer[0])
        with patch.object(host_broker, 'process_birth', side_effect=[original, 'recycled']):
            out = self.call([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=5)
        self.assertFalse(out['ok'], out); self.assertIn('revoked', out['error'])
        def reassign():
            time.sleep(0.15)
            self.row['container'] = 'different'; self.write_policy()
        thread = threading.Thread(target=reassign); thread.start()
        out = self.call([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=5)
        thread.join()
        self.assertFalse(out['ok'], out); self.assertIn('revoked', out['error'])

    def test_socket_listener_limits_handlers_and_recovers_capacity(self):
        release = threading.Event()
        entered = []
        class Hold(socketserver.BaseRequestHandler):
            def handle(self):
                entered.append(self.request)
                release.wait(2)
        with patch.object(host_broker, 'MAX_HANDLERS', 2), host_broker.Server(str(self.root / 'test.sock'), Hold) as server:
            worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01})
            worker.start()
            clients = []
            try:
                for _ in range(3):
                    client = socket.socket(socket.AF_UNIX); client.settimeout(1)
                    client.connect(server.server_address); clients.append(client)
                self.assertEqual(clients[-1].recv(1), b'')
                self.assertEqual(len(entered), 2)
                release.set()
                deadline = time.monotonic() + 1
                while not server.handlers.acquire(blocking=False) and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertLess(time.monotonic(), deadline)
                server.handlers.release()
            finally:
                release.set(); server.shutdown(); worker.join()
                for client in clients: client.close()

    def test_invalid_command_and_timeout_are_refused_without_execution(self):
        for argv in [['id'], ['/bin/echo', 3], ['/bin/echo', '\0']]:
            self.assertFalse(self.call(argv)['ok'])
        for timeout in [0, 601, '10', True, float('nan'), 2**4096]:
            self.assertFalse(self.call(['/usr/bin/id'], timeout)['ok'])


@unittest.skipUnless(sys.platform.startswith('linux') and hasattr(os, 'geteuid') and os.geteuid() == 0,
                     'Provisioning tests run only inside copied Linux Docker')
class Provision(unittest.TestCase):
    def test_enable_grants_only_after_ready_socket_and_failed_reenable_revokes_old_grant(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); install = root / 'install'; (install / 'server').mkdir(parents=True)
            (install / 'data').mkdir(); (install / 'helene.json').write_text('{"service":{"session0":false}}')
            units = root / 'units'; units.mkdir()
            box = {'State': {'Running': True}, 'Mounts': [
                {'Type': 'bind', 'Destination': '/opt/helene/helene.json', 'Source': str(install / 'helene.json')},
                {'Type': 'bind', 'Destination': '/opt/helene/data', 'Source': str(install / 'data')}]}
            held = []
            fail = False
            def run(argv, **kwargs):
                if argv[:2] == ['docker', 'inspect']:
                    return SimpleNamespace(stdout=json.dumps([box]))
                if argv[0] == 'systemctl':
                    policy = root / 'state/stand/policy.json'
                    self.assertFalse(json.loads(policy.read_text())['enabled'])
                    if argv[1] == 'restart':
                        if fail: raise host_access.subprocess.CalledProcessError(1, argv)
                        endpoint = root / 'state/stand/socket/broker.sock'
                        server = socket.socket(socket.AF_UNIX); server.bind(str(endpoint)); server.listen(); held.append(server)
                        os.chown(endpoint, 0, 10000); endpoint.chmod(0o660)
                return SimpleNamespace(stdout='', returncode=0)
            try:
                with patch.object(host_access, 'STATE', root / 'state'), patch.object(host_access, 'UNIT_DIR', units), \
                     patch.object(host_access.subprocess, 'run', side_effect=run), \
                     patch.object(host_access.subprocess, 'check_output', side_effect=[b'10001', b'10000', str(os.getpid()).encode(), b'10001', b'10000']), \
                     patch.object(host_access.shutil, 'which', return_value=sys.executable):
                    self.assertTrue(host_access.provision(install, 'stand')['enabled'])
                    self.assertFalse(json.loads((install / 'helene.json').read_text())['service']['session0'])
                    fail = True
                    with self.assertRaises(host_access.subprocess.CalledProcessError):
                        host_access.provision(install, 'stand')
                    self.assertFalse(json.loads((root / 'state/stand/policy.json').read_text())['enabled'])
            finally:
                for server in held: server.close()


if __name__ == '__main__':
    unittest.main()
