"""Requalify only failed source fixtures against an unchanged native candidate."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,subprocess,sys,zipfile
repo=Path(__file__).resolve().parents[2];sys.path.insert(0,str(repo/'desk/installer'))
from build_python_macos_rc import unpack
temp=Path(os.environ['RUNNER_TEMP']).resolve();evidence=repo/'evidence-requalified';evidence.mkdir()
archive=temp/'rc18-qualified-input.zip'
with archive.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()=='91d198b1b226ed2c630c57c6602cab963f45f7a678eee314bb06ae32c7dfcf58'
with zipfile.ZipFile(archive) as z:
    for i in z.infolist():
        if i.is_dir():continue
        name=Path(i.filename).name
        if name in ('candidate.json','native.json','01-first-start.png','02-agent-window.png','03-moved-window.png','body-contract.log') or name.startswith('t_') and name.endswith('.log'):
            (evidence/name).write_bytes(z.read(i))
        if name=='Helene-2.3.0-rc.18-macos-arm64.zip':
            candidate=temp/name;candidate.write_bytes(z.read(i))
receipt=json.loads((evidence/'candidate.json').read_text())
with candidate.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==receipt['sha256']=='79ce64094f84499c1225feeb498b5310131d9122dfa37f2766279d8d9de66255'
program=temp/'verified/Helene';unpack(candidate,program.parent)
assert sys.platform=='darwin' and os.uname().machine=='arm64'
modules=('t_update_modes_229','t_first_start_relay_3110')
def test(name):
    home=temp/('recheck-'+name);home.mkdir()
    with (evidence/(name+'.log')).open('wb') as log:
        p=subprocess.run([str(program/'runtime/bin/python3'),'-B','-m','unittest','tests.'+name,'-v'],
            cwd=repo/'desk',env=dict(os.environ,HOME=str(home),CFFIXED_USER_HOME=str(home),PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1'),
            stdout=log,stderr=subprocess.STDOUT,timeout=300,start_new_session=True)
    return name,p.returncode
with ThreadPoolExecutor(max_workers=os.cpu_count() or 3) as pool:tests=dict(pool.map(test,modules))
prior=json.loads((evidence/'native.json').read_text())
prior['focused_tests']['t_python_native_update_2410']=0
for name in prior['focused_tests']:
    if name not in tests:assert prior['focused_tests'][name]==0,name
prior['focused_tests'].update(tests)
prior.pop('error',None)
assert prior['body_contract']==0 and all(prior['steps'].values())
result={'format':'helene-macos-requalification.v1','candidate':receipt,'prior_native_run':41290729731,'native_update_pass_run':41291031559,
    'candidate_rebuilt':False,'qualification_source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
    'changes':'Two test fixtures now pass canonical input paths, matching production _main. No application/runtime/core code changed.',
    'rerun_only':tests,'native':prior,'passed':not any(prior['focused_tests'].values()),
    'physical_owner_Mac_TCC_microphone':'not accepted'}
(evidence/'QUALIFIED.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({'passed':result['passed'],'candidate_sha256':receipt['sha256'],'rerun_only':tests,'candidate_rebuilt':False}),flush=True)
assert result['passed'],'Focused fixture requalification failed'
