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

    def test_legacy_backend_status_and_read_do_not_receive_new_kwargs(self):
        def legacy(task_id, action="status", path="", query="", glob="**/*", start=1, end=0):
            return action + ":" + path
        with mock.patch.object(agent.forge, "inspect", side_effect=legacy):
            self.assertEqual(agent.tool_coding_inspect("code-read"), "status:")
            self.assertEqual(self.read(), "read:source.txt")

    def test_legacy_backend_refuses_continuation_instead_of_unbounded_fallback(self):
        calls = []
        def legacy(task_id, action="status", path="", query="", glob="**/*", start=1, end=0):
            calls.append(action)
            return "unbounded"
        with mock.patch.object(agent.forge, "inspect", legacy):
            for kwargs in ({"start_char": 8}, {"expected_sha256": "abc"}):
                self.assertIn("No read was performed", self.read(**kwargs))
        self.assertEqual(calls, [])

    def test_backend_internal_typeerror_is_not_retried(self):
        with mock.patch.object(agent.forge, "inspect", side_effect=TypeError("backend bug")) as call:
            with self.assertRaisesRegex(TypeError, "backend bug"):
                self.read(start=1, expected_sha256="abc")
            self.assertEqual(call.call_count, 1)

    def test_remote_backend_never_silently_discards_cursor_or_hash(self):
        remote = mock.Mock()
        with mock.patch.object(agent.forge, "_remote_client", return_value=remote):
            for kwargs in ({"start_char": 8}, {"expected_sha256": "abc"}):
                self.assertIn("No read was performed", self.read(**kwargs))
        remote.workspace_inspect.assert_not_called()
