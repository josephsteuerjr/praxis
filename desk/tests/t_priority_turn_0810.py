"""Call real handle_bot; no model, transport, pulse thread or subprocess."""
import collections
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'localharness'))
import botapi
import runner


class Captured(Exception): pass


class Priority(unittest.TestCase):
    def setUp(self):
        self.bot = botapi.BotTransport.__new__(botapi.BotTransport)
        self.bot._queue_lock = threading.Lock()
        self.bot._wake_priority = {}
        self.bot._wake_priority_gist = {}
        self.bot._wake_priority_rows = {'chat7':[
            {'id':'chat7:1','sender':'Маша','sender_id':'1','gist':'проверь доставку'},
            {'id':'chat7:2','sender':'Егор','sender_id':'2','gist':'проверь QR'},
        ]}
        self.bot._wake_senders = {'chat7':('Петя','3')}
        self.bot._pending = collections.deque(['ordinary','chat7'])
        self.bot._pending_set = {'ordinary','chat7'}
        self.bot.owner_id = '2'
        self.bot.sent_now = []
        self.bot.rooms = types.SimpleNamespace(meta=lambda chat:{'is_dm':False},
            lines=lambda *args:['Маша: ! проверь доставку','Егор: ! проверь QR','Петя: обычный текст'])
        self.bot.is_allowed = lambda sender: True
        self.seen = []
    def capture(self, *args, **kwargs):
        self.seen.append(kwargs['priority_note'])
        raise Captured()
    def test_real_turn_preserves_author_and_all_raw_priorities(self):
        pulse = types.SimpleNamespace(start=lambda: pulse,stop=lambda **kw:None)
        with patch.multiple(runner,_bot=self.bot,_agent=types.SimpleNamespace(ChannelContext=lambda **kw:kw),
                            _set_busy=lambda *a,**kw:None,_room_title=lambda chat:'Test',_run_turn=self.capture), \
             patch.dict(sys.modules,{'turn_pulse':types.SimpleNamespace(TurnPulse=lambda *a,**kw:pulse)}):
            with self.assertRaises(Captured): runner.handle_bot('chat7')
        self.assertIn('Маша',self.seen[0])
        self.assertIn('проверь доставку',self.seen[0])
        self.assertIn('Егор',self.seen[0])
        self.assertIn('проверь QR',self.seen[0])
        self.assertNotIn('Петя',self.seen[0])
        # A turn that was never accepted must leave both requests pending.
        self.assertEqual(len(self.bot._wake_priority_rows['chat7']),2)
    def test_urgent_room_moves_to_front_without_duplicate(self):
        self.bot._enqueue('chat7',urgent=True)
        self.assertEqual(list(self.bot._pending),['chat7','ordinary'])
        self.bot._enqueue('chat7',urgent=True)
        self.assertEqual(list(self.bot._pending),['chat7','ordinary'])
    def test_restoring_failed_turn_keeps_new_ingress_and_deduplicates(self):
        rows = self.bot.take_priorities('chat7')
        self.bot._wake_priority_rows['chat7'] = [rows[1],{'id':'chat7:3','sender':'Оля','gist':'новое'}]
        self.bot.restore_priorities('chat7',rows)
        self.assertEqual([r['id'] for r in self.bot.take_priorities('chat7')],['chat7:1','chat7:2','chat7:3'])


if __name__ == '__main__': unittest.main(verbosity=2)
