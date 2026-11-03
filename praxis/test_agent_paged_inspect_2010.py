"""Main model -> Forge paged read, including a long Unicode source line."""
import hashlib
import json
from unittest import mock
import agent
from test_forge_focus_read_1410 import PagedRead


class MainModelPagedRead(PagedRead):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for name, value in [('BASE', self.root), ('STATE_DIR', self.root / 'state'),
                            ('TASKS_DIR', self.root / 'state/tasks')]:
            patch = mock.patch.object(agent.forge, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        agent.forge._save_task({'id': 'code-read', 'root': str(self.root),
                               'scope': 'self', 'status': 'active', 'goal': 'exact read'})

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

    def test_supported_host_backend_status_and_legacy_read(self):
        task = agent.forge.get('code-read')
        task['scope'] = 'host'
        agent.forge._save_task(task)
        with mock.patch.object(agent.forge.serverd_client, 'call',
                               return_value={'ok': True, 'text': 'exact remote text'}) as rpc, \
             mock.patch.object(agent.forge, '_remote_observations'):
            self.assertIn('code-read', agent.tool_coding_inspect('code-read'))
            rpc.assert_called_once_with('workspace.inspect', {'root': str(self.root), 'action': 'status'})
            rpc.reset_mock()
            self.assertEqual(self.read(), 'exact remote text')
        self.assertEqual(rpc.call_args.args[1]['path'], 'source.txt')
        self.assertEqual(rpc.call_args.args[1]['root'], str(self.root))

    def test_supported_remote_backend_refuses_exact_read_before_transport(self):
        for scope, backend in [('host', agent.forge.serverd_client),
                               ('windows', agent.forge.body_client)]:
            task = agent.forge.get('code-read')
            task['scope'] = scope
            agent.forge._save_task(task)
            with mock.patch.object(backend, 'call') as rpc:
                for kwargs in ({'start_char': 8}, {'expected_sha256': 'abc'}):
                    self.assertIn('No read was performed', self.read(**kwargs))
            rpc.assert_not_called()

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
