"""Disposable file custody/terminal indexing, not semantic review approval."""
from pathlib import Path
import hashlib,json,shutil,sys,tempfile,unittest,uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_materials as material,source_merge as merge,source_update
from localharness.package_notice import read,write


class Materials(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='source-materials-');self.addCleanup(temp.cleanup)
        self.base=Path(temp.name);self.old=self.base/'old';self.program=self.base/'new';self.data=self.base/'data'
        for path in (self.old,self.program,self.data/'memory'):path.mkdir(parents=True)
        self.history=self.data/'memory/history.jsonl';self.history.write_bytes(b'authored history\n')
        self.source=self.base/'local';self.upstream=self.base/'upstream';self.original=self.base/'base'
        for path in (self.source,self.upstream,self.original):
            path.mkdir();(path/'module.py').write_bytes(b'value=True\n')
        identity=hashlib.sha256(str(self.base).encode()).hexdigest()
        self.directory=self.data/'memory/.source-updates'/identity
        self.plan=merge.create(self.original,self.source,self.upstream,self.directory)
        write(self.program/'code-transition-plan.json',{'plans':[{'directory':str(self.directory)}]})
        self.note={'id':uuid.uuid4().hex,'old_root':str(self.old),'new_root':str(self.program),
                   'data_paths':[str(self.data)],'old_data_paths':[str(self.data)],'state':'prepared','source_policy':'preserve'}
        # A separate live candidate is retained; only generated cache payloads
        # may disappear, never the installed source or authored data.
        (self.program/'tree').mkdir();(self.program/'tree/module.py').write_bytes(b'value=True\n')

    def operation(self):
        request={'id':self.directory.name,'tree':str(self.data),'directory':str(self.directory),
                 'program':str(self.old),'source':str(self.source),'version':'fixture',
                 'input_sha256':merge.input_signature(self.plan),'state':'ready_to_apply','resolution_owner':'agent'}
        write(self.directory/'pipeline.json',request);source_update._save_request(self.data,request)

    def confirm(self):
        self.note.update(state='new_app_started',startup_proof={'fixture_only':True,'tree':self.note['data_paths'][0]})

    def test_stock_payloads_retire_only_after_startup_and_receipts_remain(self):
        material.capture(self.program,self.note)
        with self.assertRaisesRegex(ValueError,'confirmed startup'):material.retire(self.program,self.note)
        self.assertTrue((self.directory/'base/module.py').exists())
        self.confirm();result=material.retire(self.program,self.note)
        self.assertEqual(result['state'],'retired');self.assertGreater(result['removed_files'],0)
        for name in material.PAYLOADS:self.assertFalse((self.directory/name).exists())
        self.assertTrue((self.directory/'plan.json').is_file())
        self.assertEqual(self.history.read_bytes(),b'authored history\n')
        self.assertEqual((self.program/'tree/module.py').read_bytes(),b'value=True\n')
        self.assertEqual(material.retire(self.program,self.note),result)

    def test_applied_status_survives_retired_payloads_and_rejects_further_edits(self):
        self.operation();material.capture(self.program,self.note);self.confirm()
        material.retire(self.program,self.note)
        status=source_update.command(self.data)
        self.assertEqual(status['state'],'applied');self.assertEqual(status['installed_program'],str(self.program))
        with self.assertRaisesRegex(ValueError,'already installed'):source_update.command(self.data,'resolve',path='module.py')
        self.assertFalse((self.directory/'result').exists())

    def test_changed_and_unknown_material_is_preserved(self):
        material.capture(self.program,self.note);self.confirm()
        changed=self.directory/'base/module.py';changed.write_bytes(b'later owner edit\n')
        unknown=self.directory/'result/personal.txt';unknown.write_bytes(b'keep unknown\n')
        empty=self.directory/'local/later-owner-folder';empty.mkdir()
        result=material.retire(self.program,self.note)
        self.assertEqual(result['state'],'retained-material')
        self.assertEqual(changed.read_bytes(),b'later owner edit\n');self.assertEqual(unknown.read_bytes(),b'keep unknown\n')
        self.assertTrue(empty.is_dir())
        self.assertFalse((self.directory/'incoming').exists())

    def test_relocated_control_index_is_updated_without_rewriting_history(self):
        self.operation();material.capture(self.program,self.note)
        destination=self.base/'new-data';shutil.copytree(self.data,destination)
        self.note.update(data_paths=[str(destination)],data_relocation={'format':'helene-data-relocation.v1','state':'verified',
                         'aliases':{str(self.data):str(destination)}})
        self.confirm()
        self.assertTrue(self.data.resolve().is_relative_to(self.base.resolve()))
        def readonly(function,path,error):
            from localharness.update_io import remove_created_file
            if function is not __import__('os').unlink or not isinstance(error,PermissionError):raise error
            remove_created_file(path)
        shutil.rmtree(self.data,onexc=readonly)
        result=material.retire(self.program,self.note);self.assertEqual(result['state'],'retired')
        status=source_update.command(destination);self.assertEqual(status['state'],'applied')
        from localharness.source_update_activity import activity
        projection=activity(destination)
        self.assertEqual(projection['state'],'completed');self.assertFalse(projection['live'])
        self.assertEqual(projection['role'],'none')
        current=read(source_update.path(destination))
        self.assertTrue(Path(current['directory']).is_relative_to(destination))
        self.assertEqual((destination/'memory/history.jsonl').read_bytes(),b'authored history\n')

    def test_newer_job_is_not_overwritten_by_committed_old_job_cleanup(self):
        self.operation();material.capture(self.program,self.note);self.confirm()
        previous=read(self.directory/'pipeline.json');previous['state']='cancelled';write(self.directory/'pipeline.json',previous)
        source_update._cancel_marker(self.directory,'superseded fixture')
        newer={'id':hashlib.sha256(b'newer fixture').hexdigest(),'state':'queued','tree':str(self.data)}
        write(source_update.path(self.data),newer);before=source_update.path(self.data).read_bytes()
        material.retire(self.program,self.note)
        self.assertEqual(source_update.path(self.data).read_bytes(),before)
        historical=read(source_update._requests(self.data)/(self.directory.name+'.json'))
        self.assertEqual(historical['installed_receipt']['id'],self.note['id'])
        self.assertEqual(historical['state'],'cancelled')

    def test_unverified_data_transfer_cannot_retire_source_material(self):
        material.capture(self.program,self.note);self.confirm()
        self.note['data_relocation']={'format':'helene-data-relocation.v1','state':'copying','aliases':{}}
        with self.assertRaisesRegex(ValueError,'not verified'):material.retire(self.program,self.note)
        self.assertTrue((self.directory/'base/module.py').is_file())

    def test_review_producers_must_settle_and_evidence_survives_payload_retirement(self):
        self.operation()
        record_path=self.data/'memory/.source-update-reviews/review-fixture/request.json'
        subject=record_path.parent/'subject';subject.mkdir(parents=True)
        payload=subject/'resolved/module.py';payload.parent.mkdir();payload.write_bytes(b'reviewed source\n')
        write(subject/'subject.json',{'manifest':[{'path':'resolved/module.py','size':payload.stat().st_size,
            'sha256':hashlib.sha256(payload.read_bytes()).hexdigest()}]})
        record={'snapshot':str(subject),'subject_sha256':hashlib.sha256((subject/'subject.json').read_bytes()).hexdigest(),
                'operations':['source-update:'+self.directory.name+':fixture'],'execution':'done',
                'critics':[{'execution':'running'}],'verdict':'fixture custody only, not approval'}
        write(record_path,record)
        with self.assertRaisesRegex(ValueError,'must settle'):material.capture(self.program,self.note)
        record['critics'][0]['execution']='done';write(record_path,record)
        evidence=record_path.parent/'result.json';evidence.write_bytes(b'original review evidence')
        manifest=(subject/'subject.json').read_bytes()
        material.capture(self.program,self.note);self.confirm();result=material.retire(self.program,self.note)
        self.assertEqual(result['state'],'retired');self.assertFalse(payload.exists())
        self.assertEqual((subject/'subject.json').read_bytes(),manifest)
        self.assertEqual(evidence.read_bytes(),b'original review evidence')
        self.assertEqual(read(record_path)['subject_sha256'],record['subject_sha256'])

    def test_edited_cleanup_manifest_cannot_nominate_other_owner_data(self):
        material.capture(self.program,self.note);self.confirm()
        personal=self.data/'personal';personal.mkdir();keep=personal/'keep.txt';keep.write_bytes(b'owner bytes')
        manifest=read(self.program/'source-materials.json')
        manifest['plans'][0]['roots'][0].update(path=str(personal),files={'keep.txt':{
            'sha256':hashlib.sha256(keep.read_bytes()).hexdigest(),'bytes':keep.stat().st_size}})
        write(self.program/'source-materials.json',manifest)
        with self.assertRaisesRegex(ValueError,'Unknown source payload root'):material.retire(self.program,self.note)
        self.assertEqual(keep.read_bytes(),b'owner bytes')

    def test_old_live_clean_preparer_is_upgraded_from_exact_choice_after_new_startup(self):
        from localharness import code_transition,update_source
        incoming=self.base/'incoming-program';(incoming/'tree').mkdir(parents=True)
        (incoming/'tree/module.py').write_bytes(b'value=True\n')
        code_transition.seal(incoming)
        write(self.program/'helene.json',{'tree':str(self.data),'code':'tree'})
        with self.assertRaisesRegex(ValueError,'чистая замена'):
            update_source.select(self.program,incoming,'preserve',by='window')
        update_source.select(self.program,incoming,'clean',by='window')
        self.note.pop('source_policy');self.confirm()
        with self.assertRaisesRegex(ValueError,'committed startup'):
            material.ensure_committed_materials(self.program,incoming,self.note)
        self.assertFalse((self.program/'source-materials.json').exists())
        self.note['state']='completed'
        material.ensure_committed_materials(self.program,incoming,self.note)
        self.assertEqual(self.note['source_policy'],'clean')
        result=material.retire(self.program,self.note)
        self.assertEqual(result['state'],'retired')
        self.assertEqual(self.history.read_bytes(),b'authored history\n')
        # Rejoining the same installed request needs no deleted source payload.
        material.ensure_committed_materials(self.program,incoming,self.note)
        self.assertEqual(material.retire(self.program,self.note),result)


if __name__=='__main__':unittest.main()
