"""Completion captions preserve unknown evidence, authored inputs and actual effects."""
from contextlib import contextmanager
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import json

import forge
import frame_language as fl
import test_forge_run_language_1611 as fixture
import test_forge_checkpoint_outcome_1611 as checkpoints

RAW = fixture.RAW


class FinishLanguage(unittest.TestCase):
    setUp = fixture.ForgeRunLanguage.setUp
    git_run = fixture.ForgeRunLanguage.git_run
    settings = fixture.ForgeRunLanguage.settings
    worktree = checkpoints.CheckpointOutcome.worktree

    def remote(self, response):
        row = forge.get(self.task)
        row.update(scope='host', source_git=str(self.root))
        forge._save_task(row)
        fake = SimpleNamespace(call=Mock(return_value=response),
            workspace_inspect=Mock(side_effect=lambda root, action, **kw:
                json.dumps({'changed':[RAW]}) if action=='impact' else RAW))
        return fake

    def test_incomplete_or_malformed_remote_rows_keep_task_active_and_unknown16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                for malformed in (False, True):
                    response = ({'ok':True, 'operations':[RAW]} if malformed else
                        {'ok':True, 'operations':[], 'shown':1, 'matched':2, 'note':RAW})
                    fake = self.remote(response)
                    with self.subTest(ui=ui, frame=language, malformed=malformed), self.settings(ui, language), \
                         patch.object(forge, 'serverd_client', fake), \
                         patch.object(forge, '_finish_unlocked', side_effect=AssertionError('unknown survey cannot close')):
                        answer = forge.finish(self.task)
                        detail = fl.text('forge.finish.14', v1='1') if malformed else None
                        note = (fl.text('forge.finish.15', v1=detail) if malformed else
                            fl.text('forge.finish.12', v1='shown=1, matched=2', v2='; '+RAW))
                        self.assertIn(note, answer)
                        self.assertIn(fl.text('forge.finish.18'), answer)
                        self.assertEqual(forge.get(self.task)['finish_unknowns'], [note])
                        self.assertEqual(forge.get(self.task)['status'], 'active')
                        fake.call.assert_called_once_with('op.list',
                            {'root_value':str(self.root), 'limit':100, 'live_only':True})
                        self.assertEqual(fake.workspace_inspect.call_count, 1)

    def test_abandon_closes_only_the_record_and_preserves_project_bytes16axes(self):
        before = {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        original = forge.get(self.task)
        for index, (ui, language) in enumerate((ui, lang) for ui in fl.LANGUAGES for lang in fl.LANGUAGES):
            row = {**original, 'id':'code-'+f'{index+400:08x}'}; forge._save_task(row)
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                 patch.object(forge.forge_learning, 'record', return_value={}) as record:
                answer = forge.abandon(row['id'], reason=RAW, checked=RAW)
                root_state = fl.text('forge.finish.48', v1=str(self.root))
                self.assertEqual(answer, fl.text('forge.finish.52', v0=row['id'], v2='active',
                    v4=fl.text('forge.finish.51', v1=root_state)))
                task = forge.get(row['id'])
                self.assertEqual(task['status'], 'abandoned')
                self.assertEqual(task['abandoned_from'], 'active')
                self.assertEqual(task['review'], RAW); self.assertEqual(task['checked'], RAW)
                self.assertEqual(record.call_args.kwargs['regression'], RAW)
                self.assertEqual(record.call_args.kwargs['lesson'], fl.text('forge.finish.49', v1=RAW)
                    +fl.text('forge.finish.50', v1=root_state))
            self.assertEqual(before, {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_error_after_record_is_closed_reports_unconfirmed_and_does_not_erase_the_result(self):
        original_lock = forge._mutation_lock
        @contextmanager
        def late_error(*args, **kwargs):
            with original_lock(*args, **kwargs) as beat:yield beat
            raise TimeoutError(RAW)
        original = forge.get(self.task)
        for index, language in enumerate(fl.LANGUAGES):
            row = {**original, 'id':'code-'+f'{index+500:08x}'}; forge._save_task(row)
            with self.settings('zh', language), patch.object(forge, '_mutation_lock', side_effect=late_error), \
                 patch.object(forge.forge_learning, 'record', return_value={'id':'fixture', 'lesson':RAW}):
                answer = forge.finish(row['id'], submit=False, checked=RAW)
                self.assertEqual(answer, fl.text('forge.finish.44', v1=RAW))
            self.assertEqual(forge.get(row['id'])['status'], 'done')
            path = forge._task_file(row['id']); before = path.read_bytes()
            with self.settings('ru', language):
                self.assertEqual(forge.finish(row['id']), fl.text('forge.finish.42', v0=row['id'], v2='done'))
            self.assertEqual(path.read_bytes(), before)

    def test_failed_cleanup_after_real_merge_is_described_as_requested(self):
        with self.settings('ru', 'fr'):
            row = self.worktree(); raw = RAW.encode('utf8')
            (Path(row['root'])/'authored.txt').write_bytes(raw)
            run = forge._run
            def refuse_cleanup(argv, **kwargs):
                if 'worktree' in argv and 'remove' in argv:
                    return subprocess.CompletedProcess(argv, 1, '', RAW)
                return run(argv, **kwargs)
            with patch.object(forge, '_run', side_effect=refuse_cleanup), patch('rails.sync_md', return_value=False):
                answer = forge._finish_unlocked(row['id'], title=RAW, survey={})
            self.assertEqual(forge.get(row['id'])['status'], 'done')
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertTrue(Path(row['worktree_root']).is_dir())
            self.assertIn(fl.text('forge.finish.28', v1=row['source_branch'],
                v3=self.git_run('rev-parse', '--short', 'HEAD')), answer)

    def test_survey_and_saved_unknowns_pin_language_across_remote_readback(self):
        settings = {'lang':'zh', 'agent':{'frame_language':'en'}}
        fake = self.remote({'ok':True, 'operations':[], 'shown':1, 'matched':2})
        def changed(*args, **kwargs):
            settings['agent']['frame_language'] = 'fr'
            return json.dumps({'changed':[RAW]})
        fake.workspace_inspect.side_effect = changed
        with patch.object(fl, 'settings', side_effect=lambda:settings), patch.object(forge, 'serverd_client', fake):
            first = forge.finish(self.task)
            with fl.use('en'):
                note = fl.text('forge.finish.12', v1='shown=1, matched=2', v2='')
                self.assertIn(note, first)
                self.assertEqual(forge.get(self.task)['finish_unknowns'], [note])
            second = forge.finish(self.task)
            self.assertIn(fl.text('forge.finish.12', v1='shown=1, matched=2', v2=''), second)
            self.assertNotEqual(first, second)
