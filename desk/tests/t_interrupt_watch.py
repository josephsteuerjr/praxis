"""Interrupt producer -> independent consumer -> cancellation receipt (no child process)."""
import datetime as dt
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(DESK), str(DESK / 'localharness')]
from deskd import control
import control_watch


class Manager:
    def __init__(self):
        self.rows = [{"run_id": "old", "created_at": "2026-01-01T00:00:00Z"},
                     {"run_id": "future", "created_at": "2099-01-01T00:00:00Z"}]
        self.calls = []
        self.called = threading.Event()
        self.outcome_status = 'cancelled'
        self.current_status = 'running'
        self.hook = lambda: None

    def list_runs(self, **kwargs):
        return self.rows

    def request_cancel(self, rid, **kwargs):
        self.calls.append(rid)
        self.hook()
        self.called.set()
        self.current_status = self.outcome_status
        return {"status": self.outcome_status}

    def status(self, rid):
        return {'status': self.current_status}


class Interrupt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name)
        self.manager = Manager()
        self.folder = self.tree / 'memory' / '.control'

    def test_producer_consumer_receipt_and_stale_cutoff(self):
        sent = control.interrupt(self.tree)
        receipt = control_watch.consume(self.tree, self.manager, ('running', 'paused'))
        self.assertEqual(self.manager.calls, ['old'])
        self.assertEqual(receipt['id'], sent['request']['id'])
        self.assertEqual(receipt['cancelled'], ['old'])
        self.assertIsNone(control_watch.consume(self.tree, self.manager, ('running',)))
        self.assertEqual(control.supervisor_state(self.tree)['interrupt_receipt'], receipt)

    def test_pending_tool_is_not_reported_as_cancelled(self):
        self.manager.outcome_status = 'paused'
        control.interrupt(self.tree, scope='old')
        receipt = control_watch.consume(self.tree, self.manager, ('running',))
        self.assertEqual(receipt['cancelled'], [])
        self.assertEqual(receipt['pending_tool_outcomes'], ['old'])

    def test_new_request_is_not_erased_while_old_one_is_consumed(self):
        control.interrupt(self.tree)
        self.manager.hook = lambda: control.interrupt(self.tree, scope='future')
        control_watch.consume(self.tree, self.manager, ('running',))
        new = json.loads((self.folder / 'interrupt.json').read_text(encoding='utf-8'))
        self.assertEqual(new['scope'], 'future')

    def test_invalid_request_gets_failure_receipt_without_cancel(self):
        self.folder.mkdir(parents=True)
        (self.folder / 'interrupt.json').write_text('[not-json', encoding='utf-8')
        receipt = control_watch.consume(self.tree, self.manager, ('running',))
        self.assertTrue(receipt['failed'])
        self.assertEqual(self.manager.calls, [])
        self.assertFalse((self.folder / 'interrupt.processing.json').exists())

    def test_control_runs_while_main_thread_waits(self):
        stop, worker = control_watch.start(self.tree, self.manager, ('running',), interval=.01)
        try:
            control.interrupt(self.tree)
            self.assertTrue(self.manager.called.wait(2), 'independent watcher did not cancel')
        finally:
            stop.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())

    def test_cancel_winning_first_suppresses_boundary(self):
        control.interrupt(self.tree)
        control_watch.consume(self.tree, self.manager, ('running',))
        with control_watch.delivery(lambda: self.manager, 'old') as permitted:
            self.assertFalse(permitted)

    def test_boundary_winning_first_defers_cancel_receipt(self):
        control.interrupt(self.tree)
        entered = threading.Event()
        def cancel():
            entered.set()
            control_watch.consume(self.tree, self.manager, ('running',))
        with control_watch.delivery(lambda: self.manager, 'old') as permitted:
            self.assertTrue(permitted)
            worker = threading.Thread(target=cancel)
            worker.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(self.manager.called.wait(.05))
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(self.manager.called.is_set())

    def test_same_millisecond_birth_is_not_proven_older(self):
        self.folder.mkdir(parents=True)
        (self.folder / 'interrupt.json').write_text(json.dumps({
            'scope': 'all', 'at': '2026-09-24T12:00:00.123100Z'}), encoding='utf-8')
        self.manager.rows = [{'run_id': 'edge', 'created_at': '2026-09-24T12:00:00.123Z'}]
        control_watch.consume(self.tree, self.manager, ('running',))
        self.assertEqual(self.manager.calls, [])

    def test_missing_birth_is_named_not_cancelled(self):
        self.manager.rows = [{'run_id': 'unknown'}]
        control.interrupt(self.tree)
        receipt = control_watch.consume(self.tree, self.manager, ('running',))
        self.assertEqual(self.manager.calls, [])
        self.assertEqual(receipt['failed'][0]['run_id'], 'unknown')


if __name__ == '__main__':
    unittest.main()
