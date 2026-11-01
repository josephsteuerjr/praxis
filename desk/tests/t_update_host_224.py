"""Legacy capability admission, pipeline ownership and mandatory frozen review."""
from pathlib import Path
import hashlib
import json
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import t_source_update_pipeline_220 as fixtures
from localharness import source_update as pipeline, source_update_host as host, self_review
from localharness import source_merge
from localharness.package_notice import read, write


class IncomingCoordinator(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.PipelineTests('runTest'); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f=self.f
        write(f.tree/'memory/.self-review/runtime.json', {'supported':False, 'at':time.time()})
        operation=f.operation()
        operation.update(state='needs_attention', error=pipeline.UNAVAILABLE, created=time.time()-7200)
        operation.pop('coordinator',None)  # This fixture predates incoming-host admission.
        write(f.directory/'pipeline.json', operation)
        self.binding={'kind':'incoming-update-runtime.v1', 'program':str(f.new), 'plan_id':f.directory.name}
        # Current incoming panels freeze the app/core seam as well as A/L/B/M.
        context=f.new/'app/context.py';context.parent.mkdir(parents=True,exist_ok=True)
        context.write_text('trusted_fixture = True\n')
        app={'context.py':hashlib.sha256(context.read_bytes()).hexdigest()}
        write(f.new/'source-inputs.json',{'files':app})
        self.binding['app_sha256']=source_merge.signature(app)

    def admit(self):
        f=self.f
        f.service.coordinator=self.binding
        return pipeline.ensure(f.tree,f.old,f.code,f.directory,'2.2.4',coordinator=self.binding)

    def tick(self):
        f=self.f;pipeline.tick(f.tree,f.forge,f.service,coordinator=self.binding)

    def test_old_unsupported_plan_reaches_real_forge_and_review_without_old_runtime(self):
        f=self.f;op=self.admit()
        self.assertEqual(op['state'],'queued');self.assertGreater(op['created'],time.time()-30)
        self.assertEqual(op['capability_retry']['error'],pipeline.UNAVAILABLE)
        pipeline.tick(f.tree,f.forge,f.service)
        self.assertEqual(f.operation()['state'],'queued');self.assertFalse(f.spawns)
        self.tick();self.assertEqual(f.operation()['state'],'merging')
        self.assertEqual(len(f.spawns),1)
        f.merge_result();self.tick();self.assertEqual(f.operation()['state'],'reviewing')
        with self.assertRaises(ValueError):pipeline.validate_ready(f.directory)
        f.review_result();self.tick()
        self.assertEqual(pipeline.validate_ready(f.directory)['state'],'ready_to_apply')
        self.assertFalse(read(f.tree/'memory/.self-review/runtime.json')['supported'])

    def test_stopped_failed_and_admitted_work_are_never_rearmed(self):
        f=self.f;original=f.operation()
        cases=[{'error':'worker failed'}, {'attempts':[{'task_id':'code-deadbeef'}]},
               {'cancel_requested':True}, {'task_id':'code-deadbeef'}, {'review_id':'review-'+'a'*16}]
        for extra in cases:
            with self.subTest(extra=extra):
                write(f.directory/'pipeline.json',{**original,**extra})
                self.assertNotEqual(self.admit()['state'],'queued')
        write(f.directory/'pipeline.json',original)
        write(f.directory/'cancel.json',{'reason':'Owner Stop'})
        self.assertEqual(self.admit()['state'],'cancelled')

    def test_other_coordinator_cannot_take_active_request(self):
        f=self.f;self.admit();self.tick()
        with self.assertRaisesRegex(ValueError,'Another incoming'):
            pipeline.ensure(f.tree,f.old,f.code,f.directory,'2.2.4',coordinator={**self.binding,'program':'different'})
        self.assertEqual(len(f.spawns),1)

    def test_incoming_host_does_not_advance_unrelated_review(self):
        f=self.f
        other=f.service.request(operation_id='unrelated-owner-review',sources={'mine':f.code})
        f.service.tick(review_ids=set())
        self.assertEqual(f.service.status(other['id'])['execution'],'queued')
        self.assertFalse(f.spawns)

    def test_ordinary_service_cannot_admit_incoming_hosts_review(self):
        f=self.f;self.admit();self.tick();f.merge_result();self.tick()
        record=f.service.status(f.operation()['review_id'])
        ordinary=self_review.Service(f.tree,f.code,f.old,f.forge)
        count=len(f.spawns)
        ordinary.tick()
        self.assertEqual(len(f.spawns),count)
        self.assertEqual(ordinary.status(record['id'])['execution'],'queued')

    def test_runtime_validation_rejects_tampered_core_and_coordinator(self):
        f=self.f
        for name in ('forge.py','forge_worker.py','llm.py'):(f.new/'tree'/name).write_text('# trusted fixture\n')
        names=['localharness/'+n+'.py' for n in ('source_update_host','source_update','source_update_worker','source_update_battery','source_update_parallel','source_update_hand','self_review','self_review_worker','source_update_transport','desktop_supervisor','python_relay','agent_source_update','update_source')]
        for name in names:
            p=f.new/'app'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('# trusted fixture\n')
        core={p.name:{'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in (f.new/'tree').iterdir()}
        app={n:hashlib.sha256((f.new/'app'/n).read_bytes()).hexdigest() for n in names}
        write(f.new/'code-upstream.json',{'files':core});write(f.new/'source-inputs.json',{'files':app})
        from update_verification_fixture import bind
        bind(f.root,f.new,read(f.new/'python-app.json')['version'])
        self.assertEqual(host.verified_runtime(f.new)['kind'],'incoming-update-runtime.v1')
        path=f.new/'tree/forge.py';before=path.read_bytes();path.write_text('tampered=True\n')
        with self.assertRaisesRegex(ValueError,'runtime source changed'):host.verified_runtime(f.new)
        path.write_bytes(before);(f.new/'app'/names[0]).write_text('tampered=True\n')
        with self.assertRaisesRegex(ValueError,'application source changed'):host.verified_runtime(f.new)

    def test_published_placeholder_exception_does_not_disclose_authored_credentials(self):
        from localharness import source_merge
        f=self.f;root=f.root/'projection';root.mkdir()
        path=root/'example.py';path.write_text('api_key="fixture-key"\n')
        inventory=source_merge.inventory(root)
        proof={'root':str(root),'merge_files':inventory,
               'published_files':{'example.py':[inventory['example.py']['sha256']]}}
        rows,gaps=self_review.collect({'release':proof})
        self.assertFalse(gaps);self.assertTrue(rows[0]['published_source'])
        # A changed local blob does not inherit a publication exception.
        path.write_text('api_key="private-local-value"\n')
        proof['merge_files']=source_merge.inventory(root)
        self.assertTrue(self_review.collect({'mine':proof})[1])
        # Recognizable full tokens remain withheld even with public provenance.
        path.write_text('api_key="sk-'+('x'*40)+'"\n')
        proof['merge_files']=source_merge.inventory(root)
        proof['published_files']={'example.py':[proof['merge_files']['example.py']['sha256']]}
        self.assertTrue(self_review.collect({'release':proof})[1])


if __name__=='__main__':unittest.main()
