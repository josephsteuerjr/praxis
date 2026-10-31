"""Call real handle_bot; no model, transport, pulse thread or subprocess."""
import collections
import contextlib
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
        temporary = tempfile.TemporaryDirectory(prefix='priority-turn-')
        self.addCleanup(temporary.cleanup)
        self.bot = botapi.BotTransport(types.SimpleNamespace(), Path(temporary.name), None,
                                      {'telegram': {'owner_id': '2', 'allow_from': 'any'}})
        self.bot._queue_lock = threading.Lock()
        self.bot._wake_priority = {}
        self.bot._wake_priority_gist = {}
        self.bot._wake_priority_rows = {'7':[
            {'id':'7:1','sender':'Маша','sender_id':'1','gist':'проверь доставку'},
            {'id':'7:2','sender':'Маша','sender_id':'1','gist':'проверь QR'},
            {'id':'7:3','sender':'Егор','sender_id':'2','gist':'другой запрос'},
        ]}
        self.bot._wake_senders = {'7':('Петя','3')}
        self.bot._pending = collections.deque(['ordinary','7'])
        self.bot._pending_set = {'ordinary','7'}
        self.bot.owner_id = '2'
        self.bot.sent_now = []
        self.bot.rooms = types.SimpleNamespace(meta=lambda chat:{'is_dm':False},
            lines=lambda *args:['Маша: ! проверь доставку','Егор: ! проверь QR','Петя: обычный текст'])
        self.bot.is_allowed = lambda sender: True
        for ident, sender, name, text in [('1', '1', 'Маша', 'проверь доставку'),
                                          ('2', '1', 'Маша', 'проверь QR'),
                                          ('3', '2', 'Егор', 'другой запрос')]:
            publish = self.bot.inputs.capture({'source_message_id': ident, 'sender_name': name},
                room='7', sender_id=sender, owner_id='2', is_dm=False, title='Test', input_text=text)
            publish()
        self.seen = []
    def capture(self, *args, **kwargs):
        self.seen.append(kwargs['priority_note'])
        raise Captured()
    def test_real_turn_preserves_priorities_for_selected_principal_and_restores_on_failure(self):
        pulse = types.SimpleNamespace(start=lambda: pulse,stop=lambda **kw:None)
        with patch.multiple(runner,_bot=self.bot,_agent=types.SimpleNamespace(ChannelContext=lambda **kw:kw),
                            _tree=None,_continuity=types.SimpleNamespace(activation=lambda before:contextlib.nullcontext()),
                            _set_busy=lambda *a,**kw:None,_room_title=lambda chat:'Test',_run_turn=self.capture), \
             patch.dict(sys.modules,{'turn_pulse':types.SimpleNamespace(TurnPulse=lambda *a,**kw:pulse)}):
            with self.assertRaises(Captured): runner.handle_bot('7')
        self.assertIn('Маша',self.seen[0])
        self.assertIn('проверь доставку',self.seen[0])
        self.assertNotIn('Егор',self.seen[0], 'a pending sender cannot borrow another principal\'s authority')
        self.assertIn('проверь QR',self.seen[0])
        self.assertNotIn('Петя',self.seen[0])
        # A turn that was never accepted must leave both requests pending.
        self.assertEqual(len(self.bot._wake_priority_rows['7']),3)
        self.assertEqual(len(self.bot.inputs.items),3)
    def test_urgent_room_moves_to_front_without_duplicate(self):
        self.bot._enqueue('7',urgent=True)
        self.assertEqual(list(self.bot._pending),['7','ordinary'])
        self.bot._enqueue('7',urgent=True)
        self.assertEqual(list(self.bot._pending),['7','ordinary'])
    def test_restoring_failed_turn_keeps_new_ingress_and_deduplicates(self):
        rows = self.bot.take_priorities('7')
        self.bot._wake_priority_rows['7'] = [rows[1],{'id':'7:4','sender':'Оля','gist':'новое'}]
        self.bot.restore_priorities('7',rows)
        self.assertEqual([r['id'] for r in self.bot.take_priorities('7')],['7:1','7:3','7:2','7:4'])


if __name__ == '__main__': unittest.main(verbosity=2)
