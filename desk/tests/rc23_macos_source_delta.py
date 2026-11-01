"""Scoped source validation on native Darwin, without rebuilding a release."""
from pathlib import Path
import hashlib,json,os,platform,stat,subprocess,sys,time,unittest,zipfile

REPO=Path(__file__).resolve().parents[2]
TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-rc23-source'
OUT.mkdir(exist_ok=True)
OUTER_SHA='a2c375d594aaf31e7cf0f57b598a70a16473ce26dc8acb492ae44afd606970ae'
ARCHIVE_SHA='baa533b05f4b63c69be3547122ae38fd21ca87012d68fea20445bd4b19da40f5'
SUBJECT=TEMP/'rc23-subject-input'

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def prepare():
    assert sys.platform=='darwin' and platform.machine()=='arm64'
    outer=TEMP/'qualified-release-artifact.zip'
    assert digest(outer)==OUTER_SHA
    archive=TEMP/'rc23-qualified-runtime-input.zip'
    with zipfile.ZipFile(outer) as z:
        row=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        archive.write_bytes(z.read(row))
    assert digest(archive)==ARCHIVE_SHA
    SUBJECT.mkdir(exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        for row in z.infolist():
            if not (row.filename.startswith('Helene/runtime/') or row.filename=='Helene/verification-tools/bin/node'):continue
            path=Path(row.filename)
            assert not path.is_absolute() and '..' not in path.parts
            mode=(row.external_attr>>16)&0o177777
            assert not stat.S_ISLNK(mode)
            target=SUBJECT/path
            if row.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(z.read(row));target.chmod(mode&0o777 or 0o644)
    python=SUBJECT/'Helene/runtime/bin/python3'
    (OUT/'RUNTIME-INPUT-VERIFIED.json').write_text(json.dumps({'outer_sha256':OUTER_SHA,
        'archive_sha256':ARCHIVE_SHA,'runtime_and_node_extracted':True},indent=2)+'\n')
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'check'])

class Observed(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.rows=[]
    def addSuccess(self,test):super().addSuccess(test);self.rows.append({'id':test.id(),'status':'passed'})
    def addSkip(self,test,reason):super().addSkip(test,reason);self.rows.append({'id':test.id(),'status':'skipped','reason':reason})
    def addFailure(self,test,error):super().addFailure(test,error);self.rows.append({'id':test.id(),'status':'failed','error':self._exc_info_to_string(error,test)})
    def addError(self,test,error):super().addError(test,error);self.rows.append({'id':test.id(),'status':'error','error':self._exc_info_to_string(error,test)})
    def addSubTest(self,test,subtest,error):
        super().addSubTest(test,subtest,error)
        if error is not None:self.rows.append({'id':test.id(),'status':'failed-subtest','subtest':str(subtest),
                                              'error':self._exc_info_to_string(error,test)})

def check():
    assert sys.platform=='darwin' and platform.machine()=='arm64'
    sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/tests'),str(REPO/'desk/installer'),str(REPO/'helene/core')]
    os.environ.update(PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1')
    modules=['tests.t_update_runtime_status_0111','tests.t_source_update_hand_3010',
        'tests.t_update_activity_3010','tests.t_parallel_update_reads_0111','tests.t_source_work_event_0111',
        'tests.t_cocoa_js_bridge_0111','tests.t_restore_job_3110','tests.t_final_validation_health_3110',
        'tests.t_python_app.PythonApplicationTests.test_pending_mac_profile_migration_reaches_recovery_before_first_start',
        'tests.t_app_source_update_3110',
        'tests.t_source_update_pipeline_220.PipelineTests.test_selected_finish_automatically_verifies_reviews_and_reaches_apply_gate',
        'tests.t_posix_first_start_0111']
    repair_only=os.environ.get('RC23_REPAIR_ONLY')=='1'
    if repair_only:
        modules=['tests.t_restore_job_3110','tests.t_final_validation_health_3110',
                 'tests.t_upstream_recovery_3110','tests.t_portable_switch_3010']
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    def flatten(value):
        for case in value:
            if isinstance(case,unittest.TestSuite):yield from flatten(case)
            else:yield case
    ids=[case.id() for case in flatten(suite)]
    assert len(set(ids))==len(ids), 'Duplicate test aliases'
    started=time.monotonic()
    with (OUT/'python-tests.log').open('w') as stream:
        result=unittest.TextTestRunner(verbosity=2,resultclass=Observed,stream=stream).run(suite)
    report={'complete':True,'passed':result.wasSuccessful(),'tests':result.testsRun,'unique':len(set(ids)),
        'failed_case_ids':sorted({row['id'] for row in result.rows if row['status'] not in ('passed','skipped')}),
        'failure_assertions':len(result.failures),'errors':len(result.errors),'repair_only':repair_only,
        'outcomes':result.rows,'seconds':time.monotonic()-started,'python':sys.version,
        'platform':platform.platform(),'machine':platform.machine(),
        'runtime_donor_archive_sha256':ARCHIVE_SHA,
        'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'scope':'Source-level native Darwin unit/loopback fixtures. No new distributed RC23 assembly, GUI/TCC, semantic-review or broad-matrix acceptance.'}
    (OUT/'python-results.json').write_text(json.dumps(report,indent=2)+'\n')
    node=SUBJECT/'Helene/verification-tools/bin/node'
    if repair_only:
        (OUT/'node-result.json').write_text(json.dumps({'repeated':False,'retained_passed_run':41353011091,
            'reason':'Only three Python fixture paths and the native QA observer/workflow changed; shared UI/Node inputs unchanged.'},indent=2)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k!='outcomes'}),flush=True)
        assert result.wasSuccessful()
        return
    names=['phone-settings-dismiss-0111','phone-sheet-lifecycle-0211','release-notice-0111',
           'update-monitor-0111','work-activity-3010','panel-density-2910','panel-widths']
    with (OUT/'node-tests.log').open('wb') as output:
        done=subprocess.run([str(node),'--test',*[str(REPO/'desk/app/test'/(name+'.test.mjs')) for name in names]],
            cwd=REPO/'desk/app',stdout=output,stderr=subprocess.STDOUT,timeout=120)
    (OUT/'node-result.json').write_text(json.dumps({'passed':done.returncode==0,'exit':done.returncode,'files':names,
        'node_sha256':digest(node),'source_commit':report['source_commit']},indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='outcomes'}),flush=True)
    assert result.wasSuccessful() and done.returncode==0

if __name__=='__main__':globals()[sys.argv[1]]()
