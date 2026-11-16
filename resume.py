
from pathlib import Path
import hashlib,json,os,platform,shutil,subprocess,sys,tarfile
R=Path(os.environ['RUNNER_TEMP'])/'helene-case/qualification';E=R/'equipment';S=R/'source';O=R/'evidence';O.mkdir(parents=True,exist_ok=True)
def sha(p):return hashlib.file_digest(Path(p).open('rb'),'sha256').hexdigest()
prepared=json.loads(Path('PREPARED.json').read_bytes());assert sha('inputs.tar.gz')==prepared['archive_sha256']
if '--embedded' not in sys.argv:
 with tarfile.open('inputs.tar.gz') as z:z.extractall(R,filter='data')

prior=R/'prior'
if '--embedded' not in sys.argv:
 prior.mkdir()
 with tarfile.open('prior-artifact/native-macos-intel.tar.gz') as z:
  for m in z:
   if not m.isfile() or not m.name.startswith('evidence/'):continue
   q=Path(m.name.removeprefix('evidence/'));assert not q.is_absolute() and '..' not in q.parts
   keep=(q.parts[0]=='package' and len(q.parts)==2) or (q.parts[0]=='native' and (len(q.parts)==2 or q.parts[1] in ('native-gui','core-work')))
   if q.parts[:2]==('native','core-work'):keep=len(q.parts)==3 and q.suffix=='.json'
   if not keep:continue
   p=prior/q;p.parent.mkdir(parents=True,exist_ok=True)
   with z.extractfile(m) as src,p.open('wb') as dst:shutil.copyfileobj(src,dst)
 sys.path[:0]=[str(S/'desk'),str(S/'desk/installer')]
 from build_python_macos_rc import unpack
 archive=prior/'package/Helene-2.8.5-macos-x86_64.zip'
 unpack(archive,R/'work/installed');python=R/'work/installed/Helene/runtime/bin/python3'
 os.execv(str(python),[str(python),'-B',__file__,'--embedded'])
frozen=json.loads((E/'SOURCE-FROZEN.json').read_bytes())
program=R/'work/installed/Helene';archive=prior/'package/Helene-2.8.5-macos-x86_64.zip'
assert sys.platform=='darwin' and platform.machine()=='x86_64'
assert json.loads((program/'python-app.json').read_bytes())['version']=='2.8.5'
for name,row in frozen['files'].items():assert sha(S/name)==row['sha256'],name
rows=json.loads((prior/'native/regressions.json').read_bytes())
assert len(rows)==182 and not any(row['exit'] for row in rows)
proof={'platform':'darwin','source_commit':frozen['commit'],'source_tree_sha256':frozen['source_tree_sha256'],
 'regressions_sha256':sha(prior/'native/regressions.json'),'original_ci_run':43949032981,
 'qualification_source_inputs_sha256':prepared['archive_sha256'],'archive_sha256':sha(archive)}
(E/'prior-regressions-proof.json').write_text(json.dumps(proof,indent=2))
core=dict(proof,native_core_evidence={})
for key,name in {'copied_inputs':'copied-inputs.json','executor_lock':'executor-lock.json','modules':'modules.json','observation':'observation.json'}.items():
 p=prior/'native/core-work'/name;assert p.is_file()
 core['native_core_evidence'][key]={'path':'core-work/'+name,'sha256':sha(p)}
observed=json.loads((prior/'native/core-work/observation.json').read_bytes())
assert observed['complete'] and observed['successful'] and observed['coverage_complete']
(E/'prior-native-proof.json').write_text(json.dumps(core,indent=2))
for name in ('native_deadline_observer.py','posix_native_suite_resume.py','OBSERVER-PATCH.json'):shutil.copy2(name,E/name)
def run(name,args,timeout=900):
 with (O/(name+'.log')).open('wb') as log:r=subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
 assert r.returncode==0,'Read '+name+'.log'
run('codesign',['codesign','--verify','--deep','--strict',program/'Helene.app'],120)
run('native-suite',[sys.executable,'-B',E/'posix_native_suite_resume.py','--program',program,'--source',S,'--equipment',E,
 '--out',O/'native','--version','2.8.5','--prior-regressions',prior/'native/regressions.json',
 '--prior-regressions-proof',E/'prior-regressions-proof.json','--prior-native-evidence',prior/'native',
 '--prior-native-proof',E/'prior-native-proof.json'])
(O/'package').mkdir();shutil.copy2(archive,O/'package'/archive.name)
native=json.loads((O/'native/native-final.json').read_bytes());assert native['success']
native['external_deadline_observer']=json.loads((E/'OBSERVER-PATCH.json').read_bytes())
native['external_deadline_observer']['observer_sha256']=sha(E/'native_deadline_observer.py')
(O/'native/native-final.json').write_text(json.dumps(native,indent=2))
result={'passed':True,'platform':'macos-intel','architecture':'x86_64','host':platform.mac_ver()[0],
 'source_commit':frozen['commit'],'source_tree_sha256':frozen['source_tree_sha256'],'archive':archive.name,
 'sha256':sha(archive),'bytes':archive.stat().st_size,'native':native,'runtime_and_body_reused':True,
 'native_launcher_built_in_original_run':43949032981,'same_exact_archive_requalified':True,
 'notarized':False,'owner_installation_changed':False}
(O/'COMPLETE.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='native'}),flush=True)
