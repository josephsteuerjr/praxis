"""Renderer-to-provider coverage regressions, with synthetic files only."""
import copy
from contextlib import ExitStack
import os
from pathlib import Path
from unittest.mock import patch

import agent
import frame_layout
import frame_serve
import frame_shadow
import keat_economy as economy
import keat_live as live
from test_keat_live import LiveTests


class RendererCoverageTests(LiveTests):
    def check_coverage(self, *, filename='synthetic.md', mention=False,
                       truncated=False, ceiling=False, expected=False):
        self.ctx.owner_audience = True
        self.ctx.room_chat_id = 1
        body = 'telegram_id: 1\n## Synthetic dossier\n' + '- exact synthetic body **fact**\n' * 70
        selected_body = body + ('\nmentions memory/people/synthetic.md\n' if mention else '')
        root = Path(self.tmp.name) / 'sources'
        people = root / 'memory' / 'people'
        people.mkdir(parents=True)
        (people / filename).write_text(selected_body)
        (root / 'soul').mkdir()
        for name in ('SOUL.md', 'VOICE.md'):
            (root / 'soul' / name).write_text('Synthetic constitution')
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {
                'PRAXIS_OWNER_ID': '1', 'PRAXIS_FRAME_V6': 'serve',
                'PRAXIS_FRAME_V6_STREAMS': 'dm-1', 'PRAXIS_FRAME_FORM': 'new'}))
            stack.enter_context(patch.object(frame_shadow, 'BASE', root))
            # Keep the real source selection, lifted renderer, E degradation,
            # frame_measure assembly and frame_serve export. Isolate unrelated
            # runtime sources, never substitute coverage or aggregate E text.
            for name in ('_block_self', '_block_hands', '_block_memory_index',
                         '_block_address_book'):
                stack.enter_context(patch.object(frame_shadow, name, return_value=''))
            stack.enter_context(patch.object(frame_shadow, '_block_mail', return_value=None))
            stack.enter_context(patch.object(frame_shadow, '_block_desires', return_value=('', {})))
            if truncated:
                # Exercise the real renderer at a reduced initial lifting cap.
                render = frame_shadow._render_lifted
                stack.enter_context(patch.object(frame_shadow, '_render_lifted',
                    side_effect=lambda header, src, limit=1000, **kw:
                        render(header, src, limit, **kw)))
            if ceiling:
                stack.enter_context(patch.object(frame_shadow, 'E_TOTAL_MAX', 1000))
            with economy.collect() as rows:
                economy.source('dossier', body, 'memory/people/synthetic.md')
                block = frame_layout.section('Synthetic dossier', body)[0]
                economy.section(body, block)
            evidence = block + '\nRuntime unchanged'
            current = agent._with_context_evidence('current unanswered', evidence, 'situation')
            tape = [dict(role='user', content=current)]
            original = copy.deepcopy(tape)
            state = live.capture_turn(self.ctx, 'current unanswered', [])
            legacy_system = 'LEGACY system'
            with frame_serve.bind(system=legacy_system, ctx=self.ctx, dynamic='transport'):
                system, frame_receipt = frame_serve.select(system=legacy_system, messages=tape, tools=[])
                self.assertEqual(frame_receipt['status'], 'served')
                coverage = frame_serve.economy_coverage(system)['dossier']
                self.assertEqual(set(coverage), {f'memory/people/{filename}'})
                shown = coverage[f'memory/people/{filename}']
                self.assertIn(shown, system)
                if truncated or ceiling:
                    self.assertLess(len(shown), len(selected_body))
                else:
                    self.assertEqual(shown, selected_body)
                with economy.bind(self.ctx, current, evidence, rows), live.bind_turn(state, tape):
                    candidate = economy.candidate(system, tape)
                    self.assertEqual(candidate is not None, expected)
                    provider = live.prepare_provider_messages(system, tape, [])
                    receipt = live.select_provider(system, provider, [], 'r', 'c')
                    self.assertTrue(live.valid_served_receipt(receipt, system=system,
                        messages=provider, tools=[], run_id='r', call_id='c'))
                    self.assertEqual(provider != original, expected)
                    self.assertEqual(tape, original)
                    if expected:
                        self.assertTrue(live.accept_provider_receipt(receipt, system=system,
                            messages=provider, tools=[], run_id='r', call_id='c'))
                        with patch.dict(os.environ, {'PRAXIS_KEAT_CAPTURE': 'off'}):
                            self.assertFalse(live.finalize_provider_receipt(receipt,
                                system=system, messages=provider, tools=[], run_id='r', call_id='c'))
                        self.assertEqual(tape, original)

    def test_normal_canonical_body_elides_and_rolls_back_exactly(self):
        self.check_coverage(expected=True)

    def test_changed_filename_identical_body_does_not_elide(self):
        self.check_coverage(filename='renamed.md')

    def test_other_file_mentions_original_path_and_has_same_body(self):
        self.check_coverage(filename='other.md', mention=True)

    def test_original_body_extended_with_path_mention_is_not_exact(self):
        self.check_coverage(mention=True)

    def test_truncated_body_does_not_elide(self):
        self.check_coverage(truncated=True)

    def test_ceiling_degraded_body_does_not_elide(self):
        self.check_coverage(ceiling=True)
