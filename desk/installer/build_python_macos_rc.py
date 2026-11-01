"""Native Python macOS candidate from a hash-bound reusable runtime input.

No legacy build_mac invocation, compiler toolchain, system service or release
mutation. The small Cocoa interpreter launcher is reused as an OS organ; all
application/agent code remains editable Python in the portable program folder.
"""
from pathlib import Path
import argparse,hashlib,json,os,plistlib,shutil,stat,subprocess,sys,zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from portable_sources import compose_core
from python_posix_bundle import assemble,normalize_output_modes
from localharness import app_components
from localharness.python_platform import runtime_target

VERSION='2.3.0-rc.18'
# Last successful native Python distribution from public CI 40233694345.
EVIDENCE_SHA256='6eb05a4b19951fa9cc27caaca36de0024a5e73fcb5982503798f98959ddc1b91'

def digest(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def unpack(archive,destination):
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names=set()
        for i in z.infolist():
            p=Path(i.filename)
            if p.is_absolute() or '..' in p.parts or i.filename in names:raise ValueError('Unsafe or duplicate input path')
            names.add(i.filename)
            mode=(i.external_attr>>16)&0o177777
            if stat.S_ISLNK(mode):raise ValueError('Runtime input contains a symlink')
            target=destination/p
            if i.is_dir():target.mkdir(parents=True,exist_ok=True);continue
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(i));target.chmod(mode&0o777 or 0o644)

def build(evidence,work,out):
    if runtime_target()!='macos-arm64':raise ValueError('Run on native macOS ARM64')
    evidence,work,out=map(lambda p:Path(p).resolve(),(evidence,work,out))
    if digest(evidence)!=EVIDENCE_SHA256:raise ValueError('Reusable CI input digest mismatch')
    work.mkdir(parents=True,exist_ok=False);out.mkdir(parents=True,exist_ok=False)
    with zipfile.ZipFile(evidence) as z:
        payload=work/'previous.zip';payload.write_bytes(z.read('Helene-2.1.0-macos-arm64.zip'))
    donor=work/'donor';unpack(payload,donor);donor=donor/'Helene'
    repo=Path(__file__).resolve().parents[2]
    core,origins=compose_core(repo);edition=work/'edition';edition.mkdir()
    for name,e in core.items():
        p=edition/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(e.data);p.chmod(0o755 if e.mode=='100755' else 0o644)
    downloads=work/'component-downloads.json'
    value=json.loads((donor/'app/component-downloads.json').read_text());value['app_version']=VERSION
    downloads.write_text(json.dumps(value,indent=2),encoding='utf-8')
    program=out/'Helene'
    # assemble owns an initial archive. Final GUI metadata is sealed below;
    # keep only the final archive name and the source-bound final passport.
    first=assemble(donor/'runtime',edition,None,None,program,version=VERSION,target='macos-arm64',
        downloads=downloads,body=donor/'helene-body',bridge=donor/'helene-bridge')
    first.rename(work/'assembly-before-cocoa.zip')
    bundle=program/'Helene.app'
    shutil.copytree(donor/'Helene.app',bundle)
    if not (donor/'licenses').is_dir():raise ValueError('Reusable native input is missing third-party license texts')
    shutil.copytree(donor/'licenses',program/'licenses')
    info=bundle/'Contents/Info.plist';value=plistlib.loads(info.read_bytes())
    value.update(CFBundleShortVersionString='2.3.0',CFBundleVersion='23018')
    info.write_bytes(plistlib.dumps(value))
    subprocess.run(['codesign','--force','--sign','-',str(bundle)],check=True)
    subprocess.run(['codesign','--verify','--deep','--strict',str(bundle)],check=True)
    launcher='#!/bin/sh\nset -eu\nhere=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec "$here/Helene.app/Contents/MacOS/Helene" --root "$here" "$@"\n'
    for name in ('helene','Start Helene.command'):
        p=program/name;p.write_text(launcher,encoding='utf-8');p.chmod(0o755)
    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    native={'runtime_donor_sha256':digest(payload),'evidence_sha256':EVIDENCE_SHA256,'run_id':40233694345,
            'launcher_sha256':digest(bundle/'Contents/MacOS/Helene'),
            'body_sha256':digest(program/'helene-body'),'bridge_sha256':digest(program/'helene-bridge')}
    (program/'macos-inputs.json').write_text(json.dumps({'format':'helene-macos-inputs.v1','version':VERSION,
        'source_commit':source_commit,'base_public_commit':'f7b2434965bfc8a4da4e85d1e90811a3f3051199',
        'native_reused':native,'core_origins':origins,'native_qualification':'see separate CI receipts',
        'physical_acceptance':'TCC/microphone/owner Mac pending'},indent=2),encoding='utf-8')
    normalize_output_modes(program)
    files={p.relative_to(program).as_posix():digest(p) for p in program.rglob('*') if p.is_file()
        and p.relative_to(program).parts[0] not in ('tree','components','data','downloads') and p.name not in ('helene.json','python-package-files.json')}
    (program/'python-package-files.json').write_text(json.dumps({'format':'helene-python-program-files.v1','version':VERSION,'files':files},indent=2),encoding='utf-8')
    final=out/f'Helene-{VERSION}-macos-arm64.zip'
    with zipfile.ZipFile(final,'x',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(program.rglob('*')):
            if p.is_file():z.write(p,'Helene/'+p.relative_to(program).as_posix())
    receipt={'format':'helene-macos-candidate.v1','version':VERSION,'target':runtime_target(),
             'source_commit':source_commit,'archive':final.name,'size':final.stat().st_size,'sha256':digest(final),
             'files':len(files),'native_reused':native}
    (out/'candidate.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    (out/(final.name+'.sha256')).write_text(receipt['sha256']+'  '+final.name+'\n',encoding='utf-8')
    print(json.dumps(receipt),flush=True)
    return program

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('evidence','work','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();build(a.evidence,a.work,a.output)
