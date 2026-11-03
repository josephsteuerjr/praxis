"""Causal typed-subject and ordinary-review consumers; no model/network calls."""
from contextvars import ContextVar
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'desk'), str(ROOT/'desk/tests'), str(ROOT/'helene/core')]
from localharness import self_review, self_review_integration as integration, self_review_subjects as subjects
from localharness.self_review_worker import SnapshotReviewer
import t_self_review_192 as source_fixture
import t_self_review_integration_192 as integration_fixture


def plan(text='Exact plan\nСохранить байты без подмены.'):
    return {'kind':'plan', 'task_id':'task-original', 'question':'Does this meet the brief?',
            'criteria':['Keep original materials'], 'text':text}


class TypedSubjects(unittest.TestCase):
    setUp = source_fixture.ReviewTests.setUp
    request = source_fixture.ReviewTests.request
    result = source_fixture.ReviewTests.result

    def test_author_selects_ordinary_critic_count_not_routes_or_strict_panel(self):
        row=self.request(subject=plan(),depth='deep',critics=1)
        self.assertEqual(len(row['critics']),1)
        row=self.request(subject=plan(),critics=3)
        self.assertEqual(len(row['critics']),3)
        self.assertEqual({critic['model'] for critic in row['critics']},{''})
        for count in (0,9,True):
            with self.assertRaises(ValueError):self.request(subject=plan(),critics=count)
        with self.assertRaisesRegex(ValueError,'ordinary'):
            self.request(trigger='contested_overlay',critics=1)

    def test_full_composed_harness_keeps_static_resources_and_binary_gaps(self):
        from localharness.update_composition import app_inventory
        harness=self.root/'composed-app'
        for name,raw in [('static/app.js',b'let CHANGED = true;'),
                         ('resources/ui/theme.json',b'{"accent":"violet"}'),
                         ('resources/soul/SOUL.md',b'Factory template, not live private persona.'),
                         ('localharness/runner.py',b'VALUE = 7\n'),
                         ('resources/icon.png',b'\x89PNG\x00\xff')]:
            path=harness/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
        declared=app_inventory(harness)
        sources={'harness':{'root':str(harness),'merge_files':declared,'inventory_scope':'full-composed-app-v1'}}
        row=self.request(trigger='contested_overlay',sources=sources,
                         overlay={'merge_identity':{'composition_sha256':'c'*64,'battery_sha256':'b'*64}})
        subject=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        entries={r['path']:r for r in subject['manifest']}
        self.assertEqual((Path(row['snapshot'])/'harness/static/app.js').read_bytes(),b'let CHANGED = true;')
        self.assertIn('harness/resources/ui/theme.json',entries)
        self.assertIn('harness/resources/soul/SOUL.md',entries)
        self.assertFalse(entries['harness/resources/soul/SOUL.md'].get('withheld'))
        self.assertIn('withheld',entries['harness/resources/icon.png'])
        self.assertFalse((Path(row['snapshot'])/'harness/resources/icon.png').exists())
        self.assertTrue(subject['gaps']);self.assertTrue(row['required'])
        self.assertEqual(subject['merge_identity']['composition_sha256'],'c'*64)
        self.service.tick();self.result(row);self.service.tick()
        self.assertEqual(self.service.status(row['id'])['verdict'],'insufficient')
        (harness/'static/app.js').write_text('STALE = true;')
        with self.assertRaisesRegex(ValueError,'inventory changed'):self.service.freshness(row['id'])

    def test_only_exact_strict_published_assets_have_opaque_identity_evidence(self):
        from localharness import source_merge
        harness=self.root/'binary-app';harness.mkdir()
        for name in ('icon.png','font.woff2','changed.png','unknown.png','nonutf8.py'):
            (harness/name).write_bytes(b'\x89BINARY\x00\xff')
        declared=source_merge.inventory(harness)
        distribution=self.root/'frozen-release';distribution.mkdir()
        proofs={}
        for name in ('python-package-files.json','source-inputs.json'):
            path=distribution/name
            path.write_text(json.dumps({'published_assets':declared}),encoding='utf-8')
            proofs[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        evidence={'format':'helene-distribution-evidence.v1','composition_sha256':'c'*64,
                  'package_manifest_sha256':proofs['python-package-files.json'],'source_inputs_sha256':proofs['source-inputs.json'],
                  'package_manifest_ref':'distribution/python-package-files.json','source_inputs_ref':'distribution/source-inputs.json'}
        source={'root':str(harness),'merge_files':declared,'inventory_scope':'full-composed-app-v1',
                'release_evidence':evidence,'published_files':{name:[declared[name]['sha256']] for name in
                ('icon.png','font.woff2','nonutf8.py')}}
        # Changed bytes cannot borrow the original published digest.
        source['published_files']['changed.png']=['d'*64]
        service=self_review.Service(self.root/'home',self.code,self.app,self.forge,
            coordinator={'kind':'incoming-update-runtime.v1','review_store':'isolated-v1'})
        distribution_source={'root':str(distribution),'files':{name:name for name in proofs},
                             'published_files':{name:[digest] for name,digest in proofs.items()}}
        kwargs={'trigger':'contested_overlay','sources':{'harness':source,'distribution':distribution_source},
                'overlay':{'merge_identity':{'composition_sha256':'c'*64}}}
        row=service.request(**kwargs)
        frozen=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        entries={r['path']:r for r in frozen['manifest']}
        for name in ('icon.png','font.woff2'):
            entry=entries['harness/'+name]
            self.assertIn('opaque',entry);self.assertNotIn('withheld',entry)
            self.assertEqual(entry['sha256'],declared[name]['sha256'])
            self.assertEqual(entry['release_evidence'],evidence)
            self.assertFalse((Path(row['snapshot'])/'harness'/name).exists())
        for name in ('changed.png','unknown.png','nonutf8.py'):
            self.assertIn('withheld',entries['harness/'+name])
        critic=SnapshotReviewer({'root':row['snapshot'],'subject_sha256':row['subject_sha256']})
        read=json.loads(critic.dispatch({},'inspect',{'action':'read','path':'harness/icon.png'}))
        self.assertIn('opaque',read);self.assertNotIn('text',read)
        proof=json.loads(critic.dispatch({},'inspect',{'action':'read','path':evidence['package_manifest_ref']}))
        self.assertEqual(json.loads(proof['text'])['published_assets'],declared)
        self.assertIn('NOT been decoded',service._brief(row,row['critics'][0]))
        # Same material outside the strict coordinator remains a gap, even with claimed metadata.
        ordinary=self.request(**kwargs)
        ordinary_subject=self_review.validate_snapshot(ordinary['snapshot'],ordinary['subject_sha256'])
        self.assertFalse(any(r.get('opaque') for r in ordinary_subject['manifest']))
        wrong=copy.deepcopy(kwargs);wrong['overlay']['merge_identity']['composition_sha256']='e'*64
        wrongrow=service.request(**wrong)
        wrongsubject=self_review.validate_snapshot(wrongrow['snapshot'],wrongrow['subject_sha256'])
        self.assertFalse(any(r.get('opaque') for r in wrongsubject['manifest']))
        (distribution/'source-inputs.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'distribution manifest changed'):service.freshness(row['id'])

    def material(self, name, content, side='context', logical=None):
        root = self.root/'home/workspace'
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8'))
        return {'name':logical or name,'root':'workspace','path':name,'side':side,
                'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

    def test_plan_frozen_exact_worker_material_reader_no_source_substitution(self):
        value = plan('Exact multi-line plan\n' + 'Unicode змей🐍\n'*900)
        value['materials']=[self.material('notes/report.json','{"observed":"not certified"}')]
        row = self.request(subject=value)
        self.assertFalse(row['required']);self.assertEqual(row['audience'],'owner')
        self.assertEqual(row['sources'],{})
        self.service.tick()
        self.assertEqual(len(self.spawns),1)
        request = self.spawns[0][2]
        self.assertIn('frozen plan',request['brief'])
        critic = SnapshotReviewer(request['self_review'])
        text='';cursor=0
        while True:
            page=json.loads(critic.dispatch({},'inspect',{'action':'read','path':'authored.json','end':10000,'start_char':cursor}))
            text+=page['text']
            if page['next_char'] is None:break
            cursor=page['next_char']
        self.assertEqual(json.loads(text)['text'],value['text'])
        self.assertIn('not certified',critic.dispatch({},'inspect',{'action':'read','path':'materials/context/notes/report.json'}))
        self.assertIn('Not a declared',critic.dispatch({},'inspect',{'action':'read','path':'agent/agent.py'}))
        self.assertIn('Refused',critic.dispatch({},'run',{}))
        # Live changed material is never read by the critic; old snapshot remains exact.
        (self.root/'home/workspace/notes/report.json').write_text('REPLACED')
        self.assertIn('not certified',critic.dispatch({},'inspect',{'action':'read','path':'materials/context/notes/report.json'}))
        self.assertTrue(self.service.freshness(row['id'])['stale'])
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            self.request(subject=value,operation_id='new-not-old')

    def test_change_freezes_both_sides_absences_and_exact_diff(self):
        value={'kind':'change','question':'Check this exact delta','criteria':['No regressions'],
               'materials':[self.material('before.py','VALUE = 1\n','base','program.py'),
                            self.material('after.py','VALUE = 2\n','candidate','program.py'),
                            {'name':'added.py','side':'base','root':'workspace','path':'absent.py','exists':False},
                            self.material('new.py','NEW = True\n','candidate','added.py')]}
        row=self.request(subject=value)
        frozen=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        self.assertEqual(frozen['subject_kind'],'change')
        diff=(Path(row['snapshot'])/'changes.diff').read_text(encoding='utf-8')
        self.assertIn('-VALUE = 1',diff);self.assertIn('+VALUE = 2',diff);self.assertIn('+NEW = True',diff)
        self.assertFalse(self.service.freshness(row['id'])['stale'])
        bad=copy.deepcopy(value);bad['materials'].pop()
        with self.assertRaisesRegex(ValueError,'matching'):
            self.request(subject=bad)

    def test_task_result_claims_and_findings_remain_distinct_from_author_disposition(self):
        value={'kind':'task_result','question':'Did the answer meet the original task?',
               'criteria':['No invented effects'],'assignment':'Write a report',
               'answer':'Exact report, not installed', 'observed_effects':['author CLAIM: no install'],
               'checks':['author CLAIM: inspected'], 'materials':[self.material('result.txt','Result evidence')]}
        row=self.request(subject=value);self.service.tick()
        self.assertIn("author's claims",self.spawns[0][2]['brief'])
        result_path=self.result(row,verdict='findings');raw=result_path.read_bytes();self.service.tick()
        decision=self.service.disposition(row['id'],'continue-with-reason','author chooses completion',row['subject_sha256'])
        self.assertEqual(decision['verdict'],'findings');self.assertEqual(raw,result_path.read_bytes())
        self.assertFalse(decision['required']);self.assertIsNone(decision['coordinator'])
        with self.assertRaises(ValueError):
            self.service.disposition(row['id'],'accepted','turn disagreement into clear',row['subject_sha256'])

    def test_operation_identity_never_substitutes_old_subject_or_default_source(self):
        one=self.request(subject=plan(),operation_id='same')
        self.assertEqual(self.request(subject=plan(),operation_id='same')['id'],one['id'])
        with self.assertRaisesRegex(ValueError,'different review subject'):
            self.request(subject=plan('NEW CONTENT'),operation_id='same')
        with self.assertRaisesRegex(ValueError,'different review subject'):
            self.request(operation_id='same')
        two=self.request(subject=plan('NEW CONTENT'),operation_id='new')
        self.assertNotEqual(one['subject_sha256'],two['subject_sha256'])
        self.assertNotEqual(one['id'],two['id'])
        with self.assertRaises(ValueError):self.request(subject=plan(),retry=True)

    def test_typed_never_enters_strict_coordinator_or_contested_path(self):
        for kwargs in ({'trigger':'contested_overlay'}, {'overlay':{'merge_identity':{'exact':'x'}}},
                       {'sources':{'agent':self.code}}):
            with self.assertRaisesRegex(ValueError,'ordinary'):
                self.request(subject=plan(),**kwargs)
        service=self_review.Service(self.root/'home',self.code,self.app,self.forge,coordinator={'operation':'update'})
        with self.assertRaisesRegex(ValueError,'ordinary'):service.request(subject=plan())
        # Actual default strict source subject is untouched, including arbitrary composition identity.
        row=self.request(trigger='contested_overlay',overlay={'merge_identity':{'composition_sha256':'c'*64,'battery':'bound'}})
        subject=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        self.assertTrue(row['required']);self.assertNotIn('typed_subject',subject)
        self.assertEqual(subject['merge_identity']['composition_sha256'],'c'*64)

    def test_private_paths_credentials_and_unbound_missing_material_refused(self):
        ref=self.material('okay.txt','public artifact')
        for path in ('../memory/private.md','memory/private.md','helene.json','AGENTS.md','C:/private','data/private'):
            value=plan();value['materials']=[{**ref,'path':path}]
            with self.assertRaises(ValueError):self.request(subject=value)
        value=plan('api_key = "credential-value-long"')
        with self.assertRaisesRegex(ValueError,'credential'):self.request(subject=value)
        value=plan();value['materials']=[self.material('secret.txt','password = "private-password"')]
        with self.assertRaisesRegex(ValueError,'credential'):self.request(subject=value)
        value=plan();value['materials']=[{**ref,'path':'missing.txt'}]
        with self.assertRaises(OSError):self.request(subject=value)
        value=plan();value['materials']=[{k:v for k,v in ref.items() if k!='sha256'}]
        with self.assertRaisesRegex(ValueError,'SHA-256'):self.request(subject=value)

    def test_tampered_frozen_task_result_never_spawns(self):
        row=self.request(subject=plan())
        (Path(row['snapshot'])/'authored.json').write_text('SUBSTITUTED')
        self.service.tick();self.assertEqual(self.spawns,[])
        self.assertEqual(self.service.status(row['id'])['execution'],'error')


class TypedCommand(unittest.TestCase):
    setUp=integration_fixture.Runtime.setUp
    make_runtime=integration_fixture.Runtime.make_runtime
    tick=integration_fixture.Runtime.tick

    def test_real_owner_hand_command_tick_restart_exact_subject(self):
        hand=self.agent.TOOL_IMPL['self_review']
        value=plan()
        self.assertIn('owner dialogue',hand(action='request',subject=value))
        self.agent._TURN_CHANNEL.set(types.SimpleNamespace(owner=True))
        queued=json.loads(hand(action='request',subject=value,critics=2))
        self.tick();row=self.runtime.service.status()[0]
        frozen=self_review.validate_snapshot(row['snapshot'],row['subject_sha256'])
        self.assertEqual(frozen['typed_subject']['text'],value['text'])
        self.assertIn(queued['command_id'],row['operations'])
        self.runtime=self.make_runtime();self.tick()
        self.assertEqual(len(self.forge.spawned),2)
        self.assertEqual(self.runtime.service.status()[0]['id'],row['id'])
        # No ambient caller-supplied audience/coordinator or source-state privilege.
        bad=plan();bad['audience']='public'
        with self.assertRaises(ValueError):integration.command(self.tree,{'action':'request','subject':bad})

    def test_queued_reference_hash_change_errors_instead_of_reviewing_new_material(self):
        workspace=self.tree/'workspace';workspace.mkdir()
        path=workspace/'report.md';path.write_text('ORIGINAL')
        value=plan();value['materials']=[{'name':'report.md','root':'workspace','path':'report.md',
                                        'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}]
        queued=integration.command(self.tree,{'action':'request','subject':value})
        path.write_text('NEW-PRIVATE-TEXT')
        self.tick()
        command=next(c for c in integration.status(self.tree)['commands'] if c['command_id']==queued['command_id'])
        self.assertEqual(command['state'],'error');self.assertFalse(self.forge.spawned)
        self.assertEqual(self.runtime.service.status(),[])

    def test_ordinary_default_opt_in_is_off_explicit_choice_remains(self):
        self.assertFalse(self_review.settings({})['after_self_edit'])
        self.assertTrue(self_review.settings({'after_self_edit':True})['after_self_edit'])
        self.config.write_text('{}');self.tick(paused=True)
        self.assertEqual(self.runtime.after_self_edit('new-sha','edited',['agent.py'])['state'],'disabled')
        self.assertEqual(self.runtime.service.status(),[])


if __name__=='__main__':unittest.main(verbosity=2)
