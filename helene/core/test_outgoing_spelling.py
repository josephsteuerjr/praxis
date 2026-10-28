import json
import os
import subprocess
import unittest
from unittest import mock
import outgoing_spelling as spelling


class SpellingTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {'PRAXIS_OUTGOING_SPELLING': 'limited'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def proc(self, **kw):
        return mock.patch.object(spelling.subprocess, 'run', **kw)

    def test_review_and_repeat_no_rewrite(self):
        with self.proc(return_value=mock.Mock(stdout='mesage\n')) as run:
            for _ in range(2):
                review, note = spelling.preflight('my mesage')
                self.assertEqual(json.loads(review)['reason'], 'review_required')
                self.assertFalse(note)
            self.assertEqual(run.call_args.kwargs['input'], 'mesage\n')

    def test_exact_authored_bypass(self):
        text = ' mesage\n'
        with self.proc() as run:
            self.assertIsNone(spelling.preflight(text, text)[0])
            for wrong in ('mesage', True, ' mesage\n!'):
                self.assertEqual(json.loads(spelling.preflight(text, wrong)[0])['detail'],
                                 'confirmation_text_mismatch')
            run.assert_not_called()

    def test_unavailable_dictionary_binary_timeout(self):
        for exc in (FileNotFoundError(), subprocess.CalledProcessError(1, 'aspell'),
                    subprocess.TimeoutExpired('aspell', .1)):
            with self.proc(side_effect=exc):
                review, note = spelling.preflight('mesage')
                self.assertIsNone(review)
                self.assertIn('fail-open', note)

    def test_no_partial_review_after_second_dictionary_failure(self):
        with self.proc(side_effect=[mock.Mock(stdout='превет\n'), FileNotFoundError()]):
            self.assertIsNone(spelling.preflight('превет mesage')[0])

    def test_off_budgets_and_invalid_mode(self):
        with self.proc() as run:
            with mock.patch.dict(os.environ, {'PRAXIS_OUTGOING_SPELLING': 'off'}):
                self.assertEqual(spelling.preflight('mesage'), (None, ''))
            with mock.patch.dict(os.environ, {'PRAXIS_OUTGOING_SPELLING': 'wrong'}):
                self.assertIsNone(spelling.preflight('mesage')[0])
            for text in ('x' * 8001, 'mesage ' * 401):
                self.assertIn('budget', spelling.preflight(text)[1])
            run.assert_not_called()

    def test_shared_deadline_and_timeout_no_second_process(self):
        with mock.patch.object(spelling.time, 'monotonic', side_effect=[0, .01, .101]), \
             self.proc(return_value=mock.Mock(stdout='')) as run:
            review, note = spelling.preflight('привет hello')
            self.assertIsNone(review)
            self.assertIn('fail-open', note)
            self.assertEqual(run.call_count, 1)
            self.assertAlmostEqual(run.call_args.kwargs['timeout'], .09)

    def test_masking_and_languages(self):
        words = spelling._words('привет hello @dev_name `mesage` ```превет``` '
                                'https://example.org mesage.py camelCase API Nick reply_to=42')
        self.assertEqual(words, {'ru': ['привет'], 'en_US': ['hello']})

    def test_bounded_findings_no_suggestions(self):
        words = ['mispell' + chr(97 + i) for i in range(12)]
        with self.proc(return_value=mock.Mock(stdout='\n'.join(words + ['unexpected']))):
            review = json.loads(spelling.preflight(' '.join(words))[0])
            self.assertEqual(len(review['findings']), 8)
            self.assertTrue(review['truncated'])


class BoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import agent
        cls.a = agent

    def test_send_review_does_not_deliver_or_consume_dedup(self):
        a = self.a
        with mock.patch.dict(os.environ, {'PRAXIS_OUTGOING_SPELLING': 'limited'}), \
             mock.patch.object(spelling.subprocess, 'run', return_value=mock.Mock(stdout='mesage\n')), \
             mock.patch.dict(a._TELETHON, {'send_message': mock.Mock()}) as hooks, \
             mock.patch.object(a, 'tool_journal'):
            for _ in range(2):
                self.assertEqual(json.loads(a.tool_send_message('42', 'mesage'))['status'], 'not_sent')
            hooks['send_message'].assert_not_called()
            with mock.patch.object(a.stewardship, 'outgoing_denial', return_value=None), \
                 mock.patch.object(a, 'private_record_floor', return_value=None), \
                 mock.patch.object(a._core_secrets, 'credential_floor', return_value=None):
                a.tool_send_message('42', 'mesage', spelling_confirm_text='mesage')
                hooks['send_message'].assert_called_once_with('42', 'mesage')

