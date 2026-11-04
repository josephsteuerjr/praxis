"""Semantic repairs outside textual conflicts remain isolated and reviewed."""
from pathlib import Path
import shutil
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
import t_source_merge_211 as merge_fixtures
from t_source_merge_211 import put, BASE, LOCAL
import t_source_update_pipeline_220 as fixtures
from localharness import source_merge as merge, source_update as pipeline, source_update_worker
from localharness.package_notice import read, write

HELPER = 'def helper():\n    return "upstream"\n'
AUTHORED = 'local_feature = True\n'


class CandidateRepairs(unittest.TestCase):
    def setUp(self):
        f = merge_fixtures.MergeTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups); self.f = f
        put(f.new, 'module.py', BASE.replace('return 1', 'return 99'))
        for root in (f.base, f.new): put(root, 'dependency.py', HELPER)
        put(f.local, 'dependency.py', AUTHORED)
        f.create()

    def test_nonconflict_repair_preserves_authored_code_and_two_parent_merge(self):
        f = self.f
        self.assertNotIn('dependency.py', read(f.plan / 'plan.json')['conflicts'])
        merge.resolve(f.plan, 'module.py', LOCAL)
        merge.resolve(f.plan, 'dependency.py', AUTHORED + HELPER,
                      reason='New consumer requires helper; retain the authored feature',
                      expected_candidate=merge.candidate_signature(f.plan))
        result = merge.finish(f.plan)
        self.assertEqual(result['repairs'][0]['before'], result['automatic']['dependency.py'])
        self.assertEqual(result['repairs'][0]['after'], result['result']['dependency.py'])
        self.assertEqual((f.plan / 'result/dependency.py').read_text(), AUTHORED + HELPER)
        self.assertEqual((f.local / 'dependency.py').read_text(), AUTHORED)
        parents = merge.git(f.plan / 'repository.git', 'show', '-s', '--format=%P', result['merged_commit']).stdout.decode().split()
        self.assertEqual(parents, [result['commits']['local'], result['commits']['incoming']])

    def test_repair_needs_reason_fresh_candidate_and_declared_source(self):
        f = self.f
        for reason, digest in [('', merge.candidate_signature(f.plan)), ('coupling', ''), ('coupling', '0' * 64)]:
            with self.subTest(reason=reason, digest=digest), self.assertRaises(ValueError):
                merge.resolve(f.plan, 'dependency.py', HELPER, reason=reason, expected_candidate=digest)
        for name in ('new-unrequested.py', '../live.py', 'memory/secret.py', '.env'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                merge.resolve(f.plan, name, HELPER, reason='coupling', expected_candidate=merge.candidate_signature(f.plan))
        self.assertEqual((f.plan / 'result/dependency.py').read_text(), AUTHORED)

    def test_unjournaled_change_after_repair_cannot_finish(self):
        f = self.f
        merge.resolve(f.plan, 'module.py', LOCAL)
        merge.resolve(f.plan, 'dependency.py', AUTHORED + HELPER, reason='restore dependency',
                      expected_candidate=merge.candidate_signature(f.plan))
        put(f.plan / 'result', 'dependency.py', 'tampered = True\n')
        with self.assertRaisesRegex(ValueError, 'recorded repair'): merge.finish(f.plan)

    def test_repair_without_original_text_conflicts_still_requires_review_before_apply(self):
        f = merge_fixtures.MergeTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        f.create()
        self.assertEqual(read(f.plan / 'plan.json')['conflicts'], [])
        merge.reopen(f.plan)
        merge.resolve(f.plan, 'module.py', LOCAL, reason='Explicit compatibility correction',
                      expected_candidate=merge.candidate_signature(f.plan))
        merge.finish(f.plan)
        target = f.root / 'target'; shutil.copytree(f.local, target)
        with self.assertRaisesRegex(ValueError, 'canonical independent review'):
            merge.apply(f.plan, target)
        self.assertEqual((target / 'module.py').read_text(), LOCAL)


class ReviewedCandidate(unittest.TestCase):
    def test_review_finding_can_repair_automatic_file_and_needs_new_frozen_review(self):
        f = fixtures.PipelineTests('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        for root in (f.old / 'tree', f.new / 'tree'): put(root, 'dependency.py', HELPER)
        put(f.code, 'dependency.py', AUTHORED)
        f.preflight(); f.request = read(pipeline.path(f.tree)); f.directory = Path(f.request['directory'])
        pipeline.ensure(f.tree,f.old,f.code,f.directory,'2.2.0',coordinator=f.coordinator)
        f.tick(); f.merge_result(); first = f.tick()
        first_subject = first['subject_sha256']
        f.review_result('findings'); f.tick(); f.tick()
        operation = f.operation()
        unit = f.forge._unit_dir(operation['task_id'], 'agents', operation['unit_id'])
        request = read(unit / 'request.json')
        adapter = source_update_worker.SourceMergeAdapter(request['source_update'])
        adapter.framework = 'fake'
        adapter.dispatch(request, 'merge_source', {'action': 'resolve', 'path': 'dependency.py',
            'content': AUTHORED + HELPER, 'reason': 'Repair an integration dependency found by review',
            'expected_candidate_sha256': merge.candidate_signature(f.directory),
            'expected_input_sha256': operation['input_sha256']})
        adapter.dispatch(request, 'merge_source', {'action': 'finish', 'expected_input_sha256': operation['input_sha256']})
        write(unit / 'result.json', {'status': 'done', 'complete': True, 'result': 'repaired', **adapter.result_stamp()})
        second = f.tick()
        self.assertEqual(second['state'], 'reviewing')
        self.assertNotEqual(second['subject_sha256'], first_subject)
        with self.assertRaises(ValueError): pipeline.validate_ready(f.directory)
        f.review_result(); self.assertEqual(f.tick()['state'], 'ready_to_apply')
        pipeline.validate_ready(f.directory)
        target = f.root / 'independent'; shutil.copytree(f.code, target); merge.apply(f.directory, target)
        self.assertEqual((target / 'dependency.py').read_text(), AUTHORED + HELPER)
        self.assertEqual((f.code / 'dependency.py').read_text(), AUTHORED)


if __name__ == '__main__': unittest.main()
