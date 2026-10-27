"""Offline endpoint regression tests: real brain functions and extracted tool/schema.

Extract only the handler/schema AST to avoid starting agent's runtime on import.
"""
import ast
import copy
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import brain
import tool_text_en


def tool_parts():
    tree = ast.parse(Path(__file__).with_name('agent.py').read_text(encoding='utf-8'))
    handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                   and n.name == 'tool_switch_brain')
    tools = next(n.value for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'BASE_TOOLS' for t in n.targets))
    schema = next(ast.literal_eval(n) for n in tools.elts if isinstance(n, ast.Dict)
                  and any(isinstance(k, ast.Constant) and k.value == 'name'
                          and isinstance(v, ast.Constant) and v.value == 'switch_brain'
                          for k, v in zip(n.keys, n.values)))
    return handler, schema


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.before = {'framework': 'account-a', 'model': 'shared',
                       'fallback_model': 'backup', 'reasoning_effort': 'low'}
        self.cfg = {'roles': {'voice': copy.deepcopy(self.before)}, 'frameworks': {
            'account-a': {'base_url': 'https://a/v1', 'api_key': 'fake-a'},
            'account-b': {'base_url': 'https://b/v1', 'api_key': 'fake-b'}}}
        self.models = {'account-a': ['shared', 'next', 'gpt-5.6-terra'],
                       'account-b': ['shared', 'next', 'gpt-5.6-terra']}
        self.writes = []
        self.llm = types.SimpleNamespace(
            ROLES=('voice', 'evaluator'), FRAMEWORKS=('openai', 'anthropic'),
            _config=lambda: self.cfg, _protocol=lambda fw: 'openai',
            _available_models=lambda fw: self.models[fw],
            update_config=self.update, ping=lambda role: (True, ''))
        self.stack = patch.dict(sys.modules, {'llm': self.llm})
        self.stack.start()
        self.addCleanup(self.stack.stop)
        for name in ('_journal', '_spine', 'note_call'):
            p = patch.object(brain, name)
            p.start()
            self.addCleanup(p.stop)

    def update(self, changes):
        self.writes.append(copy.deepcopy(changes))
        self.cfg['roles']['voice'].update(changes['roles']['voice'])
        if not self.cfg['roles']['voice'].get('fallback_framework'):
            self.cfg['roles']['voice'].pop('fallback_framework', None)

    def test_explicit_same_model_changes_account_and_preserves_fallback(self):
        result = brain.switch('voice', 'shared', endpoint='account-b', why='test')
        self.assertTrue(result['ok'])
        self.assertEqual(self.cfg['roles']['voice']['fallback_framework'], 'account-a')
        self.assertEqual(self.cfg['roles']['voice']['fallback_model'], 'shared')

    def test_same_account_same_model_is_noop(self):
        self.assertFalse(brain.switch('voice', 'shared', endpoint='account-a', why='test')['ok'])
        self.assertEqual(self.writes, [])

    def test_omitted_endpoint_prefers_current(self):
        self.assertTrue(brain.switch('voice', 'next', why='test')['ok'])
        self.assertEqual(self.cfg['roles']['voice']['framework'], 'account-a')
        self.assertEqual(self.cfg['roles']['voice']['fallback_model'], 'backup')

    def test_endpoint_id_is_exact(self):
        for endpoint in ('ACCOUNT-B', 'account-b ', 'https://b/v1', 'openai'):
            with self.subTest(endpoint=endpoint):
                self.assertFalse(brain.switch('voice', 'next', endpoint=endpoint, why='test')['ok'])
        self.assertEqual(self.writes, [])

    def test_model_must_be_available_on_selected_endpoint(self):
        self.models['account-b'] = []
        self.assertFalse(brain.switch('voice', 'next', endpoint='account-b', why='test')['ok'])
        self.assertEqual(self.writes, [])

    def test_ambiguity_is_not_arbitrary(self):
        with self.assertRaises(ValueError):
            brain._target_framework('next', 'missing')

    def test_equivalent_accounts_can_be_inferred(self):
        self.cfg['frameworks']['account-b'] = dict(self.cfg['frameworks']['account-a'])
        self.assertEqual(brain._target_framework('next', 'missing'), 'account-a')

    def test_missing_model(self):
        with self.assertRaises(ValueError):
            brain._target_framework('missing', 'account-a')

    def test_ping_failure_and_exception_rollback(self):
        for raises in (False, True):
            with self.subTest(raises=raises):
                self.cfg['roles']['voice'] = copy.deepcopy(self.before)
                def ping(role):
                    if raises:
                        raise RuntimeError('offline')
                    return False, 'offline'
                self.llm.ping = ping
                self.assertFalse(brain.switch('voice', 'shared', endpoint='account-b', why='test')['ok'])
                self.assertEqual(self.cfg['roles']['voice'], self.before)

    def test_profile_endpoint_and_effort(self):
        res = brain.apply_profile('chat', endpoint='account-b', why='test')
        self.assertTrue(res['ok'])
        self.assertEqual(res['framework'], 'account-b')
        self.assertEqual(self.cfg['roles']['voice']['reasoning_effort'], 'medium')
        self.assertEqual(self.cfg['roles']['voice']['fallback_framework'], 'account-a')

    def test_profile_ping_exception_rollback(self):
        def ping(role):
            raise RuntimeError('offline')
        self.llm.ping = ping
        res = brain.apply_profile('chat', endpoint='account-b', why='test')
        self.assertTrue(res['rolled_back'])
        self.assertEqual(self.cfg['roles']['voice'], self.before)

    def test_invalid_profile_endpoint_no_write(self):
        res = brain.apply_profile('chat', endpoint='unknown', why='test')
        self.assertFalse(res['ok'])
        self.assertEqual(res['stage'], 'model')
        self.assertEqual(self.writes, [])

    def test_validation_requires_why(self):
        self.assertFalse(brain.switch('voice', 'next', endpoint='account-b')['ok'])
        self.assertFalse(brain.apply_profile('chat', endpoint='account-b')['ok'])
        self.assertEqual(self.writes, [])

    def test_rollback_failure_is_not_reported_as_restored(self):
        self.llm.ping = lambda role: (False, 'offline')
        def update(changes):
            if self.writes:
                raise OSError('offline')
            self.update(changes)
        self.llm.update_config = update
        result = brain.switch('voice', 'next', endpoint='account-b', why='test')
        self.assertFalse(result['rolled_back'])
        self.assertIn('rollback_error', result)


