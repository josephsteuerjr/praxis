"""Native Windows directory wake and its non-busy timed fallback."""
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import inbox_wake


class FakeAPI:
    def __init__(self, result=inbox_wake._TIMEOUT, *, rearm=True, handle=123):
        self.result, self.rearm, self.handle = result, rearm, handle
        self.calls = []

    def FindFirstChangeNotificationW(self, *args):
        self.calls.append(('open', args))
        return self.handle

    def WaitForSingleObject(self, *args):
        self.calls.append(('wait', args))
        return self.result

    def FindNextChangeNotification(self, *args):
        self.calls.append(('rearm', args))
        return self.rearm

    def FindCloseChangeNotification(self, *args):
        self.calls.append(('close', args))
        return True


class InboxWakeProtocolTests(unittest.TestCase):
    def wake(self, api):
        with patch.object(inbox_wake, '_windows_api', return_value=api):
            value = inbox_wake.InboxWake(tempfile.gettempdir())
        self.addCleanup(value.close)
        return value

    def test_timeout_retries_wait_without_rearming(self):
        api = FakeAPI()
        wake = self.wake(api)
        self.assertFalse(wake.wait(.01))
        self.assertFalse(wake.wait(.02))
        self.assertEqual([name for name, _ in api.calls], ['open', 'wait', 'wait'])
        self.assertEqual(api.calls[0][1][1:], (False, 0x11))

    def test_signal_rearms_once_before_the_next_wait(self):
        api = FakeAPI(inbox_wake._SIGNALED)
        wake = self.wake(api)
        self.assertTrue(wake.wait(.01))
        self.assertTrue(wake.wait(.01))
        self.assertEqual([name for name, _ in api.calls], ['open', 'wait', 'rearm', 'wait', 'rearm'])

    def test_rearm_failure_keeps_signal_but_next_wait_uses_timed_fallback(self):
        api = FakeAPI(inbox_wake._SIGNALED, rearm=False)
        wake = self.wake(api)
        self.assertTrue(wake.wait(.01))
        self.assertFalse(wake.native)
        start = time.monotonic()
        self.assertFalse(wake.wait(.03))
        self.assertGreaterEqual(time.monotonic() - start, .02)
        self.assertEqual([name for name, _ in api.calls], ['open', 'wait', 'rearm', 'close'])

    def test_failed_wait_is_closed_and_does_not_make_idle_loop_busy(self):
        api = FakeAPI(inbox_wake._FAILED)
        wake = self.wake(api)
        start = time.monotonic()
        self.assertFalse(wake.wait(.04))
        self.assertGreaterEqual(time.monotonic() - start, .03)
        self.assertFalse(wake.native)
        wake.close(); wake.close()
        self.assertEqual([name for name, _ in api.calls].count('close'), 1)

    def test_rearm_exception_cannot_delay_an_already_received_inbox_signal(self):
        api = FakeAPI(inbox_wake._SIGNALED)
        wake = self.wake(api)
        with patch.object(api, 'FindNextChangeNotification', side_effect=OSError('watch unavailable')):
            start = time.monotonic()
            self.assertTrue(wake.wait(1))
            self.assertLess(time.monotonic() - start, .1)
        self.assertFalse(wake.native)

    def test_unsupported_platform_falls_back_and_close_interrupts_timed_wait(self):
        wake = self.wake(None)
        closed = threading.Event()
        def close():
            time.sleep(.04); wake.close(); closed.set()
        thread = threading.Thread(target=close)
        thread.start()
        start = time.monotonic()
        self.assertFalse(wake.wait(1))
        self.assertTrue(closed.wait(.2))
        self.assertLess(time.monotonic() - start, .5)
        thread.join()

    def test_invalid_handle_does_not_close_invalid_object(self):
        api = FakeAPI(handle=-1)
        wake = self.wake(api)
        self.assertFalse(wake.native)
        self.assertFalse(wake.wait(0))
        wake.close(); wake.close()
        self.assertEqual([name for name, _ in api.calls], ['open'])


@unittest.skipUnless(os.name == 'nt', 'Actual Windows notification API')
class NativeInboxWakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.wake = inbox_wake.InboxWake(self.folder)
        self.addCleanup(self.wake.close)
        self.assertTrue(self.wake.native, 'Native Windows inbox watch must really be active')

    def write_later(self, name):
        def write():
            time.sleep(.06)
            (self.folder / name).write_text('controlled inbox item', encoding='utf8')
        thread = threading.Thread(target=write)
        thread.start()
        return thread

    def test_file_created_before_wait_is_not_lost(self):
        (self.folder / 'before.json').write_text('{}', encoding='utf8')
        start = time.monotonic()
        self.assertTrue(self.wake.wait(1))
        self.assertLess(time.monotonic() - start, .5)

    def test_thread_writes_wake_repeatedly_before_one_second(self):
        for name in ('first.json', 'second.json', 'third.json'):
            thread = self.write_later(name)
            start = time.monotonic()
            self.assertTrue(self.wake.wait(1))
            self.assertLess(time.monotonic() - start, .7)
            thread.join()
            # The name and last-write notifications may be separate. Drain any
            # already-signaled hint, without interpreting it as another item.
            for _ in range(5):
                if not self.wake.wait(0):
                    break

    def test_idle_timeout_really_sleeps_and_close_is_idempotent(self):
        start = time.monotonic()
        self.assertFalse(self.wake.wait(.08))
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, .06)
        self.assertLess(elapsed, .6)
        self.wake.close(); self.wake.close()
        self.assertFalse(self.wake.native)
        self.assertFalse(self.wake.wait(1))


if __name__ == '__main__':
    unittest.main()
