"""Build native hands from the selected edition, not the production mirror.

The relay has its own exact source input; it is not inferred from edition/relay
because retained agent editions may contain an older interactive-only relay.
Run on the target OS. Compiler dependencies belong to the build environment.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys

def sha(path):
 with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def build(edition,relay_source,output,*,target_dir,jobs=2):
 edition,relay_source,output,target_dir=map(lambda p:Path(p).resolve(),(edition,relay_source,output,target_dir))
 if sys.platform not in ('linux','darwin'):raise ValueError('Native POSIX build requires Linux or macOS')
 if not 1<=jobs<=64:raise ValueError('jobs must be between 1 and 64')
 body=edition/'body'
 for source in (body,relay_source):
  for name in ('Cargo.toml','Cargo.lock'):
   if not (source/name).is_file():raise FileNotFoundError(source/name)
 if output.exists():raise FileExistsError(output)
 output.mkdir(parents=True);target_dir.mkdir(parents=True,exist_ok=True)
 env={**os.environ,'CARGO_TARGET_DIR':str(target_dir)}
 builds=[(body,['-p','praxis-body','-p','praxis-bridge'],{'praxis-body':'helene-body','praxis-bridge':'helene-bridge'}),
         (relay_source,['--bin','praxis-relay-headless'],{'praxis-relay-headless':'helene-relay'})]
 rows=[]
 for source,args,names in builds:
  # Hash exact compilation inputs before build; the receipt also observes any
  # changes during compilation rather than silently calling moved input exact.
  def inputs():return {p.relative_to(source).as_posix():sha(p) for p in sorted(source.rglob('*')) if p.is_file() and not any(x in ('.git','target','__pycache__') for x in p.relative_to(source).parts)}
  before=inputs()
  subprocess.run(['cargo','build','--locked','--release','-j',str(jobs),*args],cwd=source,env=env,check=True)
  if inputs()!=before:raise RuntimeError('Native source moved during build: '+str(source))
  binaries={}
  for built,name in names.items():
   dest=output/name;shutil.copy2(target_dir/'release'/built,dest);dest.chmod(0o755);binaries[name]=sha(dest)
  rows.append({'source':str(source),'files':before,'binaries':binaries})
 receipt={'format':'helene-posix-native-build.v1','platform':sys.platform,'selected_edition':str(edition),'builds':rows}
 (output/'native-build.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
 return receipt

if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__)
 for name in ('edition','relay-source','output','target-dir'):p.add_argument('--'+name,type=Path,required=True)
 p.add_argument('--jobs',type=int,default=2);a=p.parse_args()
 build(a.edition,a.relay_source,a.output,target_dir=a.target_dir,jobs=a.jobs)
