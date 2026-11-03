"""Complete locked native frontend/Node qualification with reusable owned tools."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import hashlib,json,os,shutil,subprocess,sys
REPO=Path(__file__).resolve().parents[2]
TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-node';OUT.mkdir(exist_ok=True)
TOOLS=TEMP/'rc18-verification-tools'

def run(argv,name,cwd=REPO,timeout=900):
    with (OUT/name).open('wb') as f:
        p=subprocess.run(list(map(str,argv)),cwd=cwd,stdout=f,stderr=subprocess.STDOUT,timeout=timeout,start_new_session=True)
    return p.returncode

def main():
    sys.path[:0]=[str(REPO/'desk'),str(REPO/'desk/tests')]
    from localharness.source_verification_bundle import inventory
    from rc18_macos_pipeline import fronts
    from extended_macos import prepare
    program,python=prepare()
    os.environ['HELENE_TEST_PYTHON']=str(python)
    fronts()
    TOOLS.mkdir();(TOOLS/'bin').mkdir()
    shutil.copy2(shutil.which('node'),TOOLS/'bin/node')
    npm=Path(shutil.which('npm')).resolve().parents[1]
    shutil.copytree(npm,TOOLS/'npm',symlinks=False,ignore=shutil.ignore_patterns('node_modules/.cache'))
    launcher=TOOLS/'bin/npm'
    launcher.write_text('#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$here/node" "$here/../npm/bin/npm-cli.js" "$@"\n')
    launcher.chmod(0o755)
    node_license=Path(shutil.which('node')).resolve().parent.parent/'LICENSE'
    if node_license.is_file():shutil.copy2(node_license,TOOLS/'NODE-LICENSE')
    names=('app','mobile','miniapp','remote','setup/ui')
    def install(name):return name,run([TOOLS/'bin/npm','ci','--prefix',REPO/'desk'/name,'--cache',TOOLS/'cache','--audit=false','--fund=false'],name.replace('/','-')+'-ci.log')
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as pool:installed=dict(pool.map(install,names))
    assert not any(installed.values()),installed
    files=sorted((REPO/'desk/app/test').glob('*.mjs'))
    code=run([TOOLS/'bin/node','--test','--test-concurrency='+str(os.cpu_count()),*files],'all-node-tests.log',timeout=1800)
    # Logs are diagnostics, never executor/cache material copied to a user.
    for name in ('_logs','_update-notifier-last-checked'):
        p=TOOLS/'cache'/name
        if p.is_dir():shutil.rmtree(p)
        elif p.exists():p.unlink()
    receipt={'source':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'node':subprocess.check_output([TOOLS/'bin/node','--version'],text=True).strip(),
        'npm':subprocess.check_output([TOOLS/'bin/npm','--version'],text=True).strip(),
        'installed':installed,'node_exit':code,'node_modules':len(files),'files':inventory(TOOLS)}
    (OUT/'NODE.json').write_text(json.dumps(receipt,indent=2))
    (TOOLS/'qualification.json').write_text(json.dumps({k:v for k,v in receipt.items() if k!='files'},indent=2))
    assert code==0,'Full Node source gate failed; retain actual log'

if __name__=='__main__':main()
