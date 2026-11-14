"""Localized frame rendering preserves selection, authored bytes and provider input."""
from contextlib import ExitStack
import copy
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import agent
import frame_language
import frame_shadow
import frame_serve
from test_frame_shadow import ShadowCase, _ctx, NOW


class LocalizedFrame(ShadowCase):
    def test_localized_address_rows_keep_current_place_and_inactive_first(self):
        for lang in frame_language.LANGUAGES:
            with self.subTest(lang=lang), frame_language.use(lang), frame_language.pin():
                dm = frame_language.text('owned.frame_shadow.5ba87ed8f35f')
                group = frame_language.text('owned.frame_shadow.232309a0a7bc')
                def row(title, kind, cid, stamp):
                    return frame_language.text('owned.frame_shadow.e144031c159f',
                        v0=f'{title} — {kind} {cid}', v1=stamp,
                        v2='AUTHORED перенос Français 中文', v3='')
                rows = [row('CURRENT', dm, '101', '2026-08-17 10:00 UTC'),
                        row('OLD', group, '-200', '2026-07-01 09:00 UTC'),
                        row('INACTIVE', group, '-300', '—'),
                        row('RECENT', group, '-400', '2026-08-16 23:00 UTC')]
                book = 'HEADER\n'+'\n'.join(rows)+'\nSOURCE'
                reduced = frame_shadow._shrink_book(book, len(book)-10, '101')
                self.assertIn(rows[0], reduced)
                self.assertIn(rows[3], reduced)
                self.assertNotIn('INACTIVE', reduced)
                self.assertIn('AUTHORED перенос Français 中文', reduced)
                self.assertEqual(reduced, frame_shadow._shrink_book(book, len(book)-10, '101'))

    def test_actual_book_narrows_audience_and_preserves_authored_transfer(self):
        directory = self.base/'memory/rooms'
        directory.mkdir()
        (directory/'101.md').write_text('# PRIVATE_NAME Русский 中文\ntransfer: MY_TRANSFER Русский 中文\n', encoding='utf-8')
        (directory/'-100500.md').write_text('# HIDDEN_NEIGHBOR\n', encoding='utf-8')
        for lang in frame_language.LANGUAGES:
            with self.subTest(lang=lang), frame_language.use(lang), frame_language.pin():
                narrow = frame_shadow._block_address_book(_ctx(), 'other')
                moving = frame_shadow._block_address_book(_ctx(), 'owner', moving=True)
                self.assertIn('PRIVATE_NAME Русский 中文', narrow)
                self.assertNotIn('HIDDEN_NEIGHBOR', narrow)
                self.assertNotIn('-100500', narrow)
                self.assertIn('MY_TRANSFER Русский 中文', moving)
                self.assertIn(frame_language.text('frame.transfer_group'), moving)
                self.assertIn(frame_language.text('owned.frame_shadow.5ba87ed8f35f'), narrow)

    def test_long_history_keeps_literal_ends_and_counts_the_localized_cut(self):
        head = 'PRIVATE_HEAD Русский 中文\n'
        tail = '\nPRIVATE_TAIL Français 中文  '
        raw = head + 'x'*40000 + tail
        for lang in frame_language.LANGUAGES:
            with self.subTest(lang=lang), frame_language.use(lang), frame_language.pin():
                rendered, meta, _ = frame_shadow._zone_a([{'role':'user','content':raw}], 0, '')
                self.assertIn(raw[:frame_shadow.A_MSG_HEAD], rendered)
                self.assertIn(raw[-frame_shadow.A_MSG_TAIL:], rendered)
                self.assertIn(frame_language.text('frame.cut_marker'), rendered)
                self.assertEqual(meta['capped'], 1)
                self.assertEqual(meta['kept'], 1)
                for zone in 'EAT':
                    self.assertTrue(frame_shadow.separator(zone).startswith('\n\n═══ '))
                with patch.dict(os.environ, PRAXIS_TZ_OFFSET_H='-5'):
                    self.assertIn('UTC-5', frame_shadow._clock_line(NOW))
                    self.assertNotIn('Самар', frame_shadow._clock_line(NOW))

    def test_provider_boundary_all_language_pairs_and_resident_toggle(self):
        skill = self.base/'soul/skills/living-prose'
        skill.mkdir(parents=True)
        authored = 'MY_RESIDENT Русский Français 中文 {literal}'
        (skill/'resident.md').write_text(authored, encoding='utf-8')
        config = self.base/'config.json'
        original_files = {str(p): p.read_bytes() for p in self.base.rglob('*.md')}
        legacy = [{'type':'text','text':'LIVE SYSTEM'}]
        messages = [{'role':'user','content':'PRIVATE Русский Français 中文 {literal}'}]
        tools = [{'name':'fixture_tool','description':'Stable English schema',
                  'input_schema':{'type':'object','properties':{'path':{'type':'string'}}}}]
        before_messages, before_tools = copy.deepcopy(messages), copy.deepcopy(tools)
        response = SimpleNamespace(blocks=[], text='ok', stop_reason='end_turn')
        receipts=[]
        store=SimpleNamespace(store_result=lambda *a, **kw: receipts.append((a,kw)))
        env={'HELENE_CONFIG':str(config),'HELENE_SKILLS_KIT_ROOT':'',
             'PRAXIS_FRAME_V6':'serve','PRAXIS_FRAME_V6_STREAMS':'dm-101','PRAXIS_OWNER_ID':'101'}
        outputs={}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ,env))
            stack.enter_context(patch.object(agent.run_context,'current_run',return_value=SimpleNamespace(run_id='fixture')))
            stack.enter_context(patch.object(agent,'_runs',return_value=store))
            stack.enter_context(patch.object(agent,'_run_status_gate'))
            stack.enter_context(patch.object(agent,'_run_event_strict'))
            chat=stack.enter_context(patch.object(agent.llm,'chat',return_value=response))
            # Real K, resident renderer, frame_measure assembly and serving validator;
            # only variable E sources are held fixed for exact cross-UI comparison.
            stack.enter_context(patch.object(frame_shadow,'_zone_e_current',return_value='E_FIXTURE'))
            for ui in frame_language.LANGUAGES:
                for lang in frame_language.LANGUAGES:
                    for enabled in (True,False):
                        config.write_text(json.dumps({'lang':ui,'agent':{
                            'frame_language':lang,'living_prose':enabled}}),encoding='utf-8')
                        with frame_serve.bind(system=legacy,ctx=agent.ChannelContext(chat_id=101,owner=True),dynamic='DYNAMIC_EXACT'):
                            agent._model_call(legacy,messages,tools)
                        request=chat.call_args.kwargs
                        self.assertIsInstance(request['system'],str,'resident must not force fallback')
                        with frame_language.use(lang):
                            self.assertIn(frame_shadow.separator('E'),request['system'])
                            self.assertIn(frame_language.text('frame.live_canary'),request['system'])
                        self.assertEqual(authored in request['system'],enabled)
                        self.assertIn('DYNAMIC_EXACT',request['system'])
                        self.assertIs(request['messages'],messages)
                        self.assertIs(request['tools'],tools)
                        key=(lang,enabled)
                        if key in outputs:self.assertEqual(outputs[key],request['system'])
                        outputs[key]=request['system']
            self.assertEqual(chat.call_count,32)
        self.assertEqual(messages,before_messages)
        self.assertEqual(tools,before_tools)
        self.assertEqual(original_files,{str(p):p.read_bytes() for p in self.base.rglob('*.md')})
        actual=[kw for a,kw in receipts if kw.get('name')=='model-input']
        self.assertEqual(len(actual),32)
        self.assertTrue(all(row['metadata']['frame_serve']['status']=='served' for row in actual))
