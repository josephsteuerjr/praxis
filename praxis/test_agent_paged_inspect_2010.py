"""Main model -> Forge paged read, including a long Unicode source line."""
import hashlib
import json
from unittest import mock
import agent
from test_forge_focus_read_1410 import PagedRead


class MainModelPagedRead(PagedRead):
    def read(self, **kwargs):
        return agent.tool_coding_inspect('code-read', action='read', path='source.txt', **kwargs)

    def test_main_model_schema_exposes_continuation(self):
        schema = next(t for t in agent.FORGE_TOOLS if t['name'] == 'coding_inspect')['input_schema']
        self.assertIn('start_char', schema['properties'])
        self.assertIn('expected_sha256', schema['properties'])
        with mock.patch.object(agent.forge, 'inspect', return_value='page') as call:
            self.assertEqual(agent.tool_coding_inspect('code-read', action='read', start_char=9,
                             expected_sha256='abc'), 'page')
            self.assertEqual(call.call_args.kwargs['start_char'], 9)
            self.assertEqual(call.call_args.kwargs['expected_sha256'], 'abc')
