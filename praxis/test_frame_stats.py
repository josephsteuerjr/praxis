"""Synthetic-only offline meter tests; no runtime imports or private fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from collections import Counter

import frame_stats as fs


class TestFrameStats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tree = Path(self.tmp.name)
        self.run = self.tree / 'memory/runs/old/run-20000101T000000-private'
        self.run.mkdir(parents=True)

    def write(self, events):
        (self.run / 'events.jsonl').write_text(
            ''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')

    def completed(self, **kw):
        return dict(kind='model_completed', at='2026-09-08T12:00:00Z', **kw)

    def rows(self):
        return fs.collect(self.tree)[0]

    def test_missing_is_not_zero(self):
        self.write([self.completed(), self.completed(usage={'in': 0, 'out': 0,
                    'cache_read': 0}, duration_ms=0, stop_reason='end_turn')])
        summary = fs.summarize(self.rows())
        for name in ('in', 'out', 'cache_read', 'duration_ms'):
            self.assertEqual(summary['metrics'][name], dict(known=1, missing=1,
                coverage=.5, sum=0, p50=0, p90=0))
        self.assertIsNone(summary['metrics']['cache_creation']['sum'])
        self.assertEqual(summary['cuts'], dict(known=1, missing=1, count=0))
        self.assertEqual(summary['attribution']['variant']['unknown'], 2)

    def test_resumed_old_run_event_cutoff_and_offsets(self):
        self.write([self.completed(), dict(kind='model_completed', at='2026-09-01T00:00:00Z'),
                    dict(kind='model_completed', at='2026-09-08T01:00:00+02:00'),
                    dict(kind='model_completed', at='2026-09-08T00:00:00'),
                    dict(kind='model_completed', at='broken')])
        rows, d = fs.collect(self.tree, since=fs.timestamp('2026-09-08T00:00:00Z'))
        self.assertEqual(len(rows), 1)
        self.assertEqual(d['excluded_before_cutoff'], 2)
        self.assertEqual(d['excluded_timestamp_unknown'], 2)
        # Equality is included; run path never acts as a timestamp.
        rows, _ = fs.collect(self.tree, since=fs.timestamp('2026-09-08T12:00:00Z'))
        self.assertEqual(len(rows), 1)

    def test_only_same_call_metadata_no_adjacency(self):
        self.write([
            dict(kind='model_input', call_id='a', metadata={'mode': 'observe', 'frame_id': 'F'}),
            dict(kind='model_started', call_id='a', role='voice'),
            self.completed(call_id='b'),
            dict(kind='tool_started', call_id='b', tool='SECRET_TOOL'),
            self.completed(call_id='a', role='critic', step_id='S', iteration=7),
            dict(kind='model_input', call_id='c', metadata={'reused': True, 'emit': 2, 'frame_id': 'F'}),
            self.completed(call_id='c'), self.completed()])
        rows = self.rows()
        self.assertEqual(rows[0]['frame_mode'], 'unknown')
        self.assertEqual(rows[1]['frame_mode'], 'observe')
        self.assertEqual(rows[1]['role'], 'critic')
        self.assertEqual(rows[1]['step'], 'S')
        self.assertEqual(rows[1]['iteration'], '7')
        self.assertTrue(all(r['hand'] == 'unknown' for r in rows))
        self.assertEqual(rows[2]['frame_mode'], 'unknown')  # not "reused" or inherited
        self.assertEqual(rows[2]['iteration'], 'unknown')  # emit isn't iteration
        self.assertEqual(rows[0]['role'], 'unknown')

    def test_conflicting_and_empty_call_metadata(self):
        self.write([dict(kind='model_input', metadata={'mode': 'bad'}), self.completed(),
                    dict(kind='model_input', call_id='a', metadata={'mode': 'observe'}),
                    dict(kind='model_input', call_id='a', metadata={'mode': 'strict'}),
                    self.completed(call_id='a')])
        rows, d = fs.collect(self.tree)
        self.assertTrue(all(r['frame_mode'] == 'unknown' for r in rows))
        self.assertEqual(d['ambiguous_input_metadata'], 1)

    def test_result_metadata_and_run_local_join(self):
        self.write([dict(kind='model_input', call_id='a', result={'metadata': {
                    'mode': 'strict', 'variant': 'v6'}}), self.completed(call_id='a')])
        other = self.run.parent / 'unrecognizable-name'
        other.mkdir()
        (other / 'events.jsonl').write_text(json.dumps(self.completed(call_id='a')))
        rows = self.rows()
        self.assertEqual(sorted(r['frame_mode'] for r in rows), ['strict', 'unknown'])
        self.assertEqual(sorted(r['variant'] for r in rows), ['unknown', 'v6'])

    def test_malformed_jsonl_numbers_and_utf8_fail_soft(self):
        self.write([None, [], {}, {'kind': 42}, self.completed(usage=[], duration_ms='8'),
                    self.completed(usage={'in': True, 'out': -1, 'cache_read': '5',
                                          'cache_creation': float('nan')})])
        with (self.run / 'events.jsonl').open('ab') as f:
            f.write(b'\n{broken\n\xff\n')
            f.write(json.dumps(self.completed(usage={'in': 3})).encode() + b'\n')
        rows, d = fs.collect(self.tree)
        self.assertEqual(len(rows), 3)
        self.assertEqual(d['malformed_lines'], 2)
        self.assertEqual(d['invalid_events'], 4)
        self.assertIsNone(rows[1]['in'])
        self.assertEqual(rows[2]['in'], 3)
        self.assertIsNone(fs.number(float('inf')))
        self.assertIsNone(fs.number(10 ** 500))

    def test_nearest_rank_convention(self):
        self.assertIsNone(fs.quantile([], .9))
        self.assertEqual(fs.quantile([2], .9), 2)
        self.assertEqual(fs.quantile([1, 9], .9), 9)
        self.assertEqual(fs.quantile([1, 9], .5), 1)
        self.assertEqual(fs.quantile(list(range(1, 11)), .9), 9)
        self.assertEqual(fs.quantile(list(range(1, 12)), .9), 10)
        with self.assertRaises(ValueError):
            fs.quantile([1], 0)

    def test_weighted_ratio_requires_attested_schema_and_pairs(self):
        self.write([self.completed(usage={'schema': 2, 'in': 90, 'cache_read': 10}),
                    self.completed(usage={'schema': 2, 'in': 0, 'cache_read': 900,
                                          'cache_creation': 999}),
                    self.completed(usage={'in': 1, 'cache_read': 9999}),
                    self.completed(usage={'schema': 2, 'in': 1})])
        summary = fs.summarize(self.rows())
        self.assertEqual(summary['cache_read_over_fresh_plus_read'],
                         dict(value=.91, known=2, missing=2, coverage=.5))
        self.assertIsNone(fs.report(self.rows(), {})['cost'])

    def test_usage_schema_private_strict_json_normalizes_versions(self):
        payload = 'ARBITRARY_SCHEMA_PAYLOAD'
        invalid = [float('nan'), float('inf'), float('-inf'), True, False,
                   '2', payload, {'nested': {'text': payload}}, [payload], 2.0, -1, None]
        for schema in invalid + [0, 1, 2, 3]:
            with self.subTest(schema=schema):
                self.write([self.completed(usage={'schema': schema, 'in': 90,
                                                 'cache_read': 10})])
                expected = schema if type(schema) is int and schema >= 0 else None
                rows = self.rows()
                self.assertEqual(rows[0]['usage_schema'], expected)
                self.assertIs(type(rows[0]['usage_schema']), type(expected))
                # Both the API and the CLI must remain strict JSON in private mode.
                text = json.dumps(fs.report(rows, {}, private=True), allow_nan=False)
                self.assertNotIn(payload, text)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(fs.main(['--tree', str(self.tree), '--private']), 0)
                def reject_constant(value):
                    self.fail('non-JSON constant: ' + value)
                result = json.loads(output.getvalue(), parse_constant=reject_constant)
                self.assertNotIn(payload, output.getvalue())
                self.assertEqual(result['rows'][0]['usage_schema'], expected)
                self.assertIs(type(result['rows'][0]['usage_schema']), type(expected))
                for summary in [result['summary'], *result['groups']]:
                    self.assertEqual(summary['cache_read_over_fresh_plus_read'],
                                     dict(value=.1 if expected == 2 else None,
                                          known=int(expected == 2),
                                          missing=int(expected != 2),
                                          coverage=int(expected == 2)))

    def test_finite_counters_overflow_sums_not_ratio(self):
        usage = dict(schema=2, **{key: 1e308 for key in fs.METRICS[:4]})
        self.write([self.completed(usage=usage, duration_ms=1e308, tool_calls=1e308)
                    for _ in range(2)])
        report = fs.report(self.rows(), {})
        for summary in [report['summary'], *report['groups']]:
            self.assertEqual(summary['diagnostics']['overflowed_sums'], list(fs.METRICS))
            for metric in summary['metrics'].values():
                self.assertIsNone(metric['sum'])
                self.assertEqual(metric['known'], 2)
                self.assertEqual(metric['missing'], 0)
                self.assertEqual(metric['p50'], 1e308)
                self.assertEqual(metric['p90'], 1e308)
            self.assertEqual(summary['cache_read_over_fresh_plus_read'],
                             dict(value=.5, known=2, missing=0, coverage=1))
        json.dumps(report, allow_nan=False)

    def test_overflow_cli_strict_json(self):
        self.write([self.completed(usage={'schema': 2, 'in': 1e308, 'cache_read': 1e308})
                    for _ in range(2)])
        def reject_constant(value):
            self.fail('non-JSON constant: ' + value)
        for private in ([], ['--private']):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(fs.main(['--tree', str(self.tree), *private]), 0)
            result = json.loads(output.getvalue(), parse_constant=reject_constant)
            self.assertIsNone(result['summary']['metrics']['in']['sum'])
            self.assertEqual(result['summary']['diagnostics']['overflowed_sums'],
                             ['in', 'cache_read'])
            self.assertEqual(result['summary']['cache_read_over_fresh_plus_read']['value'], .5)

    def test_ratio_overflow_preserves_schema_pairs_and_zero(self):
        self.write([self.completed(usage={'schema': 2, 'in': 1e308, 'cache_read': 1e308}),
                    self.completed(usage={'schema': 2, 'in': 1e308, 'cache_read': 0}),
                    self.completed(usage={'in': 0, 'cache_read': 1e308}),
                    self.completed(usage={'schema': 2, 'in': 1e308})])
        ratio = fs.summarize(self.rows())['cache_read_over_fresh_plus_read']
        self.assertEqual(ratio, dict(value=1/3, known=2, missing=2, coverage=.5))
        self.write([self.completed(usage={'schema': 2, 'in': 0, 'cache_read': 0})])
        self.assertIsNone(fs.summarize(self.rows())['cache_read_over_fresh_plus_read']['value'])

    def test_days_cutoff_overflows_are_usage_errors(self):
        # 1e308 exceeds timedelta range; 4e6 fits timedelta but underflows datetime.
        for days in ('1e308', '4000000'):
            with self.subTest(days=days):
                output, error = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                    with self.assertRaises(SystemExit) as caught:
                        fs.main(['--tree', str(self.tree), '--days', days])
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(output.getvalue(), '')
                self.assertIn('--days cutoff is outside the supported datetime range', error.getvalue())
                self.assertNotIn('Traceback', error.getvalue())

    def test_joint_grouping_and_privacy(self):
        self.write([self.completed(role='PRIVATE ROLE', iteration=1, variant='SECRET TEXT',
                                  call_id='PRIVATE ID', model='PRIVATE MODEL'),
                    self.completed(role='PRIVATE ROLE', iteration=2),
                    self.completed(role='OTHER ROLE', iteration=1)])
        rows = self.rows()
        public = fs.report(rows, {}, axes=('role', 'iteration', 'variant'))
        self.assertEqual(len(public['groups']), 3)
        text = json.dumps(public)
        for secret in ('PRIVATE', 'SECRET', 'OTHER ROLE', self.tmp.name, 'run-2000'):
            self.assertNotIn(secret, text)
        self.assertNotIn('rows', public)
        private = fs.report(rows, {}, private=True)
        self.assertEqual(private['rows'][0]['call'], 'PRIVATE ID')
        self.assertNotIn('text', private['rows'][0])

    def test_empty_and_cli_default_safe(self):
        rows, d = fs.collect(self.tree)
        report = fs.report(rows, d)
        self.assertEqual(report['summary']['calls'], 0)
        self.assertIsNone(report['summary']['cuts']['count'])
        self.write([self.completed(call_id='SECRET-ID', role='SECRET-ROLE')])
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(fs.main(['--tree', str(self.tree), '--since', '2026-09-08']), 0)
        self.assertNotIn('SECRET', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['privacy'], 'aggregate')

    def test_read_errors_are_counted(self):
        d = Counter()
        self.assertEqual(list(fs.events(self.run / 'absent', d)), [])
        self.assertEqual(d['unreadable_files'], 1)


if __name__ == '__main__':
    unittest.main()
