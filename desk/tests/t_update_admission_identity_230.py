"""Physical helper admissions cannot consume a previous legacy handoff receipt."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]
from localharness import python_updates as updates
from t_update_legacy_admission_2610 import LegacyAdmission


class AdmissionIdentity(unittest.TestCase):
    def test_new_reader_ignores_retained_legacy_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'updater-admitted.json'
            old.write_text(json.dumps({'pid': -1, 'target': str(root)}))
            before = old.read_bytes()
            name = 'updater-admitted-' + '1' * 32 + '.json'
            (root / name).write_text(json.dumps({'pid': os.getpid(), 'target': str(root)}))
            child = SimpleNamespace(pid=os.getpid(), poll=Mock(return_value=None), terminate=Mock(), wait=Mock())
            result = updates.await_admission(child, root, root, receipt_name=name)
            self.assertEqual(result['pid'], os.getpid())
            self.assertEqual(old.read_bytes(), before)
            child.terminate.assert_not_called()

    def test_named_receipt_still_rejects_wrong_physical_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            name = 'updater-admitted-' + '2' * 32 + '.json'
            (root / name).write_text(json.dumps({'pid': -1, 'target': str(root)}))
            child = SimpleNamespace(pid=os.getpid(), poll=Mock(return_value=None), terminate=Mock(), wait=Mock())
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                updates.await_admission(child, root, root, receipt_name=name)
            child.terminate.assert_called_once()
            with self.assertRaisesRegex(ValueError, 'leaves staging'):
                updates.await_admission(child, root, root, receipt_name='../escape.json')

    def test_real_mechanical_writer_uses_unique_receipt_without_replacing_legacy(self):
        fixture = LegacyAdmission('test_old_exit_timeout_never_prepares_or_reopens_over_live_owner')
        fixture.setUp()
        try:
            legacy = fixture.staging / 'updater-admitted.json'
            legacy.write_text(json.dumps({'pid': -1, 'target': str(fixture.old)}))
            before = legacy.read_bytes()
            name = 'updater-admitted-' + '3' * 32 + '.json'
            fixture.args.admission_name = name
            def owner_exit(pid, stopped=lambda: False):
                value = updates.await_admission(fixture.child, fixture.staging, fixture.old, receipt_name=name)
                self.assertEqual(value['pid'], os.getpid())
                self.assertEqual(legacy.read_bytes(), before)
                raise TimeoutError('bounded synthetic owner remains live')
            fixture.wait_old = owner_exit
            def observed(transaction, receipt, ready):
                ready.set()
                return transaction()
            with self.assertRaisesRegex(TimeoutError, 'synthetic owner'):
                fixture.run_mechanical(observer=observed)
            self.assertEqual(legacy.read_bytes(), before)
            fixture.assert_old_unchanged()
        finally:
            fixture.doCleanups()


if __name__ == '__main__':
    unittest.main()
