"""Native Rust protocol/body/bridge unit gate; no shell, tray or system service."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-native-source';OUT.mkdir(exist_ok=True)
body=TEMP/'rc18-native-source'
assert os.uname().machine=='arm64'
for source in (REPO/'praxis/body',REPO/'helene/core/body'):
    shutil.copytree(source,body,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git','target','__pycache__'))
def files():return {p.relative_to(body).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(body.rglob('*')) if p.is_file()}
before=files()
env=dict(os.environ,MACOSX_DEPLOYMENT_TARGET='14.0',CARGO_TARGET_DIR=str(TEMP/'rc18-body-target'),RUSTUP_TOOLCHAIN='1.94.0')
with (OUT/'rust-version.log').open('wb') as log:subprocess.run(['rustc','-vV'],env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
with (OUT/'native-unit.log').open('wb') as log:
    result=subprocess.run(['cargo','test','--locked','--manifest-path',str(body/'Cargo.toml'),'-p','praxis-body-protocol','-p','praxis-body','-p','praxis-bridge',
        '-j',str(os.cpu_count()),'--','--test-threads='+str(os.cpu_count())],env=env,cwd=body,stdout=log,stderr=subprocess.STDOUT,timeout=2100)
after=files();assert before==after,'Native source changed during test compilation'
record={'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
    'platform':os.uname().machine,'cpus':os.cpu_count(),'compiler':'1.94.0','source_files':before,
    'packages':['praxis-body-protocol','praxis-body','praxis-bridge'],'exit':result.returncode,
    'scope':'Native source test executables only. Existing release body/bridge bytes not replaced, no legacy shell/tray/service build.'}
(OUT/'NATIVE-SOURCE.json').write_text(json.dumps(record,indent=2))
assert result.returncode==0,'Native source unit gate failed; inspect complete log'
with (OUT/'native-release-build.log').open('wb') as log:
    subprocess.run(['cargo','build','--release','--locked','--manifest-path',str(body/'Cargo.toml'),
        '-p','praxis-body','-p','praxis-bridge','-j',str(os.cpu_count())],env=env,cwd=body,
        stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1800)
organ=OUT/'organs';organ.mkdir()
for package in ('body','bridge'):
    binary=Path(env['CARGO_TARGET_DIR'])/'release'/('praxis-'+package)
    target=organ/('helene-'+package);shutil.copy2(binary,target)
    subprocess.run(['codesign','--force','--sign','-',str(target)],check=True)
    subprocess.run(['codesign','--verify','--strict',str(target)],check=True)
    record[package+'_sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
record['scope']='Source-tested, signed standalone body/bridge only. No shell/tray/system service build. Actual release package must separately exercise these exact binaries.'
(OUT/'NATIVE-SOURCE.json').write_text(json.dumps(record,indent=2))
