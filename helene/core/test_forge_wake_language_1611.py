"""Forge wakes, reconcile receipts and upstream monitoring follow the agent-context language.

Statuses, ids, URLs, stored canonical markers and raw external errors stay exact; only owned
prose changes. Interface language is independent of the agent-context language.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import forge
import frame_language as fl
from core import subagents

CYRILLIC = re.compile('[А-Яа-яЁё]')
RAW = 'Literal Русский 中文 {v1} <inert>'


class ForgeWakeLanguage(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.state = Path(temp.name)
        self.enterContext(patch.object(forge, 'STATE_DIR', self.state))
        self.enterContext(patch.object(forge, 'TASKS_DIR', self.state / 'tasks'))
        self.enterContext(patch.object(forge, '_UPSTREAMS', self.state / 'upstreams.json'))
        self.enterContext(patch.object(forge, '_UPSTREAMS_RETIRED', self.state / 'retired.json'))

    def settings(self, ui, language):
        return patch.object(fl, 'settings',
                            return_value={'lang': ui, 'agent': {'frame_language': language}})

    def test_completion_wakes_keep_ids_statuses_and_raw_summaries_in_every_pairing(self):
        items = [{'priority': 'urgent', 'status': 'done', 'goal': RAW, 'task_id': 'code-1',
                  'summary': RAW, 'verdict_escalation': 'не выполнен'},
                 {'priority': 'normal', 'status': 'stalled', 'goal': '', 'task_id': 'code-2',
                  'summary': 's'},
                 {'priority': 'normal', 'status': 'error', 'goal': 'g', 'task_id': 'code-3',
                  'summary': 's'}]
        payloads = [
            {'task_id': 'code-4', 'agent_id': 'a4', 'status': 'needs_input', 'goal': RAW,
             'recap': RAW, 'tests': {'passed': 2, 'failed': 1}},
            {'task_id': 'code-5', 'agent_id': 'a5', 'status': 'failed', 'forge_status': 'lost',
             'role': 'review', 'goal': 'g', 'error': 'x' * 400},
            {'task_id': 'code-6', 'agent_id': 'task', 'status': 'failed', 'forge_status': 'lost',
             'role': 'task', 'goal': '', 'started_at': '', 'finished_at': 'F', 'error': '',
             'recap': ''},
            {'task_id': 'code-7', 'agent_id': 'a7', 'status': 'foreign_status', 'goal': 'g',
             'recap': 'r'}]
        seen = {}
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui, language):
                    wake = forge.wake_invitation(items)
                    event = subagents.invitation(payloads)
                for value in (wake, event):
                    self.assertIn(RAW, value)
                for literal in ('code-1', 'code-2', 'code-3 ', 'error', '[code-4 / a4]',
                                '[code-5 / a5]', '[code-6]', 'foreign_status'):
                    self.assertIn(literal.strip(), wake + event)
                with fl.use(language):
                    self.assertIn(fl.text('forge.learning.contract.unmet'), wake)
                    self.assertIn(fl.text('subagents.verb.lost'), event)
                    self.assertIn(fl.text('subagents.lost.no_recap'), event)
                    mark = fl.text('subagents.clipped', count='0').split('0')[0]
                    self.assertIn(mark, event, 'visible truncation stays visible')
                self.assertEqual(seen.setdefault(language, (wake, event)), (wake, event),
                                 'the interface language must not leak into the agent frame')
                if language != 'ru':
                    owned = (wake + event).replace(RAW, '').replace('не выполнен', '')
                    self.assertIsNone(CYRILLIC.search(owned), language)
        self.assertEqual(len({v for v in seen.values()}), 4)

    def test_russian_frame_keeps_the_established_wording(self):
        items = [{'priority': 'normal', 'status': 'stalled', 'goal': 'g', 'task_id': 't',
                  'summary': 's'}]
        with fl.use('ru'):
            text = forge.wake_invitation(items)
        self.assertIn('упёрся в потолок ходов (работа частичная, diff на месте)', text)
        self.assertIn('это твой плод, а не задача-повинность', text)

    def test_upstream_store_keeps_canonical_markers_while_display_follows_language(self):
        url = 'https://github.com/example/repository'
        for language in fl.LANGUAGES:
            with fl.use(language), patch.object(forge, '_ls_remote_head',
                                                return_value=('a' * 40, '')):
                for path in (forge._UPSTREAMS, forge._UPSTREAMS_RETIRED):
                    path.unlink(missing_ok=True)
                added = forge.upstream_lever('watch', arg=url)
                self.assertIn(url, added)
                self.assertIn(fl.text('forge.upstream.watch_new', count='1'), added)
                forge._UPSTREAM_STATE['ts'] = 0.0
                forge.check_upstreams(force=True)
                stored = json.loads(forge._UPSTREAMS.read_text(encoding='utf-8'))
                row = next(iter(stored.values()))
                self.assertEqual(row['known_source'], 'первый вопрос remote, не твой взгляд',
                                 'stored marker stays canonical across languages')
                listing = forge.upstream_lever('watching')
                self.assertIn(fl.text('forge.upstream.source.first_probe'), listing)
                gone = forge.upstream_lever('unwatch', arg=url)
                self.assertEqual(gone, fl.text('forge.upstream.unwatched', urls=url))
                retired = json.loads(forge._UPSTREAMS_RETIRED.read_text(encoding='utf-8'))
                self.assertEqual(set(retired), {row['key']})
                self.assertEqual(forge.upstream_lever('nonsense'),
                                 fl.text('forge.upstream.actions'))
                self.assertEqual(forge.upstream_lever('watch', arg=RAW),
                                 fl.text('forge.upstream.no_url', arg=RAW))
                if language != 'ru':
                    self.assertIsNone(CYRILLIC.search(added + gone))

    def test_raw_git_errors_are_preserved_and_only_placeholders_are_owned(self):
        class Result:
            returncode = 128
            stdout = ''
            stderr = 'fatal: repository not found ' + RAW

        with fl.use('en'), patch.object(forge.subprocess, 'run', return_value=Result()):
            sha, error = forge._ls_remote_head('https://user:secret@example.invalid/a/b')
        self.assertEqual(sha, '')
        self.assertTrue(error.startswith('fatal: repository not found'))
        Result.stderr = ''
        with fl.use('fr'), patch.object(forge.subprocess, 'run', return_value=Result()):
            self.assertEqual(forge._ls_remote_head('https://example.invalid/a/b'),
                             ('', fl.text('forge.upstream.probe_silent')))


if __name__ == '__main__':
    unittest.main()