    def test_reply_preflight_before_guard_spoken_or_transport(self):
        a = self.a
        token = a._TURN_CHANNEL.set(mock.Mock(chat_id=42))
        silence = a._TURN_SILENCE.set(None)
        self.addCleanup(a._TURN_CHANNEL.reset, token)
        self.addCleanup(a._TURN_SILENCE.reset, silence)
        with mock.patch.dict(a._TELETHON, {'reply': mock.Mock()}) as hooks, \
             mock.patch.object(spelling, 'preflight', return_value=('review', '')), \
             mock.patch.object(a, 'guard_outbound_reply') as guard, \
             mock.patch.object(a.work_loop, 'spoken') as spoken, \
             mock.patch.object(a.work_loop, 'note_sent') as note:
            self.assertEqual(a.tool_reply('mesage'), 'review')
            guard.assert_not_called(); spoken.assert_not_called(); note.assert_not_called()
            hooks['reply'].assert_not_called()

    def test_send_fail_open_original_text_and_one_attempt(self):
        a = self.a
        with mock.patch.object(spelling, 'preflight', return_value=(None, 'fail-open')), \
             mock.patch.dict(a._TELETHON, {'send_message': mock.Mock(return_value='delivered')}) as hooks, \
             mock.patch.object(a.stewardship, 'outgoing_denial', return_value=None), \
             mock.patch.object(a, 'private_record_floor', return_value=None), \
             mock.patch.object(a._core_secrets, 'credential_floor', return_value=None), \
             mock.patch.object(a, 'tool_journal'):
            self.assertIn('fail-open', a.tool_send_message('42', 'my mesage'))
            hooks['send_message'].assert_called_once_with('42', 'my mesage')
            hooks['send_message'].reset_mock(side_effect=True)
            hooks['send_message'].side_effect = TimeoutError('unknown')
            a.tool_send_message('42', 'mesage')
            self.assertEqual(hooks['send_message'].call_count, 1)

    def test_reply_exact_bypass_fail_open_corrected_retry(self):
        a = self.a
        token = a._TURN_CHANNEL.set(mock.Mock(chat_id=42))
        silence = a._TURN_SILENCE.set(None)
        self.addCleanup(a._TURN_CHANNEL.reset, token)
        self.addCleanup(a._TURN_SILENCE.reset, silence)
        with mock.patch.dict(os.environ, {'PRAXIS_OUTGOING_SPELLING': 'limited'}), \
             mock.patch.dict(a._TELETHON, {'reply': mock.Mock(return_value='delivered')}) as hooks, \
             mock.patch.object(a, 'guard_outbound_reply', side_effect=lambda text, *args, **kw: text), \
             mock.patch.object(a.work_loop, 'spoken', return_value=[]), \
             mock.patch.object(a.work_loop, 'note_guard'), \
             mock.patch.object(a.work_loop, 'note_sent') as sent, \
             mock.patch.object(a, 'tool_journal'):
            with self.proc(return_value=mock.Mock(stdout='mesage\n')):
                self.assertEqual(json.loads(a.tool_reply('mesage'))['reason'], 'review_required')
            hooks['reply'].assert_not_called(); sent.assert_not_called()
            with self.proc(return_value=mock.Mock(stdout='')):
                a.tool_reply('message')
            hooks['reply'].assert_called_once_with('42', 'message', '')
            hooks['reply'].reset_mock()
            with self.proc() as run:
                a.tool_reply('mesage', spelling_confirm_text='mesage')
                run.assert_not_called()
            hooks['reply'].assert_called_once_with('42', 'mesage', '')
            hooks['reply'].reset_mock()
            with self.proc(side_effect=subprocess.TimeoutExpired('aspell', .1)):
                self.assertIn('fail-open', a.tool_reply('mesage'))
            hooks['reply'].assert_called_once_with('42', 'mesage', '')

    def proc(self, **kw):
        return mock.patch.object(spelling.subprocess, 'run', **kw)

    def test_refusal_type_preserved(self):
        a = self.a
        self.assertIsInstance(spelling.receipt(a.DirectSendRefusal('no'), 'note'), a.DirectSendRefusal)


if __name__ == '__main__':
    unittest.main()
