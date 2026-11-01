"""Exact stale update identities, with fake process and OS authority only."""
from contextlib import contextmanager
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness import update_supersession as supersession
from localharness import desktop_update_request as jobs, source_update as source
from localharness import agent_source_update as agent, session_host, owner_stop
from localharness import windows_first_start, windows_hooks
from localharness.package_notice import read, write


class Supersession(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='update-supersession-private-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.current, self.old, self.other, self.tree = [self.base/name for name in ('new', 'old', 'other', 'data')]
        for path in (self.current, self.old, self.other, self.tree):path.mkdir()
        self.user = self.tree/'memory/.forge/tasks/user-task/task.json'
        write(self.user, {'id':'user-task', 'status':'active', 'goal':'ordinary owner work'})
        self.history = self.tree/'memory/groups/history.jsonl'
        self.history.parent.mkdir(parents=True)
        self.history.write_bytes(b'ordinary owner history must remain exact\n')
        self.observe = Mock(return_value='absent')
        @contextmanager
        def private_lock(path):
            self.assertTrue(Path(path).resolve().is_relative_to(self.base.resolve()))
            yield
        self.enterContext(patch.object(supersession, 'transaction', side_effect=private_lock))
        self.enterContext(patch.object(session_host, 'process_state',
            side_effect=AssertionError('Tests must inject captured producer observations')))
        self.enterContext(patch.object(owner_stop, 'stopped', return_value=False))
        self.enterContext(patch.object(owner_stop, 'paused', return_value=False))
        for name in ('Backend', 'kernel', 'terminate', 'restart_holders', 'user_sid'):
            self.enterContext(patch.object(windows_first_start, name,
                side_effect=AssertionError('No live OS authority')))
        self.enterContext(patch.object(windows_hooks, 'retire',
            side_effect=AssertionError('No live registrations')))
        source._OBSERVERS.pop(str(self.tree.resolve()), None)
        self.addCleanup(lambda:source._OBSERVERS.pop(str(self.tree.resolve()), None))

    def _snapshot(self):
        return {str(path.relative_to(self.base)):path.read_bytes()
                for path in self.base.rglob('*') if path.is_file()}

    def _desktop(self, *, identity='d'*32, kind='source-update', program=None, **changes):
        value = {'format':'helene-desktop-update-request.v2', 'id':identity, 'kind':kind,
            'program':str(program or self.old), 'tree':str(self.tree), 'state':'attention',
            'phase':'verification_requested', 'admission':{'pid':12001, 'birth':1.0},
            'helper':{'pid':12002, 'birth':2.0}, 'battery':{'passed':False},
            'review_evidence':{'verdict':'findings'}, 'source_mode':'clean'}
        value.update(changes)
        jobs._save(self.tree, value)
        return value

    def _source(self, *, identity='a'*64, program=None, old_scope=False, bind=True, **changes):
        directory = (self.old/'.source-updates'/identity if old_scope else
                     self.tree/'memory/.source-updates'/identity)
        request = {'id':identity, 'directory':str(directory), 'tree':str(self.tree),
            'input_sha256':'c'*64, 'state':'pending', 'resolution_owner':'agent'}
        if bind:request['program'] = str(program or self.old)
        operation = {**request, 'state':'waiting_agent', 'attempts':[],
            'battery':{'passed':False, 'report':'preserved failing evidence'},
            'review_clear':False, 'review_evidence':{'verdict':'findings'}}
        operation.update(changes)
        write(directory/'pipeline.json', operation)
        write(directory/'plan.json', {'state':'ready', 'result':{}})
        write(directory/'coordinator-process.json', {'pid':12003, 'birth':3.0, 'invocation':'captured-old'})
        write(source.path(self.tree), request)
        write(source._requests(self.tree)/(identity+'.json'), request)
        return request, operation, directory

    def _named_custody(self, *, review=True, never_started=False):
        request, operation, directory = self._source(state='ready_to_apply')
        evidence = []
        def unit(task_id, unit_id, kind, metadata, pid):
            root = self.tree/'memory/.forge/tasks'/task_id
            task = root/'task.json'
            write(task, {'id':task_id, 'status':'active', kind:metadata})
            target = root/'agents'/unit_id
            execution = {'id':unit_id, 'task_id':task_id, 'parent_unit_id':'', kind:metadata,
                'role':'worker' if kind=='source_update' else 'reviewer', 'status':'running',
                'supervisor_pid':pid, 'process_identity':{'adapter':'psutil-v1','pid':pid,'birth':float(pid)}}
            result = {'status':'done', 'complete':True, 'result':{'verdict':'findings'}}
            if never_started:
                execution.update(status='queued')
                execution.pop('supervisor_pid');execution.pop('process_identity')
                result = {'status':'stopped','never_started':True}
            write(target/'request.json', execution)
            write(target/'result.json', result)
            if never_started:
                write(target/'stop.json', {'state':'settled','result_sha256':hashlib.sha256((target/'result.json').read_bytes()).hexdigest()})
            evidence.extend([task,target/'request.json',target/'result.json'])
            if never_started:evidence.append(target/'stop.json')
            return target
        task_id, unit_id = 'code-11111111', 'agent-11111111'
        metadata = {'schema':1,'root':str(directory),'plan_id':operation['id'],
            'input_sha256':operation['input_sha256'],
            'worker_script':str(self.old/'app/localharness/source_update_worker.py'),
            'runtime_code_dir':str(self.old/'code')}
        merger = unit(task_id, unit_id, 'source_update', metadata, 12101)
        operation.update(task_id=task_id,unit_id=unit_id,attempts=[{'task_id':task_id,'unit_id':unit_id}])
        reviewer, review_path = None, None
        if review:
            review_id, subject = 'review-'+'2'*16, 'e'*64
            review_task, critic = 'code-22222222','agent-22222222'
            snapshot = self.tree/'memory/.source-update-reviews'/review_id/'snapshot'
            reviewer = unit(review_task,critic,'self_review',{'schema':1,'root':str(snapshot),
                'subject_sha256':subject,'worker_script':str(self.old/'app/localharness/self_review_worker.py'),
                'runtime_code_dir':str(self.old/'code')},12102)
            review_path = snapshot.parent/'request.json'
            write(review_path, {'schema':'helene.self-review.request.v1','id':review_id,
                'task_id':review_task,'snapshot':str(snapshot),'subject_sha256':subject,
                'execution':'cancelled' if never_started else 'done','verdict':'findings','decision':'pending',
                'critics':[{'unit_id':critic,'execution':'stopped' if never_started else 'done',
                    'verdict':'findings','result_path':str(reviewer/'result.json'),
                    'result_sha256':hashlib.sha256((reviewer/'result.json').read_bytes()).hexdigest()}]})
            operation.update(review_id=review_id,subject_sha256=subject,coordinator={'review_store':'isolated-v1'})
            evidence.append(review_path)
        write(directory/'pipeline.json',operation)
        return SimpleNamespace(request=request,operation=operation,directory=directory,
            merger=merger,reviewer=reviewer,review=review_path,evidence=evidence)

    def _capture(self):
        return supersession.capture(self.current, [self.old], [self.tree])

    def _retire(self, plan=None):
        return supersession.retire(plan or self._capture(), observe=self.observe)

    def test_all_exact_desktop_updater_kinds_retire_without_accepting_tests_or_review(self):
        for index, kind in enumerate(('migration', 'source-update', 'install', 'download')):
            with self.subTest(kind=kind):
                before = self._desktop(identity=str(index+1)*32, kind=kind)
                report = self._retire()
                after = jobs.status(self.tree)
                self.assertTrue(report['superseded'])
                self.assertEqual(after['state'], 'superseded')
                self.assertIn('without accepting tests or review', after['superseded_reason'])
                self.assertEqual(after['battery'], before['battery'])
                self.assertEqual(after['review_evidence'], before['review_evidence'])
                self.assertTrue(jobs.cancelled(self.tree, before['id']))
                self.assertTrue(supersession.blocked(after))
        self.assertEqual(read(self.user)['status'], 'active')
        self.assertEqual(self.history.read_bytes(), b'ordinary owner history must remain exact\n')

    def test_source_plan_is_terminal_without_fabricating_stop_review_or_battery_acceptance(self):
        request, operation, directory = self._source()
        report = self._retire()
        self.assertEqual(len(report['superseded']), 1)
        after = read(directory/'pipeline.json')
        self.assertEqual(after['state'], 'superseded')
        self.assertEqual(after['battery'], operation['battery'])
        self.assertEqual(after['review_evidence'], operation['review_evidence'])
        self.assertIs(after['review_clear'], False)
        self.assertNotIn('stop_confirmed', after)
        self.assertNotIn('cancel_requested', after)
        self.assertTrue(supersession.blocked(read(source.path(self.tree))))
        self.assertTrue(supersession.blocked(after, protocol='source'))
        self.assertIsNone(source.pending(self.tree))
        turn = Mock()
        agent.notify(self.tree, turn)
        turn.assert_not_called()
        self.assertEqual(read(self.user)['status'], 'active')

    def test_shared_tree_alone_never_grants_ownership_over_unbound_source_work(self):
        self._source(bind=False)
        before = self._snapshot()
        self.assertEqual(self._capture()['records'], [])
        self._retire()
        self.assertEqual(self._snapshot(), before)
        self.observe.assert_not_called()

    def test_explicit_captured_old_programme_directory_can_bind_a_legacy_source_record(self):
        request, operation, directory = self._source(old_scope=True, bind=False)
        self.assertEqual(len(self._capture()['records']), 1)
        self._retire()
        self.assertEqual(read(directory/'pipeline.json')['state'], 'superseded')
        self.assertTrue(supersession.blocked(read(source.path(self.tree)), protocol='source'))

    def test_current_unrelated_and_ordinary_owner_jobs_are_untouched(self):
        for kind, programme in (('analysis', self.old), ('source-update', self.current), ('migration', self.other)):
            with self.subTest(kind=kind, programme=programme.name):
                self._desktop(kind=kind, program=programme)
                before = self._snapshot()
                self._retire()
                self.assertEqual(self._snapshot(), before)
        self._source(program=self.current)
        before = self._snapshot()
        self._retire()
        self.assertEqual(self._snapshot(), before)

    def test_live_unknown_missing_or_unidentified_producers_never_retire_work(self):
        for state in ('alive', 'unknown'):
            with self.subTest(observed=state):
                self._desktop()
                self.observe.return_value = state
                before = self._snapshot()
                report = self._retire()
                self.assertTrue(report['blocked'])
                self.assertEqual(self._snapshot(), before)
        self._desktop(admission={'pid':12001}, helper={})
        before = self._snapshot()
        self.assertTrue(self._retire()['blocked'])
        self.assertEqual(self._snapshot(), before)

    def test_recycled_pid_is_absence_of_the_captured_generation(self):
        self._desktop()
        self.observe.return_value = 'changed'
        self.assertTrue(self._retire()['superseded'])
        self.assertEqual(jobs.status(self.tree)['state'], 'superseded')
        self.assertEqual({call.args[0]['pid'] for call in self.observe.call_args_list}, {12001, 12002})

    def test_newer_desktop_identity_replacing_captured_pointer_receives_no_action(self):
        old = self._desktop()
        plan = self._capture()
        old_history = (jobs.path(self.tree).parent/'desktop-update-jobs'/(old['id']+'.json')).read_bytes()
        newer = self._desktop(identity='e'*32, program=self.current)
        before = self._snapshot()
        self._retire(plan)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(jobs.status(self.tree)['id'], newer['id'])
        self.assertEqual((jobs.path(self.tree).parent/'desktop-update-jobs'/(old['id']+'.json')).read_bytes(), old_history)

    def test_newer_source_identity_replacing_captured_pointer_receives_no_action(self):
        _old, _operation, directory = self._source()
        plan = self._capture()
        before_operation = (directory/'pipeline.json').read_bytes()
        self._source(identity='b'*64, program=self.current)
        before = self._snapshot()
        self._retire(plan)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual((directory/'pipeline.json').read_bytes(), before_operation)

    def test_captured_old_source_history_never_overwrites_an_already_current_new_job(self):
        old_request, _operation, directory = self._source()
        new_request, _new_operation, new_directory = self._source(identity='b'*64, program=self.current)
        current_before = source.path(self.tree).read_bytes()
        new_before = (new_directory/'pipeline.json').read_bytes()
        report = self._retire()
        self.assertEqual({item['id'] for item in report['superseded']}, {old_request['id']})
        self.assertEqual(read(directory/'pipeline.json')['state'], 'superseded')
        self.assertEqual(source.path(self.tree).read_bytes(), current_before)
        self.assertEqual((new_directory/'pipeline.json').read_bytes(), new_before)
        self.assertEqual(read(source.path(self.tree))['id'], new_request['id'])

    def test_job_replacement_during_pid_observation_is_rechecked_before_writes(self):
        self._desktop()
        plan = self._capture()
        written = []
        def race(identity):
            if not written:written.append(self._desktop(identity='e'*32, program=self.current))
            return 'absent'
        self.observe.side_effect = race
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertEqual(jobs.status(self.tree)['id'], written[0]['id'])
        self.assertFalse(supersession.marker(self.tree, 'd'*32, 'desktop').exists())

    def test_new_source_producer_identity_during_observation_is_not_overwritten(self):
        _request, _operation, directory = self._source()
        plan = self._capture()
        def race(identity):
            write(directory/'coordinator-process.json', {'pid':13003, 'birth':30.0, 'invocation':'new-producer'})
            return 'absent'
        self.observe.side_effect = race
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertTrue(report['blocked'])
        self.assertEqual(read(directory/'pipeline.json')['state'], 'waiting_agent')
        self.assertEqual(read(directory/'coordinator-process.json')['pid'], 13003)

    def test_a_new_helper_added_to_the_same_job_after_capture_blocks_all_actions(self):
        self._desktop(helper={})
        plan = self._capture()
        current = read(jobs.path(self.tree))
        current['helper'] = {'pid':13002, 'birth':30.0}
        jobs._save(self.tree, current)
        before = self._snapshot()
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertTrue(report['blocked'])
        self.assertEqual(self._snapshot(), before)
        self.observe.assert_not_called()

    def test_pending_named_review_worker_is_uncertainty_not_implied_acceptance(self):
        self._source(review_id='review-'+'1'*16)
        before = self._snapshot()
        report = self._retire()
        self.assertTrue(report['blocked'])
        self.assertEqual(self._snapshot(), before)
        self.observe.assert_not_called()

    def test_ready_to_apply_named_workers_can_retire_without_a_fabricated_stop(self):
        fixture = self._named_custody()
        evidence = {path:path.read_bytes() for path in fixture.evidence}
        plan = self._capture()
        self.assertEqual(plan['records'][0]['problems'], [])
        report = self._retire(plan)
        self.assertEqual(len(report['superseded']),1)
        self.assertEqual({call.args[0]['pid'] for call in self.observe.call_args_list},{12003,12101,12102})
        after = read(fixture.directory/'pipeline.json')
        self.assertEqual(after['state'],'superseded')
        self.assertNotIn('stop_confirmed',after)
        self.assertNotIn('cancel_requested',after)
        self.assertEqual(after['battery'],fixture.operation['battery'])
        self.assertEqual(after['review_evidence'],fixture.operation['review_evidence'])
        self.assertIs(after['review_clear'],False)
        self.assertEqual({path:path.read_bytes() for path in fixture.evidence},evidence)
        self.assertEqual(read(fixture.review)['verdict'],'findings')
        self.assertEqual(read(fixture.review)['decision'],'pending')
        self.assertEqual(read(self.user)['status'],'active')

    def test_terminal_named_result_and_exited_heartbeat_do_not_prove_process_absence(self):
        for state in ('alive','unknown'):
            with self.subTest(observed=state):
                fixture = self._named_custody()
                write(fixture.reviewer/'liveness.json',{'state':'exited','basis':'stale presentation'})
                self.observe.side_effect = lambda identity:'absent' if identity['pid']!=12102 else state
                before = self._snapshot()
                report = self._retire()
                self.assertFalse(report['superseded'])
                self.assertTrue(report['blocked'])
                self.assertEqual(self._snapshot(),before)

    def test_named_worker_requires_exact_birth_subject_and_settled_terminal_custody(self):
        for mutation in ('missing-birth','wrong-task-subject','pending-stop','review-subject','review-result-hash'):
            with self.subTest(mutation=mutation):
                fixture = self._named_custody()
                if mutation=='missing-birth':
                    target = fixture.merger/'request.json'
                    value = read(target);value['process_identity'].pop('birth');write(target,value)
                elif mutation=='wrong-task-subject':
                    target = fixture.merger.parents[1]/'task.json'
                    value = read(target);value['source_update']['plan_id']='f'*64;write(target,value)
                elif mutation=='pending-stop':
                    write(fixture.merger/'stop.json',{'state':'pending'})
                else:
                    value = read(fixture.review)
                    if mutation=='review-subject':value['subject_sha256']='f'*64
                    else:value['critics'][0]['result_sha256']='f'*64
                    write(fixture.review,value)
                # A historical cancellation flag cannot replace missing custody.
                operation = read(fixture.directory/'pipeline.json')
                operation['stop_confirmed']=True;write(fixture.directory/'pipeline.json',operation)
                before = self._snapshot()
                report = self._retire()
                self.assertFalse(report['superseded'])
                self.assertTrue(report['blocked'])
                self.assertEqual(self._snapshot(),before)
        self.observe.assert_not_called()

    def test_named_task_manifest_cannot_gain_a_new_worker_after_capture(self):
        fixture = self._named_custody()
        plan = self._capture()
        added = fixture.merger.with_name('agent-33333333')
        write(added/'request.json',{'id':added.name,'supervisor_pid':13101,
            'process_identity':{'adapter':'psutil-v1','pid':13101,'birth':31.0}})
        before = self._snapshot()
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertTrue(report['blocked'])
        self.assertEqual(self._snapshot(),before)
        self.observe.assert_not_called()

    def test_an_added_historical_attempt_or_review_reference_invalidates_captured_custody(self):
        for field,reference in (('attempts',{'task_id':'code-33333333','unit_id':'agent-33333333'}),
                                ('agent_review_rounds',{'review_id':'review-'+'3'*16,'subject_sha256':'f'*64})):
            with self.subTest(field=field):
                fixture = self._named_custody()
                plan = self._capture()
                operation = read(fixture.directory/'pipeline.json')
                operation.setdefault(field,[]).append(reference)
                write(fixture.directory/'pipeline.json',operation)
                before = self._snapshot()
                report = self._retire(plan)
                self.assertFalse(report['superseded'])
                self.assertTrue(report['blocked'])
                self.assertEqual(self._snapshot(),before)
        self.observe.assert_not_called()

    def test_named_terminal_evidence_changed_during_observation_blocks_writes(self):
        fixture = self._named_custody()
        plan = self._capture()
        def race(identity):
            write(fixture.reviewer/'result.json',{'status':'done','result':{'verdict':'changed'}})
            return 'absent'
        self.observe.side_effect=race
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertTrue(report['blocked'])
        self.assertEqual(read(fixture.directory/'pipeline.json')['state'],'ready_to_apply')
        self.assertFalse(supersession.marker(self.tree,fixture.request['id'],'source').exists())

    def test_measured_never_started_units_need_no_invented_process_identity(self):
        fixture = self._named_custody(never_started=True)
        evidence = {path:path.read_bytes() for path in fixture.evidence}
        report = self._retire()
        self.assertEqual(len(report['superseded']),1)
        self.assertEqual({call.args[0]['pid'] for call in self.observe.call_args_list},{12003})
        self.assertEqual({path:path.read_bytes() for path in fixture.evidence},evidence)
        self.assertNotIn('process_identity',read(fixture.merger/'request.json'))

    def test_unexpected_legacy_command_unit_remains_uncertain_in_a_named_task(self):
        fixture = self._named_custody(review=False)
        command = fixture.merger.parents[1]/'processes/process-33333333'
        write(command/'request.json',{'id':command.name,'task_id':'code-11111111',
            'supervisor_pid':12103,'supervisor_started_at':'legacy opaque birth'})
        write(command/'result.json',{'status':'done'})
        before = self._snapshot()
        report = self._retire()
        self.assertFalse(report['superseded'])
        self.assertTrue(report['blocked'])
        self.assertEqual(self._snapshot(),before)
        self.observe.assert_not_called()

    def test_capture_problems_do_not_block_work_that_is_now_terminal_or_replaced(self):
        for transition in ('pipeline-applied','request-completed','new-current-identity'):
            with self.subTest(transition=transition):
                request,operation,directory = self._source(review_id='review-'+'1'*16)
                plan = self._capture()
                self.assertTrue(plan['records'][0]['problems'])
                if transition=='pipeline-applied':
                    operation['state']='applied';write(directory/'pipeline.json',operation)
                elif transition=='request-completed':
                    request['state']='completed';write(source.path(self.tree),request)
                else:self._source(identity='b'*64,program=self.current)
                before = self._snapshot()
                report = self._retire(plan)
                self.assertFalse(report['blocked'])
                self.assertFalse(report['superseded'])
                self.assertTrue(report['skipped'])
                self.assertEqual(self._snapshot(),before)
        self.observe.assert_not_called()

    def test_staged_coordinator_is_part_of_the_captured_old_producer_set(self):
        _request, _operation, directory = self._source()
        from localharness.program_state import staging
        staged = staging(self.old, 'fixture')
        write(staged/'Helene/code-transition-plan.json', {'plans':[{'directory':str(directory)}]})
        self._desktop(staging=str(staged))
        self.observe.side_effect = lambda identity:'alive' if identity['pid']==12003 else 'absent'
        before = self._snapshot()
        report = self._retire()
        self.assertFalse(report['superseded'])
        self.assertEqual(self._snapshot(), before)
        self.assertIn(12003, {call.args[0]['pid'] for call in self.observe.call_args_list})

    def test_unproven_staging_or_external_plan_scope_never_grants_retirement(self):
        from localharness.program_state import staging
        staged = staging(self.old, 'fixture')
        self._desktop(staging=str(staged))
        before = self._snapshot()
        self.assertTrue(self._retire()['blocked'])
        self.assertEqual(self._snapshot(), before)
        outside = self.other/'owner-task'
        write(outside/'pipeline.json', {'id':'a'*64, 'program':str(self.old), 'tree':str(self.tree)})
        write(staged/'Helene/code-transition-plan.json', {'plans':[{'directory':str(outside)}]})
        before = self._snapshot()
        self.assertTrue(self._retire()['blocked'])
        self.assertEqual(self._snapshot(), before)
        self.observe.assert_not_called()

    def test_removed_old_pipeline_is_not_recreated_when_retiring_its_durable_pointer(self):
        self._source(old_scope=True)
        plan = self._capture()
        import shutil
        shutil.rmtree(self.old)
        report = self._retire(plan)
        self.assertTrue(report['superseded'])
        self.assertFalse(self.old.exists())
        self.assertEqual(read(source.path(self.tree))['state'], 'superseded')

    def test_tampered_capture_plan_cannot_enlarge_job_scope(self):
        self._desktop()
        plan = self._capture()
        plan['old_programs'].append(str(self.other))
        before = self._snapshot()
        with self.assertRaises(ValueError):self._retire(plan)
        self.assertEqual(self._snapshot(), before)
        self.observe.assert_not_called()

    def test_supersession_is_idempotent_and_never_rewrites_terminal_evidence(self):
        self._desktop()
        self._source()
        plan = self._capture()
        self._retire(plan)
        before = self._snapshot()
        report = self._retire(plan)
        self.assertFalse(report['superseded'])
        self.assertEqual(self._snapshot(), before)

    def test_tombstone_matches_exact_programme_job_and_input_not_new_work(self):
        request, _operation, _directory = self._source()
        self._retire()
        self.assertTrue(supersession.blocked(request))
        self.assertFalse(supersession.blocked(dict(request, program=str(self.current))))
        self.assertFalse(supersession.blocked(dict(request, id='b'*64)))
        self.assertFalse(supersession.blocked(dict(request, input_sha256='different-input')))

    def test_moved_marker_fences_restored_original_summary_without_rewriting_identity(self):
        request,operation,directory = self._source()
        desktop = self._desktop()
        self._retire()
        moved = self.base/'canonical after raw data move'
        self.tree.rename(moved)
        # This deliberately restores the exact stale pre-retirement summaries.
        # Their original absolute tree/directory fields remain original evidence.
        write(source.path(moved),request)
        write(jobs.path(moved),desktop)
        before={str(path.relative_to(moved)):path.read_bytes() for path in moved.rglob('*') if path.is_file()}
        self.assertFalse(supersession.blocked(request,protocol='source'))
        self.assertFalse(supersession.blocked(desktop,protocol='desktop'))
        self.assertTrue(supersession.blocked(request,protocol='source',current_tree=moved))
        self.assertTrue(supersession.blocked(operation,protocol='source',current_tree=moved))
        self.assertTrue(supersession.blocked(desktop,protocol='desktop',current_tree=moved))
        for changed in (dict(request,program=str(self.current)),dict(request,tree=str(moved)),
                        dict(request,directory=str(moved/'memory/.source-updates'/request['id'])),
                        dict(request,input_sha256='f'*64)):
            with self.subTest(changed=changed):
                self.assertFalse(supersession.blocked(changed,protocol='source',current_tree=moved))
        self.assertFalse(supersession.blocked(request,protocol='source',current_tree=self.other))
        self.assertEqual({str(path.relative_to(moved)):path.read_bytes() for path in moved.rglob('*') if path.is_file()},before)
        self.assertFalse(self.tree.exists(),'Lookup must never recreate the retired tree')
        self.assertEqual(read(source.path(moved)),request)
        self.assertEqual(read(jobs.path(moved)),desktop)

    def test_restored_stale_summary_cannot_emit_a_fake_agent_reminder(self):
        request, operation, directory = self._source()
        self._retire()
        write(source.path(self.tree), request)
        write(directory/'pipeline.json', operation)
        self.assertTrue(supersession.blocked(request))
        turn = Mock()
        with patch.object(source, '_bound', return_value=(directory, {'state':'ready', 'result':{}})):
            agent.notify(self.tree, turn)
        turn.assert_not_called()

    def test_restored_stale_summary_cannot_restart_a_source_pipeline(self):
        request, operation, directory = self._source()
        self._retire()
        write(source.path(self.tree), request)
        write(source._requests(self.tree)/(request['id']+'.json'), request)
        write(directory/'pipeline.json', operation)
        with patch.object(source, '_advance') as advance, patch.object(source, '_stop') as stop:
            source.tick(self.tree, Mock(), Mock())
        advance.assert_not_called()
        stop.assert_not_called()


if __name__=='__main__':unittest.main()
