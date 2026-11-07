"""Idle must sleep; incoming notes, control and changed update ownership stay live."""
import ast
import json
import logging
import os
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

DESK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DESK))
from localharness import inbox_wake, session_host, source_update
from localharness.package_notice import write


def activity_runner(folder):
    path = DESK / 'localharness/runner.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('_set_busy', '_forge_events_due')]
    writes = []
    state = {'busy': False, 'run': '', 'chat_id': '', 'since': 0.0}
    def publish(inbox):
        inbox.mkdir(parents=True, exist_ok=True)
        write(inbox / '.reader.json', {**state, 'at': time.time()})
        writes.append(dict(state))
    scope = dict(Path=Path, time=time, _tree=folder, _busy=state,
                 _LAST_ACTIVITY=[0.0], _write_reader=publish,
                 log=logging.getLogger('idle-test'),
                 owner_stop=types.SimpleNamespace(paused=lambda: False),
                 _brain_ready=lambda: True, _forge_events=types.SimpleNamespace(tick=lambda: None))
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), scope)
    return scope, writes


class ActivityPublication(unittest.TestCase):
    def test_unchanged_idle_has_no_writes_but_every_actual_transition_is_published(self):
        with tempfile.TemporaryDirectory() as folder:
            r, writes = activity_runner(Path(folder))
            for _ in range(100): r['_forge_events_due']()
            self.assertEqual(writes, [])
            r['_set_busy'](True, 'run-a', chat_id='room-a')
            since = r['_busy']['since']
            r['_set_busy'](True, 'run-a', chat_id='room-a')
            self.assertEqual(r['_busy']['since'], since)
            r['_set_busy'](True, 'run-a', chat_id='room-b')
            r['_set_busy'](True, 'run-b', chat_id='room-b')
            r['_set_busy'](False)
            for _ in range(100): r['_forge_events_due']()
            self.assertEqual([(v['busy'], v['run'], v['chat_id']) for v in writes],
                             [(True, 'run-a', 'room-a'), (True, 'run-a', 'room-b'),
                              (True, 'run-b', 'room-b'), (False, '', '')])
            # Periodic heartbeat can publish freshness independently.
            r['_write_reader'](Path(folder)/'memory/.control/desk_inbox')
            self.assertEqual(len(writes), 5)


@unittest.skipUnless(os.name == 'nt', 'Native Windows notifications')
class NativeNotes(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.wake = inbox_wake.InboxWake(self.folder, notes_only=True)
        self.addCleanup(self.wake.close)
        self.assertTrue(self.wake.native)

    def test_status_and_temporary_writes_cannot_wake_idle_consumer(self):
        for name in ('.reader.json', '.tmp-publishing.md', 'receipt.json'):
            write(self.folder/name, {'busy':False})
            start = time.monotonic()
            self.assertFalse(self.wake.wait(.06))
            self.assertGreaterEqual(time.monotonic()-start, .045)

    def test_real_note_wakes_despite_status_noise_and_changes_are_not_lost(self):
        done = threading.Event()
        def producer():
            for _ in range(5):
                write(self.folder/'.reader.json', {'at':time.time()})
                time.sleep(.01)
            (self.folder/'message.md').write_text('one', encoding='utf-8')
            done.set()
        thread = threading.Thread(target=producer);thread.start()
        try:
            start=time.monotonic()
            self.assertTrue(self.wake.wait(2))
            self.assertLess(time.monotonic()-start, .8)
            self.assertTrue(done.wait(1))
            (self.folder/'message.md').write_text('two - changed', encoding='utf-8')
            self.assertTrue(self.wake.wait(.3))
            self.assertFalse(self.wake.wait(.06))
        finally:thread.join(2)

    def test_atomic_publication_before_wait_is_seen_and_failed_watcher_still_sleeps(self):
        (self.folder/'.tmp-note.md').write_text('new', encoding='utf-8')
        (self.folder/'.tmp-note.md').replace(self.folder/'published.md')
        self.assertTrue(self.wake.wait(.3))
        self.wake._retire()
        start=time.monotonic()
        self.assertFalse(self.wake.wait(.06))
        self.assertGreaterEqual(time.monotonic()-start, .045)


class UpdateOwnership(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.tree=Path(temp.name)/'data';self.directory=Path(temp.name)/'plan'
        self.coordinator={'review_store':'incoming-review','job_control':'incoming-job'}
        self.request={'id':'a'*64,'directory':str(self.directory),'state':'queued'}
        self.operation={**self.request,'tree':str(self.tree),'coordinator':self.coordinator}
        self.publish()
        source_update._OBSERVERS.clear();self.addCleanup(source_update._OBSERVERS.clear)

    def publish(self):
        write(self.directory/'pipeline.json',self.operation)
        write(source_update._requests(self.tree)/(self.request['id']+'.json'),self.request)
        write(source_update.path(self.tree),self.request)

    def test_foreign_work_is_not_polled_forever_and_its_actual_coordinator_still_sees_it(self):
        forge, service=object(),object()
        with patch.object(source_update,'_advance') as advance:
            source_update.tick(self.tree,forge,service)
            with patch.object(source_update,'read',side_effect=AssertionError('foreign history reread')):
                for _ in range(20):source_update.tick(self.tree,forge,service)
            advance.assert_not_called()
            source_update.tick(self.tree,forge,service,coordinator=self.coordinator)
            advance.assert_called_once()

    def test_republished_ownership_is_discovered_without_restarting(self):
        with patch.object(source_update,'_advance') as advance:
            source_update.tick(self.tree,object(),object())
            self.operation['coordinator']=None;self.publish()
            source_update.tick(self.tree,object(),object())
            advance.assert_called_once()


class LocalHTTP(unittest.TestCase):
    def test_loopback_rpc_never_builds_tls_or_uses_proxy_and_refuses_redirects(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            redirect=False
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if self.redirect:
                    self.send_response(302);self.send_header('Location','http://example.invalid/');self.end_headers();return
                data=json.dumps({'generation':'fixture','result':{'ok':True}}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            descriptor={'origin':f'http://127.0.0.1:{server.server_port}', 'generation':'fixture','token':'fixture-token'}
            with (patch.object(ssl,'create_default_context',side_effect=AssertionError('unneeded TLS')),
                  patch.dict(os.environ,{'http_proxy':'http://127.0.0.1:1','https_proxy':'http://127.0.0.1:1'})):
                self.assertTrue(session_host._rpc(descriptor,'status')['ok'])
                Handler.redirect=True
                with self.assertRaises(RuntimeError):session_host._rpc(descriptor,'status')
        finally:
            server.shutdown();thread.join(2);server.server_close()


if __name__=='__main__':unittest.main()
