"""Authorized attachment custody before text, recovery and owner cancellation."""
import asyncio
from contextlib import contextmanager, ExitStack
from types import SimpleNamespace
import threading

from collections import defaultdict, deque
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_group_wake_snapshot import runner, agent, media, _Client, _wake, _no_compact
import run_context
import run_manager


class MediaHandoff(unittest.IsolatedAsyncioTestCase):
    def test_durable_text_retirement_keeps_media_debt_and_never_replays_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = run_manager.RunManager(tmp)
            context = run_context.RunContext.create(kind='test', goal='handoff',
                principal_id='owner', scope='owner', origin_chat_id='1', delivery_chat_id='1')
            current = manager.create(context, 'immutable input')
            manager.transition(current.run_id, 'running')
            rid = current.run_id
            plan = {'schema': agent._TELEGRAM_TEXT_PLAN_SCHEMA,
                    'conversation_id': '1', 'peer_id': '1', 'topic_id': None,
                    'chunks': [{'index': 0, 'text': 'old draft',
                                'delivery_key': f'run:{rid}:chunk:0', 'reply_to': None}]}
            with patch.object(agent, '_RUN_MANAGER', manager):
                agent.run_delivery_started(rid, chat_id='1', text_chars=9, media_count=1,
                    text_plan=plan, media_queue_ids=['authorized-file'])
                self.assertTrue(agent._delivery_evidence(rid)['replayable_text'])
                self.assertTrue(agent.run_delivery_text_superseded(rid))
                self.assertTrue(agent.run_delivery_text_superseded(rid))
                # Fresh manager reads the event; not process-local state.
                with patch.object(agent, '_RUN_MANAGER', run_manager.RunManager(tmp)):
                    evidence = agent._delivery_evidence(rid)
                    self.assertFalse(evidence['replayable_text'])
                    self.assertFalse(agent.run_pending_text_deliveries())
                    self.assertEqual(evidence['pending_media_queue_ids'], ['authorized-file'])
                    self.assertFalse(evidence['ready'])
                    agent.run_delivery_media_started(rid, 'authorized-file')
                    agent.run_delivery_media_result(rid, 'authorized-file', ok=True, message_id=92)
                    self.assertTrue(agent._delivery_evidence(rid)['ready'])
                events = manager.events(rid)
                self.assertEqual(sum(e.get('kind') == 'telegram_text_superseded' for e in events), 1)
                self.assertTrue(any(e.get('args', {}).get('text_chars') == 9 for e in events))

    async def test_text_cancellation_keeps_exact_upload_and_does_not_reauthor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spool = media.MediaSpool(root / 'spool')
            source = root / 'report.txt'
            source.write_text('original attachment', encoding='utf-8')
            item = spool.resolve_outbound(source, kind='document',
                                          target_chat_id='-704', scope='group')
            wake = _wake(mid=40, ts=990.0, context='answer')
            wakes = {'-704': wake}
            client = _Client()
            async def interrupted(*args, **kwargs):
                self.assertEqual(spool.pending()[0].queue_id, item.queue_id)
                raise asyncio.CancelledError
            client.send_message = interrupted
            turn = Mock(return_value=media.TurnEnvelope(text='answer', outbound=(item,)))
            arm = Mock()
            with (
                patch.object(runner, 'client', client),
                patch.object(runner, '_meta', {'-704': {
                    'entity': -704, 'is_dm': False, 'title': 'room', 'size': 3,
                    'room_mode': 'normal', 'addressed': False}}),
                patch.object(runner, '_group_wakes', wakes),
                patch.object(runner, '_last_pass', defaultdict(float)),
                patch.object(runner, '_passing', set()),
                patch.object(runner, '_pending_media', defaultdict(lambda: deque(maxlen=16))),
                patch.object(runner, '_recent_msgs', defaultdict(lambda: deque(maxlen=12))),
                patch.object(runner, '_missed', {}),
                patch.object(runner, '_cooldown', return_value=0.0),
                patch.object(runner, '_maybe_compact', side_effect=_no_compact),
                patch.object(runner, '_arm', arm),
                patch.object(runner, '_MEDIA_SPOOL', spool),
                patch.object(agent, 'voice_turn_envelope', turn),
            ):
                with self.assertRaises(asyncio.CancelledError):
                    await runner._run_pass('-704')
                await asyncio.sleep(0)
            turn.assert_called_once()
            # Restart reads the persisted item, not the vanished original input.
            recovered = media.MediaSpool(root / 'spool').pending()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0].queue_id, item.queue_id)
            self.assertEqual(recovered[0].reply_to_message_id, 40)
            self.assertEqual(recovered[0].path.read_text('utf-8'), 'original attachment')
            self.assertNotIn('-704', wakes)
            arm.assert_not_called()

    async def test_explicit_owner_cancel_stops_first_send_and_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            spool = media.MediaSpool(Path(tmp) / 'spool')
            source = Path(tmp) / 'report.txt'
            source.write_text('payload', encoding='utf-8')
            item = replace(spool.resolve_outbound(source, kind='document',
                target_chat_id='100', scope='owner'), run_id='run-cancel')
            ctx = agent.ChannelContext(chat_id='100', is_dm=True, owner=True, known=True)
            network = AsyncMock(side_effect=AssertionError('cancel reached network'))
            with (patch.object(runner, '_MEDIA_SPOOL', spool),
                  patch.object(runner, '_run_cancel_requested', return_value=True),
                  patch.object(runner, '_send_turn_media', network)):
                runner._stage_authorized_media((item,), reply_to=7)
                self.assertFalse(await runner._attempt_queued_media(100, item, ctx=ctx))
                runner._stage_authorized_media((item,), reply_to=7)
                self.assertFalse(spool.pending())
                self.assertEqual(len(spool.outbox_results('failed')), 1)
            network.assert_not_awaited()
            self.assertTrue(item.path.exists(), 'cancel keeps the retained source')


    @contextmanager
    def _durable_handoff_fixture(self, root):
        """Isolate durable state; leave delivery/cancellation reducers real."""
        manager = run_manager.RunManager(root)
        context = run_context.RunContext.create(
            kind="chat_turn", goal="deliver attachment",
            principal_id="101", scope="group",
            origin_chat_id="-704", delivery_chat_id="-704",
        )
        current = manager.create(context, "immutable input")
        manager.transition(current.run_id, "running", expected="pending")
        spool = media.MediaSpool(root / "spool")
        source = root / "report.txt"
        source.write_text("original attachment", encoding="utf-8")
        item = replace(
            spool.resolve_outbound(
                source, kind="document", target_chat_id="-704", scope="group",
            ),
            run_id=current.run_id,
        )
        wake = _wake(mid=40, ts=990.0, context="answer")
        wakes = {"-704": wake}
        clock = [1_900_000_000.0]
        debounce = {}
        parked = asyncio.Event()
        client = _Client()
        client.send_file = AsyncMock(return_value=SimpleNamespace(id=92))
        no_text = AsyncMock(
            side_effect=AssertionError("superseded draft reached transport")
        )

        async def parked_successor(_chat_id):
            await parked.wait()

        # _run_pass clears notices before calling the model.
        from core import notices

        with ExitStack() as stack:
            patches = (
                patch.object(agent, "_RUN_MANAGER", manager),
                patch.object(agent, "_MEDIA_SPOOL", spool),
                patch.object(agent, "BASE", root),
                patch.object(agent, "project_delivery_outcome"),
                patch.object(runner, "_MEDIA_SPOOL", spool),
                patch.object(runner, "client", client),
                patch.object(runner, "_meta", {"-704": {
                    "entity": -704, "peer_id": "-704", "topic_id": None,
                    "is_dm": False, "title": "room", "size": 3,
                    "room_mode": "normal", "addressed": True,
                }}),
                patch.object(runner, "_group_wakes", wakes),
                patch.object(runner, "_last_pass", defaultdict(float)),
                patch.object(runner, "_passing", set()),
                patch.object(runner, "_pending_media",
                             defaultdict(lambda: deque(maxlen=16))),
                patch.object(runner, "_recent_msgs",
                             defaultdict(lambda: deque(maxlen=12))),
                patch.object(runner, "_missed", {}),
                patch.object(runner, "_supersede_gen", {}),
                patch.object(runner, "_debounce", debounce),
                patch.object(runner, "_debounced",
                             side_effect=parked_successor),
                patch.object(runner, "_ONE_MIND", asyncio.Lock()),
                patch.object(runner, "_SHUTDOWN", asyncio.Event()),
                patch.object(runner, "_MODERATION_PRIORITY_PENDING", False),
                patch.object(runner, "_MEDIA_SENDING", set()),
                patch.object(runner, "_MEDIA_ACCEPTED", {}),
                patch.object(runner, "_TEXT_SENDING", set()),
                patch.object(runner, "_cooldown", return_value=0.0),
                patch.object(runner, "_resolve_room_mode",
                             return_value=("normal", False)),
                patch.object(runner, "_room_policy_for_state", return_value={}),
                patch.object(runner, "_group_context_frozen",
                             return_value=(wake.context_snapshot, ())),
                patch.object(runner, "_buf_push"),
                patch.object(runner, "_maybe_compact",
                             side_effect=_no_compact),
                patch.object(runner, "_send_message_idempotent", no_text),
                patch.object(notices, "clear_chat"),
                patch.object(media.time, "time", side_effect=lambda: clock[0]),
            )
            for replacement in patches:
                stack.enter_context(replacement)
            yield manager, spool, item, wakes, clock, client, no_text, debounce

    async def _assert_durable_superseded_handoff(self, *, owner_cancel):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._durable_handoff_fixture(root) as fixture:
                manager, spool, item, wakes, clock, client, no_text, debounce = fixture
                rid = item.run_id
                turn = Mock(return_value=media.TurnEnvelope(
                    text="old draft", outbound=(item,), run_id=rid,
                ))
                loop = asyncio.get_running_loop()
                persisted = asyncio.Event()
                release = threading.Event()
                real_started = agent.run_delivery_started

                def persist_then_hold(*args, **kwargs):
                    result = real_started(*args, **kwargs)
                    loop.call_soon_threadsafe(persisted.set)
                    if not release.wait(10):
                        raise AssertionError("test did not release intent writer")
                    return result

                task = None
                try:
                    with (
                        patch.object(agent, "voice_turn_envelope", turn),
                        patch.object(agent, "run_delivery_started",
                                     side_effect=persist_then_hold),
                    ):
                        task = asyncio.create_task(runner._run_pass("-704"))
                        debounce["-704"] = task
                        await asyncio.wait_for(persisted.wait(), 10)

                        # Prove cancellation lands AFTER durable intent and
                        # BEFORE the offloaded call returns to _run_pass.
                        self.assertTrue(agent._delivery_evidence(rid)["replayable_text"])
                        self.assertEqual(
                            spool.pending()[0].queue_id, item.queue_id,
                        )
                        successor = _wake(mid=41, ts=991.0, context="new question")
                        wakes["-704"] = successor
                        runner._arm("-704")  # real gen bump + task.cancel()
                        self.assertGreater(runner._supersede_gen["-704"], 0)
                        release.set()
                        with self.assertRaises(asyncio.CancelledError):
                            await asyncio.wait_for(task, 10)

                    turn.assert_called_once()
                    no_text.assert_not_awaited()
                    client.send_file.assert_not_awaited()
                    self.assertIs(wakes["-704"], successor)
                    self.assertNotIn("-704", runner._passing)
                    self.assertNotIn(rid, runner._TEXT_SENDING)

                    # Simulated restart: fresh objects and empty process-local
                    # acceptance/in-flight caches, same durable directories.
                    recovered_manager = run_manager.RunManager(root)
                    recovered_spool = media.MediaSpool(root / "spool")
                    with (
                        patch.object(agent, "_RUN_MANAGER", recovered_manager),
                        patch.object(agent, "_MEDIA_SPOOL", recovered_spool),
                        patch.object(runner, "_MEDIA_SPOOL", recovered_spool),
                        patch.object(runner, "_MEDIA_SENDING", set()),
                        patch.object(runner, "_MEDIA_ACCEPTED", {}),
                    ):
                        evidence = agent._delivery_evidence(rid)
                        self.assertFalse(evidence["replayable_text"])
                        self.assertFalse(agent.run_pending_text_deliveries())
                        self.assertEqual(
                            evidence["pending_media_queue_ids"], [item.queue_id],
                        )
                        self.assertFalse(evidence["ready"])
                        self.assertEqual(
                            recovered_manager.manifest(rid)["status"], "running",
                        )
                        recovered = recovered_spool.pending()[0]
                        self.assertEqual(recovered.run_id, rid)
                        self.assertEqual(recovered.reply_to_message_id, 40)
                        self.assertEqual(
                            recovered.path.read_text("utf-8"),
                            "original attachment",
                        )
                        ctx = agent.ChannelContext(
                            chat_id="-704", is_dm=False, known=True,
                            _scope_override="group",
                        )

                        if owner_cancel:
                            requested = recovered_manager.request_cancel(
                                rid, actor="owner", reason="do not send attachment",
                            )
                            self.assertEqual(requested["status"], "paused")
                            self.assertEqual(requested["control"]["action"], "cancel")

                        clock[0] += 29.999
                        self.assertFalse(await runner._attempt_queued_media(
                            -704, recovered, ctx=ctx,
                        ))
                        client.send_file.assert_not_awaited()

                        clock[0] += 0.001
                        if owner_cancel:
                            # The first attempt durably failed the queue item.
                            # Restaging/replaying cannot revive that identity.
                            runner._stage_authorized_media((recovered,), reply_to=40)
                            self.assertFalse(await runner._attempt_queued_media(
                                -704, recovered, ctx=ctx,
                            ))
                            self.assertFalse(recovered_spool.pending())
                            self.assertEqual(
                                len(recovered_spool.outbox_results("failed")), 1,
                            )
                            self.assertTrue(recovered.path.exists())
                            client.send_file.assert_not_awaited()
                        else:
                            self.assertTrue(await runner._attempt_queued_media(
                                -704, recovered, ctx=ctx,
                            ))
                            client.send_file.assert_awaited_once()
                            self.assertEqual(
                                client.send_file.await_args.kwargs["reply_to"], 40,
                            )
                            self.assertFalse(recovered_spool.pending())
                            self.assertEqual(
                                len(recovered_spool.outbox_results("delivered")), 1,
                            )
                            self.assertTrue(agent._delivery_evidence(rid)["ready"])
                            self.assertEqual(
                                recovered_manager.manifest(rid)["status"], "done",
                            )
                            self.assertFalse(recovered_manager.outstanding_tools(rid))
                            # Even a stale retained object cannot be sent twice.
                            self.assertFalse(await runner._attempt_queued_media(
                                -704, recovered, ctx=ctx,
                            ))
                            client.send_file.assert_awaited_once()

                        await runner._text_outbox_once()
                        no_text.assert_not_awaited()
                        events = recovered_manager.events(rid)
                        self.assertEqual(sum(
                            row.get("kind") == "telegram_text_superseded"
                            for row in events
                        ), 1)
                finally:
                    release.set()
                    pending = set(debounce.values())
                    if task is not None:
                        pending.add(task)
                    for pending_task in pending:
                        if not pending_task.done():
                            pending_task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                    # Drain _maybe_compact while its harmless stub is installed.
                    await asyncio.sleep(0)

    async def test_durable_supersession_restarts_media_without_old_text(self):
        await self._assert_durable_superseded_handoff(owner_cancel=False)

    async def test_real_owner_cancel_blocks_superseded_media_replay(self):
        await self._assert_durable_superseded_handoff(owner_cancel=True)

    async def test_real_owner_cancel_blocks_first_media_send(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self._durable_handoff_fixture(root) as fixture:
                manager, spool, item, _wakes, clock, client, _text, _debounce = fixture
                rid = item.run_id
                agent.run_delivery_started(
                    rid, chat_id="-704", text_chars=0, media_count=1,
                    media_queue_ids=[item.queue_id],
                )
                runner._stage_authorized_media((item,), reply_to=40)
                requested = manager.request_cancel(
                    rid, actor="owner", reason="cancel before first upload",
                )
                self.assertEqual(requested["status"], "paused")
                self.assertEqual(requested["control"]["action"], "cancel")
                # Due already: rejection must be cancellation, not zen pause.
                clock[0] += 30.0
                ctx = agent.ChannelContext(
                    chat_id="-704", is_dm=False, known=True,
                    _scope_override="group",
                )
                self.assertFalse(await runner._attempt_queued_media(
                    -704, item, ctx=ctx,
                ))
                client.send_file.assert_not_awaited()
                self.assertFalse(spool.pending())
                self.assertEqual(len(spool.outbox_results("failed")), 1)
                self.assertTrue(item.path.exists())


if __name__ == '__main__':
    unittest.main()
