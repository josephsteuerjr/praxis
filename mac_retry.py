"""Qualify unchanged native ZIP; retry only failed rows on a case-sensitive fixture volume."""
from pathlib import Path
import argparse,hashlib,json,os,platform,re,shutil,subprocess,sys,tarfile,time
p=argparse.ArgumentParser();p.add_argument('--target',required=True);p.add_argument('--root',type=Path,required=True);p.add_argument('--prior',type=Path,required=True);p.add_argument('--finish',action='store_true');a=p.parse_args()
R=a.root.resolve();E=R/'equipment';S=R/'source';O=R/'retry-evidence';O.mkdir(exist_ok=True);W=R/'retry-work';W.mkdir(exist_ok=True)
arch='arm64' if a.target=='macos' else 'x86_64';assert sys.platform=='darwin' and platform.machine()==arch
def sha(path):
 with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
if not a.finish:
 with tarfile.open(a.prior) as z:
  for name in ('evidence/package/artifacts.json','evidence/native/regressions.json','evidence/package/Helene-2.6.0-macos-'+arch+'.zip'):
   f=z.extractfile(name);dest=W/Path(name).name;dest.write_bytes(f.read())
 archive=W/('Helene-2.6.0-macos-'+arch+'.zip');artifact=json.loads((W/'artifacts.json').read_bytes())['macos'];assert sha(archive)==artifact['sha256']
 sys.path[:0]=[str(S/'desk'),str(S/'desk/installer')]
 from build_python_macos_rc import unpack
 unpack(archive,W/'installed');python=W/'installed/Helene/runtime/bin/python3'
 os.execv(str(python),[str(python),'-B',__file__,'--target',a.target,'--root',str(R),'--prior',str(a.prior.resolve()),'--finish'])
program=W/'installed/Helene';proof=json.loads((E/'SOURCE-FROZEN.json').read_bytes())
for name,row in proof['files'].items():assert sha(S/name)==row['sha256'],name
sys.path[:0]=[str(S/'desk'),str(S/'desk/installer')]
from portable_sources import compose_core
edition=W/'edition';edition.mkdir()
for name,entry in compose_core(S)[0].items():
 dst=edition/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(entry.data);dst.chmod(int(entry.mode[-3:],8))
home=W/'test-home';tmp=home/'tmp';tmp.mkdir(parents=True)
# Confirm these test fixtures have distinct Windows spelling variants. Product ZIP is unchanged.
(tmp/'case').write_text('lower');(tmp/'CASE').write_text('upper');assert (tmp/'case').read_text()=='lower';(tmp/'case').unlink();(tmp/'CASE').unlink()
env=dict(os.environ,HOME=str(home),USERPROFILE=str(home),TMPDIR=str(tmp),TMP=str(tmp),TEMP=str(tmp),
 PYTHONPATH=os.pathsep.join(map(str,[S,S/'desk',S/'desk/localharness',S/'desk/tests',edition])),HELENE_TREE_SRC=str(edition),PYTHONDONTWRITEBYTECODE='1',PYTHONUTF8='1',NO_PROXY='127.0.0.1,localhost,::1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',PATH=os.pathsep.join([str(program/'runtime/bin'),str(program/'verification-tools/bin'),os.environ['PATH']]))
rows=json.loads((W/'regressions.json').read_bytes());retries=[]
for row in rows:
 if not row['exit']:continue
 name=row['module'];started=time.monotonic();log=O/(name+'.log')
 with log.open('wb') as f:r=subprocess.run([sys.executable,'-B',str(S/'desk/tests'/(name+'.py'))],cwd=S/'desk',env=env,stdout=f,stderr=subprocess.STDOUT,timeout=180,start_new_session=True)
 new={'module':name,'exit':r.returncode,'seconds':round(time.monotonic()-started,2),'cases':re.findall(r'Ran (\d+) tests?',log.read_text(encoding='utf8',errors='replace'))}
 retries.append({'original':dict(row),'retry':new});row.update(new)
 if name=='t_harness_journey_2910' and r.returncode:
  diagnostic=W/'journey-diagnostic';diagnostic.mkdir();script=S/'desk/tests/t_harness_journey_2910.py'
  code='import faulthandler,runpy,sys;faulthandler.dump_traceback_later(12,repeat=True);sys.argv='+repr([str(script),'--consumer',str(diagnostic),str(diagnostic/'observed.json')])+';runpy.run_path('+repr(str(script))+',run_name="__main__")'
  with (O/'journey-stack.log').open('wb') as f:
   proc=subprocess.Popen([sys.executable,'-B','-c',code],cwd=S/'desk',env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
   try:proc.wait(45)
   except subprocess.TimeoutExpired:
    import signal
    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
(O/'retries.json').write_text(json.dumps(retries,indent=2));(O/'regressions.json').write_text(json.dumps(rows,indent=2))
summary={'target':a.target,'failed':[r for r in rows if r['exit']],'retries':retries,'case_sensitive_fixture':True,'source_modified':False,'archive_rebuilt':False,'logs':{f.name:f.read_text(encoding='utf8',errors='replace')[-24000:] for f in O.glob('*.log')}}
(O/'summary.json').write_text(json.dumps(summary,indent=2))
assert not summary['failed'],'Read retry summary'
reuse={'platform':sys.platform,'source_commit':proof['commit'],'source_tree_sha256':proof['source_tree_sha256'],'regressions_sha256':sha(O/'regressions.json'),'prior_archive_sha256':sha(a.prior),'retry_details':retries}
(O/'regressions-proof.json').write_text(json.dumps(reuse,indent=2))
def run(name,command,timeout=2400):
 with (O/(name+'.log')).open('wb') as f:r=subprocess.run(list(map(str,command)),stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 assert r.returncode==0,'Read '+name+'.log'
run('codesign',['codesign','--verify','--deep','--strict',program/'Helene.app'],60)
run('wrapper-help',[program/'Helene.app/Contents/MacOS/Helene','--help'],60)
run('native-suite',[sys.executable,'-B',E/'posix_native_suite.py','--program',program,'--source',S,'--equipment',E,'--out',O/'native','--version','2.6.0','--prior-regressions',O/'regressions.json','--prior-regressions-proof',O/'regressions-proof.json'])
archive=W/('Helene-2.6.0-macos-'+arch+'.zip')
result={'passed':True,'target':a.target,'architecture':arch,'host':platform.mac_ver()[0],'source_commit':proof['commit'],'source_tree_sha256':proof['source_tree_sha256'],'archive':archive.name,'sha256':sha(archive),'archive_rebuilt':False,'case_sensitive_test_fixture':True,'native':json.loads((O/'native/native-final.json').read_bytes()),'notarized':False,'owner_installation_changed':False}
(O/'COMPLETE.json').write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='native'}))