class ToolTests(unittest.TestCase):
    def test_real_handler_forwards_optional_endpoint_and_keeps_old_calls(self):
        handler, _ = tool_parts()
        ns = {'_is_sovereign_actor': lambda: True}
        exec(compile(ast.Module(body=[handler], type_ignores=[]), 'agent-handler', 'exec'), ns)
        with patch.object(brain, 'switch', return_value={'ok': False, 'error': 'test'}) as switch:
            ns['tool_switch_brain']('switch', 'voice', 'next', 'test', endpoint='account-b')
            switch.assert_called_once_with('voice', 'next', why='test', endpoint='account-b')
        with patch.object(brain, 'switch', return_value={'ok': False, 'error': 'test'}) as switch:
            ns['tool_switch_brain']('switch', 'voice', 'next', 'test')
            switch.assert_called_once_with('voice', 'next', why='test')
        with patch.object(brain, 'apply_profile', return_value={'ok': False, 'error': 'test'}) as profile:
            ns['tool_switch_brain']('profile', profile='chat', why='test', endpoint='account-b')
            profile.assert_called_once_with('chat', why='test', endpoint='account-b')

    def test_schema_translation_and_fingerprint(self):
        _, schema = tool_parts()
        self.assertEqual(schema['input_schema']['required'], ['action'])
        self.assertEqual(schema['input_schema']['properties']['endpoint']['type'], 'string')
        with patch.object(tool_text_en, 'enabled', return_value=True):
            translated = tool_text_en.apply([schema])[0]
            self.assertEqual(tool_text_en.coverage([schema])['stale_translations'], [])
        original = copy.deepcopy(schema)
        original.pop('description')
        translated.pop('description')
        for value in (original, translated):
            for spec in value['input_schema']['properties'].values():
                spec.pop('description', None)
        self.assertEqual(original, translated)
        self.assertIn('endpoint', tool_text_en.EN['switch_brain']['p'])


if __name__ == '__main__':
    unittest.main()
