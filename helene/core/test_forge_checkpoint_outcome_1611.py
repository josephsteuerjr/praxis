"""Worktree integration needs a current checkpoint receipt, independent of prose."""
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import forge
import frame_language as fl
from test_forge_run_language_1611 import ForgeRunLanguage, RAW


class CheckpointOutcome(unittest.TestCase):
    setUp = ForgeRunLanguage.setUp
    git_run = ForgeRunLanguage.git_run
    settings = ForgeRunLanguage.settings

    def worktree(self):
        task = forge.start(RAW, target=str(self.root), isolation='worktree')
        row = forge.get(task.task_id)
        self.assertEqual(row['cleanup'], 'git-worktree')
        return row

    def finish(self, row):
        with patch('rails.sync_md', return_value=False):
            return forge._finish_unlocked(row['id'], title=RAW, survey={})

    def test_actual_worktree_commit_and_merge16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                    row = self.worktree()
                    raw = (RAW+ui+language).encode('utf8')
                    (Path(row['root'])/'authored.txt').write_bytes(raw)
                    before = self.git_run('rev-parse', 'HEAD')
                    answer = self.finish(row)
                    self.assertEqual(forge.get(row['id'])['status'], 'done', answer)
                    self.assertNotEqual(before, self.git_run('rev-parse', 'HEAD'))
                    self.assertEqual(self.path.read_bytes(), raw)
                    self.assertEqual(self.git_run('log', '-1', '--format=%B'), RAW)
                    self.assertFalse(Path(row['worktree_root']).exists())
                    self.assertNotIn(row['branch'], self.git_run('branch', '--list'))

    def test_actual_unchanged_worktree_can_finish_in_every_context_language(self):
        for language in fl.LANGUAGES:
            with self.subTest(frame=language), self.settings('zh', language):
                row = self.worktree(); before = self.git_run('rev-parse', 'HEAD')
                answer = self.finish(row)
                self.assertEqual(forge.get(row['id'])['status'], 'done', answer)
                self.assertEqual(self.git_run('rev-parse', 'HEAD'), before)
                self.assertEqual(self.path.read_bytes(), b'initial\r\n')
                self.assertFalse(Path(row['worktree_root']).exists())

    def test_untyped_success_words_cannot_authorize_merge_or_discard_a_worktree(self):
        row = self.worktree(); before = self.git_run('rev-parse', 'HEAD')
        raw = RAW.encode('utf8'); path = Path(row['root'])/'authored.txt'; path.write_bytes(raw)
        for caption in ['Checkpoint imaginary', 'Изменений нет', 'No changes', RAW]:
            with self.subTest(caption=caption), patch.object(forge, '_checkpoint_unlocked', return_value=caption):
                self.finish(row)
            self.assertEqual(forge.get(row['id'])['status'], 'active')
            self.assertEqual(self.git_run('rev-parse', 'HEAD'), before)
            self.assertEqual(path.read_bytes(), raw)
            self.assertTrue(Path(row['worktree_root']).is_dir())

    def test_actual_commit_error_keeps_source_and_branch_without_integration(self):
        row = self.worktree(); before = self.git_run('rev-parse', 'HEAD')
        raw = RAW.encode('utf8'); path = Path(row['root'])/'authored.txt'; path.write_bytes(raw)
        run = forge._run
        def commit_error(argv, **kwargs):
            if 'commit' in argv:
                return subprocess.CompletedProcess(argv, 1, '', RAW)
            return run(argv, **kwargs)
        with self.settings('ru', 'fr'), patch.object(forge, '_run', side_effect=commit_error):
            answer = self.finish(row)
        self.assertIn(RAW, answer)
        self.assertEqual(forge.get(row['id'])['status'], 'active')
        self.assertEqual(self.git_run('rev-parse', 'HEAD'), before)
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(Path(row['worktree_root']).is_dir())
        self.assertIn(row['branch'], self.git_run('branch', '--list'))

    def test_proved_commit_integrates_with_arbitrary_display_text(self):
        original = forge._checkpoint_unlocked
        def unreadable_caption(*args, **kwargs):
            result = original(*args, **kwargs)
            self.assertEqual(result.checkpoint_status, 'committed')
            self.assertRegex(result.sha, r'^[0-9a-f]{7,40}$')
            return forge.CheckpointReply(RAW, status=result.checkpoint_status, sha=result.sha)
        for language in fl.LANGUAGES:
            with self.subTest(frame=language), self.settings('ru', language):
                row = self.worktree(); raw = (RAW+language).encode('utf8')
                (Path(row['root'])/'authored.txt').write_bytes(raw)
                with patch.object(forge, '_checkpoint_unlocked', side_effect=unreadable_caption):
                    answer = self.finish(row)
                self.assertEqual(forge.get(row['id'])['status'], 'done', answer)
                self.assertEqual(self.path.read_bytes(), raw)
                self.assertFalse(Path(row['worktree_root']).exists())
