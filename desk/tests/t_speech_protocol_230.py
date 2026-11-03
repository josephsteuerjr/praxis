"""Windows shared-reader publication regressions: old writer fails on this host."""
import ctypes
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import speech_protocol as ipc


@unittest.skipUnless(os.name == 'nt', 'Windows delete-sharing contract')
class SpeechProtocol(unittest.TestCase):
    def test_real_shared_reader_does_not_crash_ready_writer_or_unlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ready.json'
            ipc.write(target, {'ready': False})
            for operation in ('write', 'unlink'):
                handle = target.open('rb')
                attempts = []
                original_sleep = ipc.time.sleep
                def release_on_retry(seconds):
                    attempts.append(seconds)
                    handle.close()
                    original_sleep(0)
                try:
                    # A real held read handle is the deterministic source of
                    # WinError5; only release timing is controlled by this test.
                    with patch.object(ipc.time, 'sleep', side_effect=release_on_retry):
                        if operation == 'write':
                            ipc.write(target, {'ready': True})
                        else:
                            ipc.unlink(target)
                finally:
                    handle.close()
                self.assertTrue(attempts, 'fixture did not exercise Windows sharing failure')
                if operation == 'write':
                    self.assertEqual(ipc.read(target), {'ready': True})
                else:
                    self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_persistent_denial_is_loud_and_bounded_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'ready.json'
            ipc.write(target, {'old': True})
            with (patch.object(ipc.os, 'replace', side_effect=ctypes.WinError(5)) as replace,
                  patch.object(ipc.time, 'sleep')):
                with self.assertRaises(PermissionError):
                    ipc.write(target, {'new': True})
            self.assertEqual(replace.call_count, 21)
            self.assertEqual(ipc.read(target), {'old': True})
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_unrelated_write_fault_is_not_retried(self):
        with (patch.object(ipc.time, 'sleep') as sleep,
              patch.object(ipc.os, 'replace', side_effect=OSError('disk fault')) as replace):
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(OSError, 'disk fault'):
                    ipc.write(Path(directory) / 'ready.json', {'new': True})
            self.assertEqual(replace.call_count, 1)
            sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
