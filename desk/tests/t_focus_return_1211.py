"""Actual core, alarm and BotTransport; network and model are forbidden fixtures."""
import json
import types
import unittest
from unittest import mock

from desk.tests import t_continuity_core as fixtures
from desk.tests.t_continuity_core import agent, tasks, media, AlarmClock
import botapi
import runner
from telegram_receipts import UnknownDelivery, BotApiDeliveryRefused


class FocusReturn(unittest.TestCase):
    setUp = fixtures.ContinuityTests.setUp
    _model = fixtures.ContinuityTests._model
    _pause = fixtures.ContinuityTests._pause

    def configure(self):
        cfg = {'owner':{'name':'Local owner'},'telegram':{'owner_id':'42','bot_token':'fixture'}}
        self.cfg.write_text(json.dumps(cfg),encoding='utf-8')
        self.bot = botapi.BotTransport(agent,self.base,types.SimpleNamespace(record_message=mock.Mock()),cfg)
        self.bot.me = {'id':99}
        self.bot.client = mock.Mock(_base='https://api.telegram.org/botfixture/')
        self.bot.client.call.return_value = {'message_id':123,'date':1700000000}
        self.bot.client.upload.return_value = {'message_id':124,'date':1700000000}
        self.adapter.telegram = lambda:self.bot
        return cfg

    def create(self, room='42'):
        ctx = agent.ChannelContext(chat_id=room,principal_id='42',owner=True,
                                   known=True,addressed=True,is_dm=True)
        return agent._create_durable_run(ctx=ctx,kind='chat_turn',goal='return fixture',
                                        conversation='owner asked here',history=[])

    def alarm(self):
        for patch in (mock.patch.object(tasks,'TASKS',self.base/'memory/tasks.json'),
                      mock.patch.object(tasks,'add',tasks.add),mock.patch.object(tasks,'_save',tasks._save)):
            patch.start();self.addCleanup(patch.stop)
        clock = AlarmClock(self.adapter,tasks);clock.install()
        token = agent._TURN_CHANNEL.set(agent.ChannelContext(chat_id='42',principal_id='42',owner=True,is_dm=True))
        try: task = tasks.add('note',goal='return this result here')
        finally: agent._TURN_CHANNEL.reset(token)
        return clock,task

    def test_alarm_remembers_owner_telegram_and_rejects_changed_bot(self):
        cfg = self.configure();clock,task = self.alarm()
        stored = tasks._load()[0]
        self.assertEqual(clock.room(stored),'42')
        self.assertNotIn('fixture',json.dumps(stored['helene_origin']))
        cfg['telegram']['bot_token']='replacement'
        self.cfg.write_text(json.dumps(cfg),encoding='utf-8')
        with self.assertRaises(ValueError): clock.room(stored)

    def test_interleaved_addition_does_not_inherit_telegram_origin(self):
        import threading
        self.configure();original=tasks.add
        def interleaved(*args,**kwargs):
            thread=threading.Thread(target=lambda:original('note',goal='unrelated addition'))
            thread.start();thread.join()
            return original(*args,**kwargs)
        with mock.patch.object(tasks,'TASKS',self.base/'memory/tasks.json'), \
                mock.patch.object(tasks,'add',interleaved),mock.patch.object(tasks,'_save',tasks._save):
            clock=AlarmClock(self.adapter,tasks);clock.install()
            token=agent._TURN_CHANNEL.set(agent.ChannelContext(chat_id='42',principal_id='42',owner=True,is_dm=True))
            try: tasks.add('note',goal='owner addition')
            finally: agent._TURN_CHANNEL.reset(token)
            rows=tasks._load()
        self.assertNotIn('helene_origin',rows[0])
        self.assertEqual(rows[1]['helene_origin']['room'],'42')

    def test_alarm_run_is_linked_to_telegram_then_reconciled_once(self):
        self.configure();clock,task = self.alarm()
        created=[]
        def invoke(room):
            self.assertEqual(room,'42')
            run=self.create();created.append(run.run_id)
            self.assertTrue(self.adapter.deliver_telegram_text(run.run_id,room,'finished'))
        clock.fire(task,invoke);clock.reconcile_claims()
        self.assertEqual(tasks._load()[0]['status'],'done')
        self.assertEqual(self.manager.run_ids(),created)
        self.bot.client.call.assert_called_once()

    def test_acceptance_before_core_ack_replays_without_second_http(self):
        self.configure();run=self.create()
        with mock.patch.object(agent,'run_delivery_text_chunk_accepted',side_effect=OSError('WAL unavailable')):
            with self.assertLogs('helene.continuity',level='ERROR'):
                self.assertFalse(self.adapter.deliver_telegram_text(run.run_id,'42','finished'))
        self.assertNotEqual(self.manager.manifest(run.run_id)['status'],'done')
        self.adapter.deliver_pending_text();self.adapter.deliver_pending_text()
        self.assertEqual(self.bot.client.call.call_count,1)
        self.assertEqual(self.manager.manifest(run.run_id)['status'],'done')
        rows=(self.base/'memory/groups/42.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(rows),1)
        self.assertFalse((self.base/'memory/groups/window.jsonl').exists())

    def test_ambiguous_http_keeps_text_and_never_calls_it_silence(self):
        self.configure();run=self.create()
        self.bot.client.call.side_effect=TimeoutError('reply lost')
        with self.assertLogs('helene.continuity',level='ERROR'):
            self.assertFalse(self.adapter.deliver_telegram_text(run.run_id,'42','valuable answer'))
            self.adapter.deliver_pending_text()
        self.assertEqual(self.bot.client.call.call_count,1)
        self.assertNotEqual(self.manager.manifest(run.run_id)['status'],'done')
        pending=agent.run_pending_text_deliveries()
        self.assertEqual(pending[0]['pending_chunks'][0]['text'],'valuable answer')
        self.assertFalse(any(e.get('type')=='delivery_skipped' for e in self.manager.iter_events(run.run_id)))

    def test_permanent_refusal_is_not_success(self):
        self.configure();run=self.create()
        self.bot.client.call.side_effect=botapi.BotApiError('sendMessage',403,'blocked')
        with self.assertLogs('helene.continuity',level='ERROR'):
            self.assertFalse(self.adapter.deliver_telegram_text(run.run_id,'42','kept answer'))
        self.assertEqual(self.manager.manifest(run.run_id)['status'],'failed')

    def test_owner_pause_and_identity_change_prevent_network(self):
        cfg=self.configure();run=self.create()
        plan=self.adapter.prepare_telegram_text(run.run_id,'42','still pending')
        self.manager.request_pause(run.run_id,actor='helene:owner',reason='fixture')
        self.assertFalse(self.adapter.deliver_telegram_plan(plan))
        self.bot.client.call.assert_not_called()
        cfg['telegram']['owner_id']='43';self.cfg.write_text(json.dumps(cfg),encoding='utf-8')
        with self.assertLogs('helene.continuity',level='ERROR'):
            self.assertFalse(self.adapter.deliver_telegram_plan(plan))
        self.bot.client.call.assert_not_called()

    def test_resume_authored_output_returns_to_telegram_without_new_model(self):
        self.configure();run=self.create()
        self._model(run,blocks=[{'type':'text','text':'recovered answer'}],stop_reason='end_turn',text='recovered answer')
        self._pause(run)
        report=agent.resume_durable_run(run.run_id)
        self.assertEqual(report['plan_kind'],'authored_output',report)
        self.adapter.deliver_pending_text()
        self.assertEqual(self.manager.manifest(run.run_id)['status'],'done')
        self.assertEqual(str(self.bot.client.call.call_args.kwargs['chat_id']),'42')

    def test_text_and_file_have_one_complete_delivery_intent(self):
        self.configure();run=self.create()
        import dataclasses
        item=agent._media_spool().resolve_outbound_bytes(b'report',kind='document',filename='report.txt',
            target_chat_id='42',reply_to_message_id=None,scope='owner')
        item=dataclasses.replace(item,run_id=run.run_id)
        envelope=types.SimpleNamespace(run_id=run.run_id,outbound=(item,),failed=False,deferred=False)
        plan=self.adapter.prepare_telegram_text(run.run_id,'42','report ready',media_queue_ids=[item.queue_id])
        with mock.patch.object(runner,'_agent',agent),mock.patch.object(runner,'_bot',self.bot):
            self.assertEqual(runner._deliver_outbound(envelope,'42',intent_prepared=True),1)
        self.assertTrue(self.adapter.deliver_telegram_plan(plan))
        self.assertEqual(self.manager.manifest(run.run_id)['status'],'done')
        self.assertEqual(self.bot.client.upload.call_count,1)
        self.assertEqual(self.bot.client.call.call_count,1)

    def test_alarm_tick_dispatches_to_owner_chat_as_system_note(self):
        self.configure();clock,task=self.alarm()
        with mock.patch.multiple(runner,_agent=agent,_bot=self.bot,_alarms=clock,
                                 _desk=object(),_life=object(),_ALARM_FIRED=[]), \
                mock.patch.object(runner,'_brain_ready',return_value=True), \
                mock.patch.object(runner,'handle_bot') as handle, \
                mock.patch.object(runner,'_turn_in_window') as window:
            runner._fire_due_tasks()
        window.assert_not_called()
        self.assertEqual(handle.call_args.args,('42',))
        self.assertEqual(handle.call_args.kwargs['sender_override'],('Hélène','42'))
        self.assertIn('return this result here',handle.call_args.kwargs['origin_text'])

    def test_actual_bot_boundary_retains_failed_result_without_silence(self):
        from contextlib import nullcontext
        import turn_pulse,control_watch
        self.configure();run=self.create()
        self.bot.rooms.record('42','please finish',outgoing=False,sender='Owner',source_id='original')
        self.bot.client.call.side_effect=TimeoutError('unknown result')
        envelope=types.SimpleNamespace(run_id=run.run_id,text='work is finished',outbound=(),failed=False,deferred=False)
        with mock.patch.multiple(runner,_agent=agent,_bot=self.bot,_continuity=self.adapter), \
                mock.patch.object(runner,'_run_turn',return_value=envelope), \
                mock.patch.object(runner,'_close_run') as close, \
                mock.patch.object(runner,'_set_busy'),mock.patch.object(runner,'_room_title',return_value='Owner'), \
                mock.patch.object(runner,'_last_n',return_value=10), \
                mock.patch.object(runner,'_telegram_media_refs',return_value=([],[])), \
                mock.patch.object(turn_pulse,'TurnPulse'), \
                mock.patch.object(control_watch,'delivery',return_value=nullcontext(True)), \
                self.assertLogs('helene.continuity',level='ERROR'):
            runner.handle_bot('42',sender_override=('Owner','42'))
        close.assert_not_called()
        self.assertEqual(agent.run_pending_text_deliveries()[0]['pending_chunks'][0]['text'],'work is finished')
        self.assertNotEqual(self.manager.manifest(run.run_id)['status'],'done')

    def test_file_receipt_gap_retains_spool_and_recovers_without_upload(self):
        import dataclasses
        self.configure();run=self.create()
        item=agent._media_spool().resolve_outbound_bytes(b'report',kind='document',filename='report.txt',
            target_chat_id='42',reply_to_message_id=None,scope='owner')
        item=dataclasses.replace(item,run_id=run.run_id)
        envelope=types.SimpleNamespace(run_id=run.run_id,outbound=(item,),failed=False,deferred=False)
        self.adapter.media_sender=runner.deliver_one_media
        with mock.patch.object(runner,'_agent',agent),mock.patch.object(runner,'_bot',self.bot):
            with mock.patch.object(agent,'run_delivery_media_result',side_effect=OSError('lost core ack')), \
                    self.assertLogs(runner.log,level='ERROR'):
                self.assertEqual(runner._deliver_outbound(envelope,'42'),0)
            self.assertEqual(len(agent._media_spool().pending()),1)
            self.assertEqual(self.adapter.deliver_pending_media(),1)
            self.adapter.deliver_pending_media()
        self.assertEqual(self.bot.client.upload.call_count,1)
        self.assertEqual(self.manager.manifest(run.run_id)['status'],'done')

    def test_later_chunk_ambiguity_does_not_repeat_accepted_prefix(self):
        self.configure();run=self.create()
        self.bot.client.call.side_effect=[{'message_id':123},TimeoutError('second response lost')]
        with self.assertLogs('helene.continuity',level='ERROR'):
            self.assertFalse(self.adapter.deliver_telegram_text(run.run_id,'42','a'*4500))
            self.adapter.deliver_pending_text()
        self.assertEqual(self.bot.client.call.call_count,2)
        self.assertEqual(len(agent.run_pending_text_deliveries()[0]['pending_chunks']),1)


if __name__=='__main__': unittest.main()
