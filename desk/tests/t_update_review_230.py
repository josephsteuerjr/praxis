"""Causal regression for the real unrelated 78 MB update-review diff."""
from pathlib import Path
import json,sys,tempfile,types,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import self_review,source_merge,self_review_worker

class UpdateReview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        code=self.root/'old-code';code.mkdir();(code/'unrelated.py').write_text('OLD_SCOPE=True\n')
        app=self.root/'app';app.mkdir();(app/'window.py').write_text('WINDOW=True\n')
        self.service=self_review.Service(self.root/'home',code,self.root,types.SimpleNamespace())
        self.service.request(operation_id='ordinary-review')
        self.sources={}
        for label in ('base','mine','release','resolved'):
            p=self.root/label;p.mkdir()
            (p/'changed.py').write_text('VALUE='+('2' if label in ('release','resolved') else '1')+'\n')
            (p/'shared.py').write_text('UNCHANGED=True\n')
            if label in ('base','release'):(p/'old_deleted.py').write_text('PREEXISTING_DELETION=True\n')
        names={'changed.py','shared.py','old_deleted.py'}
        for label in ('base','mine','release','resolved'):
            inv=source_merge.inventory(self.root/label)
            self.sources[label]={'root':self.root/label,'merge_files':{n:inv.get(n) for n in names}}
        self.record=self.service.request(trigger='contested_overlay',operation_id='update',sources=self.sources,
            overlay={'conflicts':['changed.py'],'merge_identity':{'plan_id':'a'*64,'candidate_sha256':'b'*64}})
        self.snapshot=Path(self.record['snapshot'])

    def test_update_diff_is_bound_to_its_four_sides_not_unrelated_review(self):
        diff=(self.snapshot/'changes.diff').read_text()
        self.assertNotIn('before/agent/',diff)
        self.assertNotIn('OLD_SCOPE',diff)
        self.assertIn('changed.py',diff)
        self.assertLess(len(diff),4000)
        subject=self_review.validate_snapshot(self.snapshot,self.record['subject_sha256'])
        self.assertEqual(subject['merge_review']['groups']['local-only'],['old_deleted.py'])
        self.assertEqual(subject['merge_review']['changed_from_local'],['changed.py'])
        self.assertFalse(self.service.freshness(self.record['id'])['stale'])

    def reviewer(self):return self_review_worker.SnapshotReviewer({'root':str(self.snapshot),'subject_sha256':self.record['subject_sha256']})

    def test_per_file_comparison_reads_both_frozen_sides(self):
        row=json.loads(self.reviewer().dispatch({},'inspect',{'action':'diff','path':'changed.py','left':'mine','right':'resolved'}))
        self.assertIn('-VALUE=1',row['text']);self.assertIn('+VALUE=2',row['text'])
        with self.assertRaises(ValueError):self.reviewer().dispatch({},'inspect',{'action':'diff','path':'../outside','left':'mine','right':'resolved'})

    def test_diff_accepts_declared_side_path_returned_by_read_and_list(self):
        for side in ('base','mine','release','resolved'):
            row=json.loads(self.reviewer().dispatch({},'inspect',{
                'action':'diff','path':side+'/changed.py','left':'mine','right':'resolved'}))
            self.assertEqual(row['path'],'changed.py')
            self.assertIn('-VALUE=1',row['text']);self.assertIn('+VALUE=2',row['text'])
        with self.assertRaises(ValueError):
            self.reviewer().dispatch({},'inspect',{'action':'diff','path':'base/missing.py'})

    def test_status_is_bounded_and_manifest_remains_available_in_pages(self):
        adapter=self.reviewer();status=json.loads(adapter.dispatch({},'inspect',{'action':'status'}))
        self.assertNotIn('manifest',status);self.assertEqual(status['manifest_count'],13)
        page=json.loads(adapter.dispatch({},'inspect',{'action':'list','path':'resolved','limit':2}))
        self.assertEqual(len(page['entries']),2);self.assertEqual(page['next_offset'],2)
        second=json.loads(adapter.dispatch({},'inspect',{'action':'list','path':'resolved','limit':2,'offset':2}))
        self.assertEqual(len(second['entries']),1);self.assertIsNone(second['next_offset'])

    def test_incoming_panel_is_invisible_to_legacy_unfenced_review_scanner(self):
        binding={'kind':'incoming-update-runtime.v1','review_store':'isolated-v1'}
        service=self_review.Service(self.root/'home',self.root/'mine',self.root,types.SimpleNamespace(),coordinator=binding)
        record=service.request(trigger='contested_overlay',operation_id='isolated-update',sources=self.sources,
            overlay={'conflicts':['changed.py'],'merge_identity':{'plan_id':'a'*64}})
        legacy_root=self.root/'home/memory/.self-review'
        discovered=[json.loads(p.read_text())['id'] for p in legacy_root.glob('review-*/request.json')]
        self.assertNotIn(record['id'],discovered)
        self.assertEqual(service.status(record['id'])['id'],record['id'])
        self.assertEqual(self_review.Service(self.root/'home',self.root/'mine',self.root,None,coordinator=binding).status(record['id'])['id'],record['id'])

    def test_missing_partition_cannot_be_accepted(self):
        from localharness.self_review_merge import validate_coverage
        subject=self_review.validate_snapshot(self.snapshot,self.record['subject_sha256'])
        units=subject['merge_review']['coverage_units']
        self.assertEqual({p for u in units for p in u['paths']},{'changed.py'})
        validate_coverage(subject,self.record['critics'])
        with self.assertRaisesRegex(ValueError,'incomplete'):validate_coverage(subject,[])
        wrong=[{**self.record['critics'][0],'coverage':{'id':units[0]['id'],'paths':[]}}]
        with self.assertRaisesRegex(ValueError,'assignment'):validate_coverage(subject,wrong)
        self.assertIn('exclusive assignment',self.service._brief(self.record,self.record['critics'][0]))

    def test_incomplete_reviewer_findings_remain_actionable_without_approval(self):
        row={'critics':[{'execution':'done','verdict':'insufficient',
                         'findings':[{'path':'changed.py','detail':'real defect'}]}],'gaps':[]}
        self.service._aggregate(row)
        self.assertEqual(row['verdict'],'findings');self.assertEqual(row['state'],'needs_attention')

    def test_review_waves_and_subject_changes_never_create_a_master_age_deadline(self):
        from localharness import source_update
        from localharness.package_notice import write
        record={**self.record,'policy':{**self.record['policy'],'max_parallel':2},'critics':[{}]*5}
        write(self.service._path(record['id']),record)
        operation={'tree':str(self.root/'home'),'review_id':record['id'],'subject_sha256':record['subject_sha256']}
        self.assertIsNone(source_update.budget_seconds(operation))
        self.assertIsNone(source_update.budget_seconds({**operation,'subject_sha256':'changed'}))

    def test_incoming_app_context_is_readable_frozen_and_bound_to_verified_inventory(self):
        import hashlib
        from localharness import source_update
        app = self.root/'incoming-app/app/deskd'; app.mkdir(parents=True)
        reader = app/'forge_reader.py'; reader.write_text('def task_detail(): return "actual reader"\n')
        files = {'deskd/forge_reader.py': hashlib.sha256(reader.read_bytes()).hexdigest()}
        resource = app.parent/'static/index.html';resource.parent.mkdir()
        resource.write_text('<html>Actual composed UI is part of the reviewed application</html>')
        files['app/dist/index.html'] = hashlib.sha256(resource.read_bytes()).hexdigest()
        (app.parents[1]/'source-inputs.json').write_text(json.dumps({'files':files}))
        program=app.parents[1]
        runtime=program/'runtime/fixture.lock';runtime.parent.mkdir();runtime.write_text('synthetic immutable runtime')
        (program/'python-package-files.json').write_text(json.dumps({'files':{'runtime/fixture.lock':hashlib.sha256(runtime.read_bytes()).hexdigest()}}))
        directory=self.root/'composition-plan'
        plan=source_merge.create(self.root/'base',self.root/'mine',self.root/'release',directory)
        operation = {'directory':str(directory),'input_sha256':source_merge.input_signature(plan),
                     'coordinator':{'kind':'incoming-update-runtime.v1', 'program':str(program),
                                    'app_sha256':source_merge.signature(files)}}
        harness=source_update._harness_material(operation)
        identity={'plan_id':'a'*64,'composition_sha256':harness['harness']['release_evidence']['composition_sha256']}
        self.service.coordinator=operation['coordinator']
        sources = {**self.sources, **harness}
        record = self.service.request(trigger='contested_overlay', operation_id='with-harness', sources=sources,
            overlay={'conflicts':['changed.py'], 'merge_identity':identity})
        frozen = Path(record['snapshot'])/'harness/deskd/forge_reader.py'
        self.assertEqual(frozen.read_bytes(), reader.read_bytes())
        subject = self_review.validate_snapshot(record['snapshot'], record['subject_sha256'])
        self.assertFalse(subject['gaps'])
        self.assertEqual(subject['merge_review']['changed_from_local'], ['changed.py'])
        self.assertIn('harness/static/index.html', {r['path'] for r in subject['manifest']})
        with self.assertRaisesRegex(ValueError, 'Only declared'):
            self_review._source_roots({'base':{**sources['base'],'inventory_scope':'python-context-v1'}})
        unexpected=directory/'compound/app/deskd/unlisted.py';unexpected.write_text('UNBOUND=True\n')
        with self.assertRaisesRegex(ValueError, 'Frozen merge inventory changed: harness'):
            self.service.request(trigger='contested_overlay',operation_id='unbound-context',sources=sources,
                overlay={'conflicts':['changed.py'],'merge_identity':identity})
        unexpected.unlink()
        reader.write_text('changed after admission\n')
        self.assertIn('actual reader', frozen.read_text())
        with self.assertRaisesRegex(ValueError, 'incoming app changed'):
            source_update._harness_material(operation)
        reader.write_bytes(frozen.read_bytes())
        (app.parents[1]/'source-inputs.json').write_text(json.dumps({'files':{}}))
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            source_update._harness_material(operation)

if __name__=='__main__':unittest.main()
