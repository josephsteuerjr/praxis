"""Self-proposal replies follow the agent-context language; ledger and journal markers do not.

Canonical statuses, git commit titles, journal tags read by the panel and stored reasons stay
exact. The interface language is independent of the agent-context language.
"""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import frame_language as fl
import selfdev
import test_selfdev as fixtures

CYRILLIC = re.compile('[А-Яа-яЁё]')
RAW = 'literal Русский 中文 {v1} <inert>'


class SelfdevLanguage(unittest.TestCase):
    setUp = fixtures.SelfdevFlow.setUp
    tearDown = fixtures.SelfdevFlow.tearDown
    _begin_and_edit = fixtures.SelfdevFlow._begin_and_edit

    def settings(self, ui, language):
        return patch.object(fl, 'settings',
                            return_value={'lang': ui, 'agent': {'frame_language': language}})

    def journal(self):
        return "".join(p.read_text(encoding='utf-8')
                       for p in sorted(selfdev.JOURNAL_DIR.glob('*.md')))

    def test_refusals_policy_and_listing_in_every_pairing(self):
        seen = {}
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                selfdev.POLICY_FILE.unlink(missing_ok=True)
                with self.settings(ui, language):
                    replies = [
                        selfdev.submit('missing0', 'title'),
                        selfdev.autonomy_change('add', ''),
                        selfdev.autonomy_change('add', 'notes/*'),
                        selfdev.autonomy_change('add', 'notes/*'),
                        selfdev.autonomy_change('list'),
                        selfdev.autonomy_change('remove', 'nothing/*'),
                    ]
                    self.assertEqual(replies[0].submission_status, 'refused')
                    self.assertEqual(replies[0], fl.text('selfdev.submit.unknown', pid='missing0'))
                    self.assertEqual(replies[3], fl.text('selfdev.autonomy.already',
                                                         pattern='notes/*'))
                    self.assertEqual(selfdev.policy()['auto'][-1], 'notes/*',
                                     'the policy file stores the glob exactly')
                self.assertEqual(seen.setdefault(language, replies), replies)
                if language != 'ru':
                    self.assertIsNone(CYRILLIC.search(''.join(replies)), language)

    def test_ledger_statuses_reasons_and_journal_tags_stay_exact(self):
        rows = [{'id': 'aa', 'status': 'merged', 'title': RAW, 'tests': {'status': 'passed'},
                 'diffstat': '1 file changed', 'reason': RAW},
                {'id': 'bb', 'status': 'foreign', 'why': 'w', 'tests': {'status': 'odd'}},
                {'id': 'cc', 'status': 'building', 'tests': None}]
        for language in fl.LANGUAGES:
            with fl.use(language), patch.object(selfdev, '_load', return_value=rows):
                text = selfdev.list_text()
                for literal in ('#aa', '#bb', '#cc', RAW, '[foreign]', 'odd', '1 file changed'):
                    self.assertIn(literal, text)
                self.assertIn(fl.text('selfdev.list.status.merged'), text)
                self.assertIn(fl.text('selfdev.list.no_diff'), text)
            with fl.use(language), patch.object(selfdev, '_load', return_value=[]):
                self.assertEqual(selfdev.list_text(), fl.text('selfdev.list.empty'))

    def test_real_reject_keeps_canonical_journal_and_ledger_in_each_language(self):
        for language in fl.LANGUAGES:
            pid = self._begin_and_edit('core.py', 'VALUE = 2\n')
            with fl.use(language):
                result = selfdev.reject(pid, reason=RAW)
                again = selfdev.reject(pid)
            self.assertTrue(result['ok'])
            self.assertEqual(result['msg'], fl.catalog(language)['selfdev.reject.done'])
            self.assertFalse(again['ok'])
            row = selfdev.get(pid)
            self.assertEqual((row['status'], row['reason']), ('rejected', RAW))
            self.assertFalse(selfdev.worktree_path(pid).exists())
            line = next(l for l in self.journal().splitlines() if pid in l and RAW in l)
            self.assertIn(': ' + RAW, line)
            self.assertIn('(s3) [предложение]', self.journal(), 'journal tag stays canonical')

    def test_empty_review_refusal_names_the_diff_hand_and_changes_nothing(self):
        pid = self._begin_and_edit('core.py', 'VALUE = 2\n')
        ledger = selfdev.LEDGER.read_bytes()
        for language in fl.LANGUAGES:
            with fl.use(language):
                reply = selfdev.submit(pid, 'title', review='')
                self.assertEqual(reply.submission_status, 'refused')
                self.assertIn(f'proposal(action="diff", id="{pid}")', reply)
                self.assertIn(fl.text('selfdev.review.empty'), reply)
            self.assertEqual(selfdev.LEDGER.read_bytes(), ledger)
            self.assertEqual((self.repo / 'core.py').read_text(), 'VALUE = 1\n')


if __name__ == '__main__':
    unittest.main()
