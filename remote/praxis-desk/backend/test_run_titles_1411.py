"""Phone-only origin projection: never replace an unavailable trigger with a window."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deskd import readers


class RunTitles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(readers, 'tree', return_value=self.root).start()
        patch.object(readers, '_run_words', return_value={'who': '**Егор**', 'in': 'Praxis: WRONG WINDOW'}).start()
        patch.object(readers, 'chat_titles', return_value={}).start()
        self.ctx = {'kind': 'chat_turn', 'principal_id': '42', 'origin_chat_id': '-10042',
                    'origin_message_ids': [123], 'goal': 'Praxis: WRONG WINDOW\nЕгор: wanted'}

    def make(self, number=1, text='**Проверь** [код](https://example.test). Ещё фраза.', **ctx):
        run_id = f'run-20261114T120000000000Z-{number:08x}'
        path = self.root / 'memory/runs/2026-11' / run_id
        path.mkdir(parents=True, exist_ok=True)
        context = {**self.ctx, **ctx}
        manifest = {'context': context, 'status': 'done'}
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        if text is not None:
            authority = {**context, 'schema': 'praxis.run.authority.v2', 'origin_text': text}
            (path / 'context.md').write_text('# Immutable run context\n## Authority and address\n```json\n' + json.dumps(authority) + '\n```\n', encoding='utf-8')
        return path, manifest

    def test_id_bound_snapshot_beats_own_first_line_and_markdown(self):
        self.make()
        self.assertEqual(readers.list_runs()[0]['goal_head'], 'Егор: Проверь код.')

    def test_missing_snapshot_does_not_fall_back_to_goal_or_turn_log(self):
        path, _ = self.make(text=None)
        self.assertEqual(readers.list_runs()[0]['goal_head'], 'Повод: сообщение #123 недоступно')
        self.assertEqual(readers.run_detail(path.name)['origin']['source'], 'unavailable')

    def test_id_or_peer_mismatch_is_unavailable(self):
        for key, value in [('origin_message_ids', [456]), ('origin_chat_id', '-10099')]:
            path, manifest = self.make()
            manifest['context'][key] = value
            self.assertEqual(readers._run_origin(path, manifest)['source'], 'unknown')

    def test_missing_author_has_explicit_id(self):
        with patch.object(readers, '_run_words', return_value={}):
            path, manifest = self.make()
            self.assertEqual(readers._run_title(path, manifest), 'Участник #42: Проверь код.')

    def test_self_and_non_chat_events_never_get_conversation_title(self):
        for kind, principal, expected in [('wake', 'praxis:self', 'Пробуждение'),
                                         ('task_window', '42', 'Работа над задачей'),
                                         ('chat_turn', 'praxis:self', 'Собственное событие')]:
            path, manifest = self.make(kind=kind, principal_id=principal)
            self.assertEqual(readers._run_title(path, manifest), expected)

    def test_operator_filter_applies_before_limit_and_cursor(self):
        normal, _ = self.make(1)
        self.make(2, kind='wake', principal_id='operator:codex')
        self.make(3, kind='operator_control_probe', principal_id='42')
        self.assertEqual([r['id'] for r in readers.list_runs(limit=1, audience='work')], [normal.name])
        first = readers.list_runs(limit=1, audience='operator')[0]
        second = readers.list_runs(limit=1, audience='operator', before=first['id'])[0]
        self.assertTrue(first['operator'])
        self.assertEqual(second['goal_head'], 'Проверка оператора (codex)')
        self.assertEqual(len(readers.list_runs()), 3)

    def test_snapshot_cache_invalidates_and_never_writes_source(self):
        path, manifest = self.make()
        self.assertIn('Проверь', readers._run_title(path, manifest))
        self.make(text='Новое сообщение.')
        before = {p.name: p.read_bytes() for p in path.iterdir()}
        self.assertEqual(readers._run_title(path, manifest), 'Егор: Новое сообщение.')
        self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})


if __name__ == '__main__':
    unittest.main()
