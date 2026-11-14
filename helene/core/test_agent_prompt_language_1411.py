"""Actual prompt producers use frame language without rewriting supplied data."""
import json
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import frame_language


class PromptLanguages(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = Path(temp.name) / 'config.json'
        env = patch.dict(os.environ, {'HELENE_CONFIG': str(self.config)})
        env.start()
        self.addCleanup(env.stop)

    def configure(self, ui, lang):
        self.config.write_text(json.dumps({'lang': ui, 'agent': {'frame_language': lang}}), encoding='utf-8')

    def test_tool_navigation_changes_language_without_changing_schemas_or_signatures(self):
        ctx = agent.ChannelContext(is_dm=True, owner=True, known=True)
        with patch.dict(os.environ, {'PRAXIS_TOOLS_EN': 'on'}):
            catalog = agent.catalog_tools_for(ctx)
            before = json.dumps(catalog, sort_keys=True)
            signatures = [agent._hand_signature(tool) for tool in catalog
                          if tool.get('name') and tool['name'] not in agent.NATIVE_HAND_NAMES]
            rendered = {}
            for ui in frame_language.LANGUAGES:
                for lang in frame_language.LANGUAGES:
                    self.configure(ui, lang)
                    text = agent.hands_pointer_text(catalog)
                    self.assertIn(frame_language.text('hands.pointer.title'), text)
                    self.assertIn('Name(arguments)' if lang=='en' else frame_language.text('hands.pointer.columns'), text)
                    for signature in signatures:
                        self.assertIn(signature, text)
                    contract = agent._hands_contract_text()
                    for name in ('windows_mcp_', 'run_command', 'code_query', 'expected_sha256',
                                 'confirmed', 'refused', 'in_doubt', 'partial', 'Stop'):
                        self.assertIn(name, contract)
                    if lang in rendered:
                        self.assertEqual(rendered[lang], (text, contract))
                    rendered[lang] = text, contract
                    self.assertEqual(json.dumps(catalog, sort_keys=True), before)

    def test_summaries_and_scout_use_selected_language_and_literal_input_at_provider_boundary(self):
        personal = 'PRIVATE Русский 中文 Français {owner} [молчу]'
        sent = []
        def chat(*args, **kwargs):
            sent.append(kwargs)
            return types.SimpleNamespace(text='RESULT', stop_reason='end_turn')
        with patch.object(agent.llm, 'configured', return_value=True), \
             patch.object(agent.llm, 'chat', side_effect=chat), \
             patch.object(agent, '_chat_cancellable', side_effect=chat), \
             patch.object(agent.llm, 'limits', return_value=types.SimpleNamespace(max_tool_iters=2)), \
             patch.object(agent, 'read_summary', return_value=personal), \
             patch.object(agent, 'write_summary') as saved, \
             patch.object(agent, 'tool_journal'):
            for ui in frame_language.LANGUAGES:
                for lang in frame_language.LANGUAGES:
                    with self.subTest(ui=ui, frame=lang):
                        self.configure(ui, lang)
                        self.assertEqual(agent._summarize_history([{'role':'assistant','content':personal}]), 'RESULT')
                        self.assertEqual(sent[-1]['system'], frame_language.text('prompt.summarize'))
                        self.assertIn(personal, sent[-1]['messages'][0]['content'])
                        self.assertEqual(agent.compact('123', [personal]), 'RESULT')
                        saved.assert_called_with('123', 'RESULT')
                        self.assertEqual(sent[-1]['system'], frame_language.text('prompt.compact'))
                        self.assertEqual(sent[-1]['messages'][0]['content'].count(personal), 2)
                        self.assertEqual(agent.second_look(personal), 'RESULT')
                        self.assertEqual(sent[-1]['system'], frame_language.text('prompt.scout'))
                        self.assertEqual(sent[-1]['messages'][0]['content'], frame_language.text('prompt.brief', brief=personal))
                        self.assertTrue(sent[-1]['tools'])
                        self.assertLessEqual({t['name'] for t in sent[-1]['tools']}, set(agent._SCOUT_TOOL_NAMES))

    def test_presence_contracts_preserve_reply_protocol_in_all_language_pairs(self):
        seen = {}
        for ui in frame_language.LANGUAGES:
            for lang in frame_language.LANGUAGES:
                self.configure(ui, lang)
                for dm in (False, True):
                    for owner in (False, True):
                        for hands in (False, True):
                            ctx = agent.ChannelContext(chat_id='42' if dm else '-10042', is_dm=dm,
                                                       owner=owner, principal_id='42', known=owner,
                                                       reply_targets=((17, 'PERSONAL Русский', 'DATA'),))
                            with patch.object(agent.work_loop, 'reply_hand_enabled', return_value=hands):
                                frame = agent._presence_frame(ctx)
                            key = (lang, dm, owner, hands)
                            if key in seen:
                                self.assertEqual(frame, seen[key])
                            seen[key] = frame
                            self.assertNotIn('Yegor', frame)
                            self.assertNotIn('Praxis', frame)
                            self.assertIn('reply_to' if hands else 'ОТВЕТ->#<id>', frame)
                            self.assertEqual('[молчу]' in frame, not hands)
                            self.assertEqual('end_turn' in frame, hands)
                            self.assertIn('message #N', frame)
                            self.assertIn('reply_to=#M', frame)
        self.assertEqual(len(seen), 32)

    def test_scout_turn_keeps_language_when_config_changes_during_a_tool(self):
        self.configure('zh', 'en')
        systems = []
        def chat(*args, **kwargs):
            systems.append(kwargs['system'])
            if len(systems) == 1:
                return types.SimpleNamespace(stop_reason='tool_use', blocks=[
                    {'type':'tool_use','id':'read-1','name':'fs_read','input':{'path':'file.md'}}])
            return types.SimpleNamespace(stop_reason='end_turn', text='REPORT')
        def read(**kwargs):
            self.configure('en', 'fr')
            return 'AUTHORED Русский'
        with patch.object(agent, '_chat_cancellable', side_effect=chat), \
             patch.object(agent.llm, 'limits', return_value=types.SimpleNamespace(max_tool_iters=2)), \
             patch.dict(agent.TOOL_IMPL, {'fs_read': read}), patch.object(agent, 'tool_journal'):
            self.assertEqual(agent.second_look('Inspect'), 'REPORT')
        self.assertEqual(systems, [frame_language.catalog('en')['prompt.scout']] * 2)
        self.assertEqual(frame_language.language(), 'fr')
