"""Self-submit receipts, gates and Forge state survive arbitrary display wording."""
from pathlib import Path
import unittest
from unittest.mock import patch

import forge
import frame_language as fl
import selfdev
import test_selfdev as fixtures

RAW = 'literal Русский 中文 {v1} <script>inert</script>'


class SubmitOutcome(unittest.TestCase):
    def setUp(self):
        fixtures.SelfdevFlow.setUp(self)
        self.addCleanup(fixtures.SelfdevFlow.tearDown, self)
        for name, value in [('BASE', self.repo.parent), ('REPO', self.repo),
                            ('TASKS_DIR', self.repo.parent/'forge-tasks'),
                            ('STATE_DIR', self.repo.parent/'forge-state')]:
            self.enterContext(patch.object(forge, name, value))
        self.enterContext(patch.object(forge.forge_learning, 'record',
            return_value={'id':'fixture-lesson', 'lesson':'raw fixture lesson'}))

    _begin_and_edit = fixtures.SelfdevFlow._begin_and_edit

    def task(self, pid, index=0):
        row = {'id':'code-'+f'{index+300:08x}', 'goal':RAW, 'status':'active',
               'root':str(selfdev.worktree_path(pid)), 'proposal_id':pid, 'cleanup':'selfdev'}
        forge._save_task(row)
        return row['id']

    def finish(self, tid):
        return forge._finish_unlocked(tid, title='change fixture value', review=fixtures.RV,
                                       checked=RAW, survey={})

    def test_real_red_gate_then_successful_retry_changes_forge_state_from_receipt(self):
        pid = self._begin_and_edit('core.py', 'VALUE = 99\n')
        tid = self.task(pid); original = selfdev.submit; outcomes = []
        def display_only(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertIsInstance(result, selfdev.SubmitReply)
            outcomes.append(result.submission_status)
            return selfdev.SubmitReply(RAW, status=result.submission_status)
        with fl.use('zh'), patch.object(selfdev, 'submit', side_effect=display_only):
            self.finish(tid)
            self.assertEqual(forge.get(tid)['status'], 'active')
            self.assertEqual(selfdev.get(pid)['status'], 'proposed')
            self.assertFalse(selfdev.get(pid)['tests']['ok'])
            self.assertEqual((self.repo/'core.py').read_text(), 'VALUE = 1\n')
            self.assertFalse(selfdev.restart_requested())
            (selfdev.worktree_path(pid)/'core.py').write_text('VALUE = 2\n', encoding='utf8')
            self.finish(tid)
        self.assertEqual(outcomes, ['refused', 'merged'])
        self.assertEqual(forge.get(tid)['status'], 'done')
        self.assertEqual(selfdev.get(pid)['status'], 'merged')
        self.assertTrue(selfdev.get(pid)['tests']['ok'])
        self.assertEqual((self.repo/'core.py').read_text(), 'VALUE = 2\n')
        self.assertIn('merged', selfdev.restart_requested())
        self.assertFalse(selfdev.worktree_path(pid).exists())
        with patch.object(selfdev, 'run_tests') as tests:
            result = selfdev.submit(pid, 'already merged')
        self.assertEqual(result.submission_status, 'merged')
        tests.assert_not_called()

    def test_real_merge_conflict_is_unknown_and_preserves_both_source_and_proposal(self):
        pid = self._begin_and_edit('core.py', 'VALUE = 2\n')
        tid = self.task(pid)
        (self.repo/'core.py').write_text('VALUE = 3\n', encoding='utf8')
        for args in [('add','core.py'), ('commit','-qm','concurrent source edit')]:
            result = fixtures._sh(self.repo, 'git', *args)
            self.assertEqual(result.returncode, 0, result.stderr)
        original = selfdev.submit; outcomes = []
        def observed(*args, **kwargs):
            result = original(*args, **kwargs); outcomes.append(result.submission_status)
            return selfdev.SubmitReply(RAW, status=result.submission_status)
        with fl.use('en'), patch.object(selfdev, 'submit', side_effect=observed):
            self.finish(tid)
        self.assertEqual(outcomes, ['unknown'])
        self.assertEqual(forge.get(tid)['status'], 'submitted')
        self.assertEqual(selfdev.get(pid)['status'], 'proposed')
        self.assertEqual((self.repo/'core.py').read_text(), 'VALUE = 3\n')
        self.assertEqual((selfdev.worktree_path(pid)/'core.py').read_text(), 'VALUE = 2\n')
        self.assertFalse(selfdev.restart_requested())

    def test_untyped_success_words_cannot_turn_a_proposal_into_done_in_any_language(self):
        pid = self._begin_and_edit('core.py', 'VALUE = 2\n')
        ledger = selfdev.LEDGER.read_bytes()
        for index, (language, caption) in enumerate(zip(fl.LANGUAGES,
                ['смёржено самостоятельно', 'merged', 'fusionné', '已合并'])):
            tid = self.task(pid, index)
            with self.subTest(frame=language), fl.use(language), patch.object(selfdev, 'submit', return_value=caption):
                self.finish(tid)
            self.assertEqual(forge.get(tid)['status'], 'submitted')
            self.assertEqual(selfdev.LEDGER.read_bytes(), ledger)
            self.assertEqual((self.repo/'core.py').read_text(), 'VALUE = 1\n')
            self.assertTrue(selfdev.worktree_path(pid).is_dir())
            self.assertFalse(selfdev.restart_requested())

    def test_actual_review_refusal_has_no_git_or_restart_effect(self):
        pid = self._begin_and_edit('core.py', 'VALUE = 2\n')
        before = selfdev.LEDGER.read_bytes()
        with patch.object(selfdev, '_git') as git:
            result = selfdev.submit(pid, 'change fixture value', review='')
        self.assertEqual(result.submission_status, 'refused')
        git.assert_not_called()
        self.assertEqual(selfdev.LEDGER.read_bytes(), before)
        self.assertEqual((self.repo/'core.py').read_text(), 'VALUE = 1\n')
        self.assertTrue(selfdev.worktree_path(pid).exists())
        self.assertFalse(selfdev.restart_requested())
