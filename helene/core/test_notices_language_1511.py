"""Actual notice projection: languages never widen an audience or rewrite evidence."""
import copy
import datetime
import json
from pathlib import Path
import re
import tempfile
import time
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import patch

import frame_language
from core import notices
from test_state_language_1511 import functions, RAW


class NoticeLanguages(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='notice-language-')
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name) / 'notices.json'
        changed = patch.object(notices, 'STATE_FILE', self.state)
        changed.start(); self.addCleanup(changed.stop)
        enabled = patch.dict('os.environ', {'PRAXIS_NOTICES': 'on'})
        enabled.start(); self.addCleanup(enabled.stop)

    def block(self, run, *, owner=False, **kw):
        return notices.block_for_input(run_id=run, run_kind='chat', chat_id='other',
                                       owner_context=owner, **kw)

    def test_language_switch_preserves_evidence_and_per_run_acknowledgement(self):
        notices.note_incoming(kind='dm', chat_id='private', chat_title=RAW, who=RAW,
                              message_id=7, gist=RAW, private=True)
        original = copy.deepcopy(notices.pending()[0])
        for language in frame_language.LANGUAGES:
            with self.subTest(language=language), frame_language.use(language):
                before = self.state.read_bytes()
                # Pure projections do not even acknowledge the ledger.
                public = notices._line(original, owner_context=False, fresh=True)
                self.assertNotIn('«'+RAW+'»', public)
                self.assertNotIn('“'+RAW+'”', public)
                private = notices._line(original, owner_context=True, fresh=True)
                self.assertIn(RAW, private)
                self.assertEqual(before, self.state.read_bytes())
                run = 'run-'+language
                shown = self.block(run, owner=True)
                self.assertIn(RAW, shown)
                self.assertEqual(self.block(run, owner=True, mid_turn=True), '')
                now = notices.pending()[0]
                self.assertEqual({k:v for k,v in now.items() if k not in ('shown_runs','shown_first_at')},
                                 {k:v for k,v in original.items() if k not in ('shown_runs','shown_first_at')})
                self.assertEqual(now['shown_runs'][run], 1)

    def test_private_forge_identifiers_notes_and_result_locators_stay_private(self):
        notices.note_node(task_id='task-secret', unit_id='unit-secret', status='done',
                          goal=RAW, result_ref={'path':'PRIVATE-RESULT', 'data':RAW})
        row = notices.pending()[0]
        before = self.state.read_bytes()
        for language in frame_language.LANGUAGES:
            with self.subTest(language=language), frame_language.use(language):
                with patch('authored_notes.for_notice', return_value=RAW) as authored:
                    public = notices._line(row, owner_context=False, fresh=True)
                    self.assertFalse(authored.called)
                    for secret in (RAW, 'task-secret','unit-secret','PRIVATE-RESULT', row['work_ref']):
                        self.assertNotIn(secret, public)
                    owner = notices._line(row, owner_context=True, fresh=True)
                    self.assertIn(RAW, owner)
                    self.assertIn('task-secret', owner); self.assertIn('unit-secret', owner)
                    self.assertIn('PRIVATE-RESULT', owner)
                    self.assertIn(frame_language.text('notices.result_prefix'), owner)
                    result = json.loads(owner.split(frame_language.text('notices.result_prefix'))[-1])
                    self.assertEqual(result, row['result_ref'])
        self.assertEqual(before, self.state.read_bytes())

    def test_only_exact_owned_prefix_changes_unknown_and_authored_gist_remain_literal(self):
        row = {'task_id':'task-1','unit_id':'unit-1','status':'done',
               'gist':'узел unit-1 задачи task-1 закончил: done — '+RAW}
        before = copy.deepcopy(row)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                self.assertTrue(notices._node_gist(row).endswith(' — '+RAW))
                for gist in (RAW, 'custom '+row['gist'], row['gist'].replace('done','failed'),
                             row['gist'].replace('done','done-but-edited')):
                    edited = dict(row, gist=gist)
                    self.assertEqual(notices._node_gist(edited), gist)
                self.assertEqual(notices._node_gist({'gist':row['gist']}), row['gist'])
        self.assertEqual(row, before)

    def test_busy_labels_and_stale_profile_warning_preserve_authored_body(self):
        built = functions('agent.py', ['_busy_label','_appetite_interpretation_view'],
            llm=SimpleNamespace(role_model=lambda role:'model-current',
                                _config=lambda:{'roles':{'voice':{'reasoning_effort':'low'}}}),
            _BRAIN_MENTION_RE=re.compile(r'gpt-[\w.\-]+'))
        interpretation = {'text':'gpt-old '+RAW, 'plan':{'authored':RAW}, 'ts':time.time()-172800}
        original = copy.deepcopy(interpretation)
        for language in frame_language.LANGUAGES:
            with frame_language.use(language):
                label = built._busy_label(SimpleNamespace(kind='task_window', goal=RAW), None)
                self.assertIn(RAW, label)
                self.assertIn(RAW, built._busy_label(SimpleNamespace(kind='chat'),SimpleNamespace(title=RAW)))
                view = built._appetite_interpretation_view(interpretation)
                self.assertEqual(view['text'], original['text']); self.assertEqual(view['plan'], original['plan'])
                self.assertEqual(view['ts'], original['ts'])
                self.assertIn('gpt-old',view['stale']); self.assertIn('model-current',view['stale'])
                self.assertIn('switch_brain',view['stale']); self.assertIn('2',view['recorded'])
                if language!='ru': self.assertNotRegex(view['stale'], '[А-Яа-яЁё]')
        self.assertEqual(interpretation, original)

    def test_observation_envelope_keys_and_values_are_language_independent(self):
        perception = ModuleType('perception')
        perception.panel_state = lambda:{'skips_today':{'a':2,'b':3}}
        built = functions('agent.py', ['build_frame_tail','_bounded_state_float','_bounded_state_int'],
            turns=SimpleNamespace(recent=lambda limit:[{'ts':time.time(),'kind':'task_window',
                                                        'held':'privacy','tools':[RAW],'out':RAW}]),
            tail_split_enabled=lambda:True, _BOOT_TS=datetime.datetime(2020,1,1),
            _FRAME_ADDRESS=SimpleNamespace(get=lambda:{'chat':RAW}), math=__import__('math'),
            praxis_time=SimpleNamespace(stamp=lambda:'2026-11-15T00:00Z'))
        baseline = None
        for language in frame_language.LANGUAGES:
            with frame_language.use(language), patch.dict('sys.modules', {'perception':perception}):
                value = built.build_frame_tail()
                self.assertEqual(value['observations']['latest_turn']['kind'],'task_window')
                self.assertEqual(value['observations']['latest_turn']['held'],'privacy')
                self.assertEqual(value['observations']['address']['chat'],RAW)
                self.assertEqual(value['observations']['perception']['skips_today'],5)
                note = value.pop('note'); self.assertTrue(note)
                if language!='ru': self.assertNotRegex(note,'[А-Яа-яЁё]')
                if baseline is None: baseline=value
                self.assertEqual(value, baseline)


if __name__=='__main__': unittest.main()
