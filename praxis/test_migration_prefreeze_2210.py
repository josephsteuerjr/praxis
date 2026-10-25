"""Migration notice must survive non-delivery builds and reach the voice boundary.

Run via praxis_test.py: disposable canonical state, no provider/Telegram calls.
Construction acknowledgement is not an inference or delivery guarantee.
"""
import json
import os
from unittest import mock

import agent
import compaction_migration as migration
import frame_epoch as fe
import frame_layout
import group_context as gc
import mtproto_runner as runner
from test_coverage_vs_current import Base, ROOM


class MigrationPrefreeze(Base):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.dict(os.environ, {
            'PRAXIS_FRAME_EPOCH': '1', 'PRAXIS_FRAME_EPOCH_ROOMS': '*',
            'PRAXIS_FRAME_HEAD_STABLE': '1', 'PRAXIS_FRAME_EPOCH_MAX_CHARS': '400000',
            'PRAXIS_FRAME_TAPE_HANDS': 'off', 'PRAXIS_KEAT_CAPTURE': 'off',
            'PRAXIS_DIALOGUE_ROLES': '1'}))
        for module, name, value in ((agent, 'BASE', self.tmp),
                                    (fe, 'STORE', self.tmp / 'epochs'),
                                    (gc, 'GROUPS_DIR', self.tmp / 'groups'),
                                    (gc, '_KEY_CACHE', {})):
            self.enterContext(mock.patch.object(module, name, value))
        self.enterContext(mock.patch.object(agent, '_recall_block', return_value=''))
        self.addCleanup(fe.reset)
        self.addCleanup(frame_layout.reset)
        migration.apply(self.tmp, sha='a' * 40, reason='Synthetic prefreeze regression')
        self.ctx = agent.ChannelContext(chat_id=ROOM, room_id=ROOM, is_dm=False,
                                        principal_id=2, owner=False)

    def kinds(self):
        return [json.loads(line)['kind']
                for line in migration._ledger(self.tmp).read_text().splitlines()]

    def test_freeze_then_voice_retains_notice_until_model_input_construction(self):
        self._msg(6, 'exact uncovered tail')
        route = runner.telegram_topics.route_from_conversation_id(ROOM)
        with mock.patch.object(runner, '_route_from_state', return_value=route), \
             mock.patch.object(runner, '_room_policy_for_state', return_value={'context_hot': 1}):
            wake = runner._group_trigger_snapshot(ROOM, mid=6, message_ts=6,
                kind='mention', addressed=True, query='exact uncovered tail', name='Alice',
                sender_id=2, is_owner=False, known=True, family=False)
        self.assertIn('envelope', wake.epoch_snapshot)
        self.assertNotIn('compaction_deployment_change', str(wake.epoch_snapshot['envelope']))
        self.assertEqual(self.kinds(), ['planned', 'applied'],
                         'discarded prefreeze prompt consumed the pending notice')
        history, current = runner._group_dialogue(wake.turns_snapshot, exact=True)
        current = current or wake.context_snapshot
        captured = {}
        frozen = wake.epoch_snapshot
        with fe.bind(ROOM, frozen['anchor'], tape=frozen), \
             mock.patch.object(agent, '_terminal_tool_loop',
                               side_effect=lambda **kw: captured.update(kw) or 'ok'):
            result = agent._voice_impl(current, history, 'Alice', tools_override=[], ctx=self.ctx)
        self.assertEqual(result, 'ok')
        # Inspect the real voice-to-terminal-loop input, not a standalone builder.
        model_input = str(captured['messages'])
        self.assertIn('compaction_deployment_change', model_input)
        self.assertIn('helper compaction removed globally', model_input)
        self.assertIn('Rollback:', model_input)
        self.assertEqual(self.kinds(), ['planned', 'applied', 'context_shown'])

    def test_default_builder_and_system_only_wrapper_leave_notice_pending(self):
        parts = agent._build_prompt_parts(speaker='Alice', query='hello', ctx=self.ctx)
        self.assertEqual(len(parts), 3)  # legacy call/return shape remains usable
        self.assertNotIn('compaction_deployment_change', parts[2])
        self.assertEqual(len(agent.build_system_parts(ctx=self.ctx)), 2)
        self.assertEqual(self.kinds(), ['planned', 'applied'])

    def test_explicit_model_input_builder_opt_in(self):
        _, _, evidence = agent._build_prompt_parts(ctx=self.ctx, include_deployment_notice=True)
        self.assertIn('compaction_deployment_change', evidence)
        self.assertEqual(self.kinds(), ['planned', 'applied', 'context_shown'])
