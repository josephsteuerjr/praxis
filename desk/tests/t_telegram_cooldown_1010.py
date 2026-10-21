"""Use a deterministic clock to prove long/repeated cooldown, no duplicate chunks and cancel."""
from pathlib import Path
import sys
import json
import datetime as dt
import tempfile
import types
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'localharness'), str(Path(__file__).resolve().parents[1])]
import botapi
import control_watch
import runner
from telegram_retry import Cooldown, DeliveryCancelled

class Retry(unittest.TestCase):
    def clock(self, **kwargs):
        state = types.SimpleNamespace(now=0.0)
        def wait(seconds): state.now += seconds
        return state, Cooldown(wait=wait, clock=lambda: state.now, **kwargs)

    def test_long_and_repeated_cooldown_waits_whole_duration(self):
        notes=[]; clock, gate=self.clock(notify=lambda *args:notes.append(args))
        calls=[]
        def operation():
            calls.append(clock.now)
            if len(calls)<3: raise botapi.BotApiError('sendMessage',429,'cooldown',retry_after=95 if len(calls)==1 else 40)
            return {'message_id':8}
        value,retries=gate.call('-1','sendMessage',operation)
        self.assertEqual(calls,[0,97.0,139.0]);self.assertEqual(retries,2)
        snapshot = gate.snapshot('-1')
        self.assertEqual(snapshot['margin_seconds'], 2)
        self.assertEqual(snapshot['state'], 'not_waiting')
        self.assertEqual(gate.snapshot('-other')['state'], 'unknown')
        self.assertEqual(value['message_id'],8);self.assertEqual(notes[-1][2],0)

    def test_orientation_exposes_current_peer_deadline_not_cadence(self):
        from unittest import mock
        clock, gate = self.clock()
        gate.deadlines['-1'] = 97
        bot = botapi.BotTransport.__new__(botapi.BotTransport)
        bot._cooldown = gate
        bot.identity_line = lambda: ''
        bot.status_line = lambda: 'connected'
        with mock.patch.object(runner, '_bot', bot):
            text = runner._orient('-1__topic__5')
        self.assertIn('remaining_seconds', text)
        self.assertIn('97', text)
        self.assertIn('not voluntary cadence', text)

    def test_mtproto_known_refusal_is_exposed_without_hidden_sleep(self):
        import asyncio, threading
        from unittest import mock
        import mtproto
        class FloodWaitError(Exception):
            seconds = 12
        errors = types.ModuleType('telethon.errors')
        errors.FloodWaitError = FloodWaitError
        errors.SlowModeWaitError = type('SlowModeWaitError', (Exception,), {})
        class Future:
            def result(self, timeout): raise FloodWaitError('known refusal')
        client = mtproto.MtprotoClient.__new__(mtproto.MtprotoClient)
        client._thread = object(); client._loop = object()
        async def operation(): return None
        coro = operation()
        try:
            with mock.patch.dict(sys.modules, {'telethon.errors': errors}), mock.patch.object(asyncio, 'run_coroutine_threadsafe', return_value=Future()):
                with self.assertRaises(botapi.BotApiError) as result: client._run(coro)
            self.assertEqual(result.exception.code, 429)
            self.assertEqual(result.exception.retry_after, 12)
        finally: coro.close()
        with tempfile.TemporaryDirectory() as directory:
            constructor = mock.Mock()
            module = types.ModuleType('telethon'); module.TelegramClient = constructor
            with mock.patch.dict(sys.modules, {'telethon': module}):
                instance = mtproto.MtprotoClient(1, 'synthetic', Path(directory)/'session')
            self.assertEqual(constructor.call_args.kwargs['flood_sleep_threshold'], 0)
            instance._loop.close()

    def test_unknown_send_outcome_never_retries(self):
        _clock, gate = self.clock()
        calls = []
        def operation():
            calls.append(1)
            raise TimeoutError('unknown send')
        with self.assertRaises(TimeoutError): gate.call('-1', 'sendMessage', operation)
        self.assertEqual(len(calls), 1)

    def test_cancel_during_wait_prevents_next_send(self):
        clock,gate=self.clock()
        gate.guard=lambda: (_ for _ in ()).throw(DeliveryCancelled('cancel')) if clock.now>=2 else None
        attempts=[]
        def operation():
            attempts.append(clock.now);raise botapi.BotApiError('sendPhoto',429,'cooldown',retry_after=90)
        with self.assertRaises(DeliveryCancelled):gate.call('-1','sendPhoto',operation)
        self.assertEqual(attempts,[0]);self.assertEqual(clock.now,2)

    def test_text_retries_only_unaccepted_chunk(self):
        clock,gate=self.clock()
        bot=botapi.BotTransport.__new__(botapi.BotTransport)
        bot._cooldown=gate;bot.before_send=None;bot.sent_now=[]
        bot.agent=types.SimpleNamespace(DirectSendRefusal=type('Refusal',(str,),{}))
        rows=[];bot.rooms=types.SimpleNamespace(record=lambda *a,**kw:rows.append(a[1]),meta=lambda *a:{'title':'Group'})
        calls=[]
        def call(method,**params):
            calls.append(params['text'])
            if len(calls)==2:raise botapi.BotApiError(method,429,'cooldown',retry_after=70)
            return {'message_id':len(calls)}
        bot.client=types.SimpleNamespace(call=call)
        text='a'*4500
        result=bot.deliver_text('-1',text)
        self.assertEqual(len(calls),3);self.assertEqual(calls[1],calls[2])
        self.assertEqual(''.join(rows),text);self.assertEqual(len(rows),2)
        self.assertIn('после ожидания Telegram',result)

    def test_transport_stop_during_wait_reports_cancelled_not_waiting(self):
        notes=[];clock,gate=self.clock(notify=lambda *args:notes.append(args))
        gate.wait=lambda seconds: (_ for _ in ()).throw(DeliveryCancelled('stop'))
        def operation():raise botapi.BotApiError('sendMessage',429,'cooldown',retry_after=90)
        with self.assertRaises(DeliveryCancelled):gate.call('-1','sendMessage',operation)
        self.assertEqual(notes[-1][2],-2)

    def test_pending_owner_interrupt_is_seen_without_delivery_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory);folder=tree/'memory/.control';folder.mkdir(parents=True)
            (folder/'interrupt.json').write_text(json.dumps({'scope':'all','at':dt.datetime.fromtimestamp(100,dt.timezone.utc).isoformat()}))
            self.assertTrue(control_watch.pending_interrupt(tree,'run',99))
            self.assertFalse(control_watch.pending_interrupt(tree,'run',101))

    def test_boundary_guard_retains_run_identity_after_model_busy_flag_clears(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory);folder=tree/'memory/.control';folder.mkdir(parents=True)
            (folder/'interrupt.json').write_text(json.dumps({'scope':'real-run','at':dt.datetime.fromtimestamp(100,dt.timezone.utc).isoformat()}))
            manager=types.SimpleNamespace(status=lambda run:{'status':'running','created_at':dt.datetime.fromtimestamp(99,dt.timezone.utc).isoformat()})
            agent=types.SimpleNamespace(_runs=lambda:manager)
            with mock.patch.object(runner,'_tree',tree),mock.patch.object(runner,'_busy',{'run':'','since':0}),mock.patch.object(runner,'_agent',agent):
                with control_watch.delivery(lambda:manager,'real-run'):
                    with self.assertRaises(DeliveryCancelled):runner._guard_telegram_send()
                self.assertIsNone(control_watch.active_delivery())

if __name__=='__main__':unittest.main()
