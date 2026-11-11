import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
"""10.11.2026: the step card carries the hand's own outcome, target and nested calls.

Before this, `run_detail` dropped `tool_result.metadata`, so the phone said «результат получен»
for a success and for a refusal alike. The journal (tool_report) already knew which one it was.
"""
import json, os, tempfile, unittest
from pathlib import Path

_TEMP = tempfile.TemporaryDirectory()
TREE = Path(_TEMP.name) / 'tree'
TREE.mkdir()
os.environ['HELENE_TREE'] = str(TREE)
from deskd import readers  # noqa: E402

RUN = 'run-20261110T120000000000Z-abcdef12'


def _write_run(events):
    path = TREE / 'memory' / 'runs' / '2026-11' / RUN
    path.mkdir(parents=True, exist_ok=True)
    (path / 'manifest.json').write_text(json.dumps({'status': 'done', 'created_at': '2026-11-10T12:00:00Z'}))
    (path / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events))


def _result(call, name, metadata):
    return {'kind': 'tool_result', 'call_id': call, 'name': name, 'at': '2026-11-10T12:00:03Z', 'seq': 9,
            'result': {'inline': {'head': 'ok'}, 'result_id': 'r-' + call, 'size': 2},
            'metadata': metadata}


class StepOutcome(unittest.TestCase):
    def test_outcome_target_and_nested_reach_the_card(self):
        _write_run([
            {'kind': 'model_started', 'call_id': 'm1', 'at': '2026-11-10T12:00:00Z', 'seq': 1},
            {'kind': 'tool_started', 'call_id': 'c1', 'tool': 'edit', 'args': {'path': 'a.py'},
             'at': '2026-11-10T12:00:01Z', 'seq': 2},
            _result('c1', 'edit', {'outcome': 'refused', 'target': 'a.py',
                                   'operations': [{'hand': 'fs_read', 'outcome': 'ok', 'x': 'y' * 900}]}),
            {'kind': 'tool_started', 'call_id': 'c2', 'tool': 'shell', 'args': {'command': 'ls'},
             'at': '2026-11-10T12:00:04Z', 'seq': 10},
            _result('c2', 'shell', {'outcome': 'definitely-fine'}),
            {'kind': 'tool_started', 'call_id': 'c3', 'tool': 'reply', 'args': {'text': 'x'},
             'at': '2026-11-10T12:00:05Z', 'seq': 11},
            _result('c3', 'reply', None),
            {'kind': 'tool_started', 'call_id': 'c4', 'tool': 'shell', 'args': {'command': 'git status'},
             'at': '2026-11-10T12:00:06Z', 'seq': 12},
            _result('c4', 'shell', {'outcome': 'ok', 'operations': [
                ['read', '/home/a.py'], ['process', '/usr/bin/git', ['git', 'status']]]}),
        ])
        tools = readers.run_detail(RUN)['iterations'][0]['tools']
        edit, shell, reply, shell_ops = tools
        self.assertEqual(edit['outcome'], 'refused')
        self.assertEqual(shell_ops['nested'][0], ['read', '/home/a.py'], 'list rows from tool_report reach the card')
        self.assertEqual(shell_ops['nested'][1][2], ['git', 'status'])
        self.assertEqual(edit['target'], 'a.py')
        self.assertEqual(edit['nested'][0]['hand'], 'fs_read')
        self.assertLessEqual(len(edit['nested'][0]['x']), 300, 'nested values are bounded')
        self.assertEqual(shell['outcome'], 'unknown', 'an unknown word is not turned into success')
        self.assertNotIn('outcome', reply, 'no metadata — no invented outcome')
        self.assertEqual(edit['finished_at'], '2026-11-10T12:00:03Z')


if __name__ == '__main__':
    unittest.main()
