"""A single desktop Update waits for real Git merge and frozen review receipts.

Only model/process launch is fake (the existing pipeline fixture); the candidate,
review evidence, Stop/Pause markers and changing files are real disposable files.
"""
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import ExitStack

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).parent))
import t_source_update_pipeline_220 as fixtures
from localharness import python_updates as updates, update_progress, code_transition
from localharness.package_notice import write


class Continuation(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.PipelineTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.waiting = threading.Event()
        self.owner = SimpleNamespace(root=f.old, event=lambda name, row:
            self.waiting.set() if row.get('stage') == 'waiting_review' else None)
        self.report = update_progress.Progress(f.root / '.helene-update-test.progress.json',
            'test', f.old, event=self.owner.event)
        self.out, self.errors = [], []
        self.target = os.path.normcase(str(f.old.resolve()))

    def start(self):
        def run():
            try:self.out.append(updates._wait_for_review(self.owner, self.fixture.new, self.report))
            except BaseException as exc:self.errors.append(exc)
        self.thread = threading.Thread(target=run)
        self.thread.start()
        self.addCleanup(self.finish)
        self.assertTrue(self.waiting.wait(8), self.errors)

    def finish(self):
        updates.cancel_waiting(self.owner)
        self.thread.join(8)
        self.assertFalse(self.thread.is_alive(), 'continuation must settle')

    def test_same_request_continues_after_real_merge_and_frozen_panel_without_new_preflight(self):
        self.start()
        self.assertEqual(updates.update_progress(self.owner)['stage'], 'waiting_review')
        self.fixture.ready()
        with patch.object(code_transition, 'preflight', side_effect=AssertionError('new request')):
            self.thread.join(8)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.out, [True])
        self.assertNotIn(self.target, updates._waiting_updates)

    def test_original_install_invocation_reaches_one_prepared_handoff_after_review(self):
        from localharness import python_platform, update_extensions
        f = self.fixture
        staging = f.root / '.helene-update-single-click'
        staging.mkdir();f.new.rename(staging / 'Helene');f.new = staging / 'Helene'
        runtime = f.new / ('runtime/python.exe' if os.name == 'nt' else 'runtime/bin/python3')
        runtime.parent.mkdir(parents=True);runtime.touch()
        (f.new / 'app').mkdir();(f.new / 'app/python_app.py').touch()
        with ExitStack() as stack:
            stack.enter_context(patch.object(python_platform, 'require_runtime'))
            stack.enter_context(patch.object(update_extensions, 'check'))
            handoff = stack.enter_context(patch.object(updates, '_launch_handoff', return_value={'updater_pid':42}))
            def run():
                try:self.out.append(updates._prepare_handoff(self.owner, staging, {}, self.report, False))
                except BaseException as exc:self.errors.append(exc)
            self.thread = threading.Thread(target=run);self.thread.start()
            self.addCleanup(self.finish)
            self.assertTrue(self.waiting.wait(8), self.errors)
            handoff.assert_not_called()
            f.ready()
            self.thread.join(10)
            self.assertEqual(self.errors, [])
            self.assertEqual(self.out, [{'updater_pid':42}])
            handoff.assert_called_once()
            self.assertEqual(self.report.value['stage'], 'prepared')

    def test_cancel_revokes_continuation_even_if_review_finishes_later(self):
        self.start()
        self.assertEqual(updates.update_cancel(self.owner), {'ok': True})
        self.fixture.ready()
        self.thread.join(8)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.out, [False])
        self.assertEqual(self.report.value['stage'], 'cancelled')

    def test_close_revokes_pending_invocation(self):
        self.start()
        updates.cancel_waiting(self.owner)
        self.thread.join(8)
        self.assertEqual(self.out, [False])
        self.assertNotIn(self.target, updates._waiting_updates)

    def test_settings_change_requires_new_owner_action(self):
        self.start()
        write(self.fixture.old / 'agent-default.json', {'id': 'different'})
        self.fixture.ready()
        self.thread.join(8)
        self.assertFalse(self.out)
        self.assertRegex(str(self.errors[0]), 'изменились')

    def test_opening_panel_or_changing_appearance_does_not_cancel_source_review(self):
        self.start()
        write(self.fixture.old / 'window-preferences.json', {'frame.panel': '0', 'frame.theme': 'dark'})
        self.fixture.ready()
        self.thread.join(8)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.out, [True])

    def test_local_edit_after_review_is_not_silently_merged_again(self):
        self.start()
        (self.fixture.code / 'module.py').write_text('late_owner_edit = True\n')
        operation = self.fixture.operation()
        operation['state'] = 'ready_to_apply'
        write(self.fixture.directory / 'pipeline.json', operation)
        self.thread.join(8)
        self.assertFalse(self.out)
        self.assertTrue(self.errors)
        self.assertIn('late_owner_edit', (self.fixture.code / 'module.py').read_text())

    def test_cancelled_or_superseded_plan_cannot_continue(self):
        self.start()
        write(self.fixture.directory / 'cancel.json', {'reason': 'Owner Stop'})
        self.thread.join(8)
        self.assertFalse(self.out)
        self.assertRegex(str(self.errors[0]), 'отменена')

    def test_pause_holds_ready_candidate_until_explicit_resume(self):
        self.fixture.ready()
        marker = self.fixture.tree / 'memory/.control/autonomy-paused.json'
        write(marker, {'paused': True})
        self.start()
        self.thread.join(.7)
        self.assertTrue(self.thread.is_alive())
        marker.unlink()
        self.thread.join(8)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.out, [True])

    def test_closed_process_receipt_is_not_a_new_automatic_update(self):
        self.report.phase('waiting_review')
        self.assertEqual(updates.update_progress(self.owner)['stage'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
