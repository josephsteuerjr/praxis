"""code_query phone lens: the saved result is read whole, bounded, never edited."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from deskd import readers


def result(**more):
    rows = [{'start': {'line': 10 + i, 'char': 4, 'byte': 4}, 'end': {'line': 10 + i, 'char': 9},
             'name': 'Budget', 'qualname': 'intelligence.Budget', 'kind': 'call', 'caller': f'fn{i}',
             'evidence': 'module_attribute', 'method': 'python_ast', 'ambiguous': i % 7 == 0,
             'matches_target': True, 'binding': {'module': 'forge_intelligence', 'local': 'intelligence'},
             'path': f'pkg/m{i}.py', 'file_sha256': 'a' * 64, 'snippet': 'x = intelligence.Budget()'}
            for i in range(40)]
    data = {'op': 'references', 'root': {'kind': 'home', 'path': '/app', 'git_root': '/app', 'head': 'f' * 40},
            'snapshot': '82b7a1eb86419107', 'rows': rows, 'total': 41, 'next_cursor': '82b7a1eb86419107:40',
            'complete': True,
            'coverage': {'seen': 726, 'parsed': 13, 'failed': [], 'prefiltered': 712,
                         'skipped': [{'path': 'a', 'reason': 'directory'}, {'path': 'b', 'reason': 'directory'},
                                     {'reason': 'name_byte_prefilter', 'count': 712}],
                         'dynamic_imports': [{'path': 'r.py'}], 'missing_grammars': ['rust'],
                         'budget': {'complete': True, 'stopped_by': '', 'file_cap': 12000, 'time_budget_s': 90.0,
                                    'spent_s': 6.929, 'stages': []}, 'complete': True, 'limits': ['...']},
            'method_summary': {'python_ast': 23, 'text': 18}, 'cache': {'hits': 0, 'misses': 13}}
    data.update(more)
    return data


class CodeQueryLens(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch.object(readers, 'tree', return_value=self.root).start()
        patch.object(readers, '_run_words', return_value={}).start()

    def run_with(self, payload, outcome='unknown', inline_limit=2000):
        run_id = 'run-20261114T120000000000Z-0000c0de'
        path = self.root / 'memory/runs/2026-11' / run_id
        (path / 'results').mkdir(parents=True, exist_ok=True)
        (path / 'manifest.json').write_text(json.dumps({'context': {'kind': 'wake'}, 'status': 'done'}), encoding='utf-8')
        text = json.dumps(payload, ensure_ascii=False)
        (path / 'results/result-0001.txt').write_text(text, encoding='utf-8')
        ref = {'result_id': 'result-0001', 'path': 'results/result-0001.txt', 'size': len(text.encode()),
               'inline': {'head': text[:inline_limit], 'tail': text[-200:], 'truncated': len(text) > inline_limit}}
        events = [{'kind': 'model_started', 'call_id': 'm1', 'seq': 1},
                  {'kind': 'tool_started', 'tool': 'code_query', 'call_id': 'c1', 'seq': 2,
                   'args': {'op': payload.get('op', 'references') if isinstance(payload, dict) else 'x', 'name': 'Budget'}},
                  {'kind': 'tool_result', 'call_id': 'c1', 'seq': 3, 'result': ref,
                   'metadata': {'outcome': outcome} if outcome else {}}]
        (path / 'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events) + '\n', encoding='utf-8')
        before = {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()}
        card = readers.run_detail(run_id)['iterations'][0]['tools'][0]
        self.assertEqual(before, {p.relative_to(path): p.read_bytes() for p in path.rglob('*') if p.is_file()})
        return card

    def test_truncated_preview_reads_whole_result_file(self):
        card = self.run_with(result())
        self.assertTrue(card['result']['truncated'])
        lens = card['lens']
        self.assertEqual((lens['op'], lens['total'], len(lens['rows']), lens['complete']), ('references', 41, 40, True))
        self.assertEqual(lens['next_cursor'], '82b7a1eb86419107:40')
        self.assertEqual(lens['coverage']['skipped'], {'directory': 2, 'name_byte_prefilter': 712})
        self.assertEqual((lens['coverage']['dynamic_imports'], lens['coverage']['missing_grammars']), (1, ['rust']))
        row = lens['rows'][0]
        self.assertEqual((row['path'], row['line'], row['char'], row['module']), ('pkg/m0.py', 10, 4, 'forge_intelligence'))
        self.assertNotIn('file_sha256', row)
        self.assertEqual((card['outcome'], card['outcome_source']), ('ok', 'result'))

    def test_journal_outcome_is_never_overridden(self):
        card = self.run_with(result(), outcome='in_doubt')
        self.assertEqual(card['outcome'], 'in_doubt')
        self.assertNotIn('outcome_source', card)

    def test_incomplete_and_refused_results(self):
        card = self.run_with(result(complete=False, rows=[], total=0, next_cursor=None))
        self.assertEqual((card['outcome'], card['lens']['complete'], card['lens']['total']), ('partial', False, 0))
        card = self.run_with({'error': 'stale_cursor', 'changed_paths': ['a.py'], 'complete': False,
                              'coverage': {'seen': None, 'parsed': 0, 'failed': [], 'skipped': [], 'budget': None}})
        self.assertEqual((card['outcome'], card['lens']['error'], card['lens']['changed_paths']), ('refused', 'stale_cursor', ['a.py']))
        card = self.run_with({'error': 'owned_search_failed', 'complete': False})
        self.assertEqual(card['outcome'], 'failed')

    def test_unparseable_or_foreign_result_has_no_lens(self):
        card = self.run_with(['not', 'a', 'query'])
        self.assertNotIn('lens', card)
        self.assertEqual(card['outcome'], 'unknown')

    def test_values_are_bounded(self):
        huge = result(rows=[{'path': 'p' * 1500, 'snippet': 's' * 1500, 'start': {'line': 1, 'char': 0}}] * 300, total=300)
        lens = self.run_with(huge)['lens']
        self.assertEqual(len(lens['rows']), 200)
        self.assertLessEqual(max(len(lens['rows'][0][k]) for k in ('path', 'snippet')), 200)
        # Over the 2 MB read bound there is no lens; the raw tabs remain the record.
        card = self.run_with(result(rows=[{'path': 'p' * 9000}] * 300))
        self.assertNotIn('lens', card)

    def test_overview_and_impact_details(self):
        detail = {'model': {'files_scanned': 3351, 'languages': {'python': 677, 'rust': 33}},
                  'entrypoints': ['agent.py'], 'central_modules': [{'module': 'agent', 'imported_by': 209}],
                  'hotspots': [{'path': 'agent.py', 'lines': 20426}], 'tests': {'count': 438}, 'docs': ['A.md']}
        lens = self.run_with(result(op='overview', detail=detail))['lens']
        self.assertEqual((lens['overview']['files_scanned'], lens['overview']['tests'], lens['overview']['docs']), (3351, 438, 1))
        detail = {'changed': ['code_query.py'], 'impacted': ['a.py'] * 331, 'tests': ['t.py'] * 100,
                  'parse_errors': [], 'truncated': False}
        lens = self.run_with(result(op='impact', detail=detail))['lens']
        self.assertEqual((lens['impact']['impacted'], lens['impact']['tests'], len(lens['impact']['tests_sample'])), (331, 100, 8))


if __name__ == '__main__':
    unittest.main()
