"""Background compact refresh owns one thread, cooldown and shutdown."""
import contextvars
from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'localharness'))
from refresh_care import RefreshCare


class CareTests(unittest.TestCase):
    def test_runs_off_requester_and_coalesces_canonical_place(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        seen = []
        value = contextvars.ContextVar('refresh-test', default='missing')
        def debt(place):
            seen.append((place, threading.get_ident(), value.get()))
            entered.set()
            self.assertTrue(release.wait(3))
            return {'unresolved_group_count': 1}
        def refresh(place, **kw):
            seen.append((place, kw))
            done.set()
            return {'ok': True}
        life = SimpleNamespace(place_key=lambda _: 'canonical', refresh_debt=debt, refresh_compacts=refresh)
        care = RefreshCare(life).start()
        try:
            token = value.set('request-context')
            try:
                self.assertTrue(care.request('topic'))
            finally:
                value.reset(token)
            self.assertTrue(entered.wait(3))
            self.assertFalse(care.request('same-place'))
            self.assertNotEqual(seen[0][1], threading.get_ident())
            self.assertEqual(seen[0][2], 'request-context')
            release.set()
            self.assertTrue(done.wait(3))
            self.assertEqual(seen[1], ('canonical', {'max_chunks': 1}))
        finally:
            release.set()
            self.assertTrue(care.stop())
        self.assertFalse(care.request('after-stop'))

    def test_stop_during_debt_prevents_later_model_refresh(self):
        entered, release = threading.Event(), threading.Event()
        called = []
        def debt(place):
            entered.set()
            self.assertTrue(release.wait(3))
            return {'unresolved_group_count': 1}
        life = SimpleNamespace(place_key=str, refresh_debt=debt,
                               refresh_compacts=lambda *a, **kw: called.append(a))
        care = RefreshCare(life).start()
        care.request('a')
        self.assertTrue(entered.wait(3))
        self.assertTrue(care.request('b'))
        self.assertFalse(care.stop(timeout=0))
        release.set()
        self.assertTrue(care.stop())
        self.assertEqual(called, [])

    def test_cooldown_and_disabled_policy(self):
        life = SimpleNamespace(place_key=str)
        clock = [10.0]
        care = RefreshCare(life, cooldown=10, clock=lambda: clock[0])
        self.assertTrue(care.request('a'))
        with care._condition:
            care._pending.clear()
        self.assertFalse(care.request('a'))
        clock[0] = 20
        self.assertTrue(care.request('a'))
        self.assertTrue(care.stop())
        disabled = RefreshCare(life, cooldown=0)
        self.assertFalse(disabled.request('a'))
        self.assertTrue(disabled.stop())


if __name__ == '__main__':
    unittest.main()
