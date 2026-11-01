"""Exact archive on another native Mac OS, with no ambient Python packages."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,zipfile
REPO=Path(__file__).resolve().parents[2];TEMP=Path(os.environ['RUNNER_TEMP']).resolve()
OUT=REPO/'evidence-distribution';OUT.mkdir(exist_ok=True)
sys.path[:0]=[str(REPO/'desk/installer'),str(REPO/'desk/tests'),str(REPO/'desk')]
from build_python_macos_rc import unpack

def digest(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def command(args,name,**kwargs):
    with (OUT/name).open('wb') as output:
        p=subprocess.run(list(map(str,args)),stdout=output,stderr=subprocess.STDOUT,timeout=600,start_new_session=True,**kwargs)
    return p.returncode
def prepare():
    artifact=TEMP/'qualified-release-artifact.zip'
    assert digest(artifact)==os.environ['ARTIFACT_SHA256'],'Artifact transport identity differs'
    archive=TEMP/'qualified-release.zip'
    with zipfile.ZipFile(artifact) as z:
        matches=[i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip']
        assert len(matches)==1
        archive.write_bytes(z.read(matches[0]))
    assert digest(archive)==os.environ['CANDIDATE_SHA256'],'Native package differs'
    unpack(archive,TEMP/'matrix-package')
    program=TEMP/'matrix-package/Helene'
    # The subject moves its program folder. Keep the observer's interpreter
    # outside it so late stdlib imports do not resolve through a vanished path.
    driver=TEMP/'matrix-driver-runtime';shutil.copytree(program/'runtime',driver)
    python=driver/'bin/python3'
    os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'subject'])
def subject():
    program=TEMP/'matrix-package/Helene';python=program/'runtime/bin/python3'
    if os.environ.get('MATRIX_RECHECK')=='post-move':
        # Original run41296230218 already passed ALL10 source modules, body8,
        # native first-start/relay/exit/login/move AND isolated imports on each
        # OS. Only its final tiny rollback observer lost its moving stdlib.
        moved=program.parent/'Moved Hélène';program.rename(moved)
        sys.path.insert(0,str(moved/'app'))
        from localharness import python_source_updater as updater
        old=TEMP/'rollback-old';new=TEMP/'rollback-new';recovery=TEMP/'rollback-recovery'
        old.mkdir();new.mkdir();(old/'file.py').write_text('previous\n');(new/'file.py').write_text('incoming\n')
        updater.apply_files(new,old,recovery);assert (old/'file.py').read_text()=='incoming\n'
        (old/'file.py').write_text('later owner edit\n');updater.rollback(old,recovery)
        assert (old/'file.py').read_text()=='previous\n'
        assert (recovery/'later-self-edits/file.py').read_text()=='later owner edit\n'
        assert command(['xattr','-w','com.apple.quarantine','0083;00000000;HeleneRCQualification;',moved/'Helene.app'],'quarantine-set.log')==0
        assessed=command(['spctl','--assess','--type','execute','--verbose=4',moved/'Helene.app'],'quarantined-gatekeeper.log')
        (OUT/'TAIL-QUALIFIED.json').write_text(json.dumps({'archive_sha256':os.environ['CANDIDATE_SHA256'],
            'previous_native_and_imports_run':41296230218,'sw_vers':subprocess.check_output(['sw_vers'],text=True),
            'actual_post_move_rollback':True,'later_edit_retained':True,'quarantine_assessment_exit':assessed,
            'scope':'Only failed observer tail rechecked; join previous exact native/import evidence, not whole failed run as green'},indent=2))
        return
    # Exercise actual imports with PATH restricted to OS tools. Host Python,
    # Homebrew, npm, user site and build-machine PYTHONPATH cannot fill gaps.
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME','VIRTUAL_ENV','DYLD_LIBRARY_PATH','SSL_CERT_FILE','SSL_CERT_DIR')}
    env.update(PATH='/usr/bin:/bin:/usr/sbin:/sbin',PYTHONNOUSERSITE='1',PYTHONDONTWRITEBYTECODE='1',DYLD_PRINT_LIBRARIES='1')
    code="""import sys,json,importlib,ssl;from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path[:0]=[str(root/'app'),str(root/'tree')]
names=('python_app','webview','Quartz','Cocoa','WebKit','anthropic','openai','httpx','aiohttp','telethon','paramiko','cryptography','psutil','yaml','socks','pytest','aiogram','soundfile')
rows={}
for name in names:
 m=importlib.import_module(name);p=Path(m.__file__).resolve() if getattr(m,'__file__',None) else None
 if p is not None:assert p.is_relative_to(root),(name,str(p))
 rows[name]=str(p)
from localharness.tls_context import default_context
ctx=default_context();assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED
assert ctx.get_ca_certs(),'No usable TLS roots'
print(json.dumps({'imports':rows,'python':sys.version,'path':sys.path,'trusted_roots':len(ctx.get_ca_certs())},indent=2))
"""
    assert command([python,'-I','-B','-c',code,program],'isolated-runtime-imports.log',env=env)==0
    loaded=(OUT/'isolated-runtime-imports.log').read_text(errors='replace')
    for forbidden in ('/opt/homebrew/','/usr/local/','/Library/Frameworks/Python'):
        assert forbidden not in loaded,'Ambient native library/package path: '+forbidden
    assert command(['codesign','--verify','--deep','--strict',program/'Helene.app'],'codesign.log')==0
    binaries=[program/'Helene.app/Contents/MacOS/Helene',program/'helene-body',program/'helene-bridge',python,program/'verification-tools/bin/node']
    for number,p in enumerate(binaries):
        assert command(['file',p],f'binary-{number}.log')==0
        assert 'arm64' in (OUT/f'binary-{number}.log').read_text()
        assert command(['otool','-L',p],f'dylibs-{number}.log')==0
        command(['vtool','-show-build',p],f'minimum-os-{number}.log')
    # These are observations of an unsigned-distribution policy, never an
    # attempt to disable Gatekeeper or change an owner's global trust settings.
    assessment=command(['spctl','--assess','--type','execute','--verbose=4',program/'Helene.app'],'gatekeeper.log')
    receipt={'archive_sha256':os.environ['CANDIDATE_SHA256'],'sw_vers':subprocess.check_output(['sw_vers'],text=True),
        'architecture':os.uname().machine,'cpus':os.cpu_count(),'isolated_runtime_imports':True,
        'codesign_valid':True,'gatekeeper_assessment_exit':assessment,'notarized':False,
        'physical_owner_acceptance':False}
    assert os.uname().machine=='arm64'
    (OUT/'DISTRIBUTION.json').write_text(json.dumps(receipt,indent=2))
    import rc18_macos_pipeline as native
    native.PROGRAM=program
    native.native()
    # Native consumer moved its own complete folder. Quarantine only that
    # disposable copy after its tests; the original downloaded ZIP is intact.
    moved=program.parent/'Moved Hélène'
    command(['xattr','-w','com.apple.quarantine','0083;00000000;HeleneRCQualification;',moved/'Helene.app'],'quarantine-set.log')
    command(['spctl','--assess','--type','execute','--verbose=4',moved/'Helene.app'],'quarantined-gatekeeper.log')

if __name__=='__main__':{'prepare':prepare,'subject':subject}[sys.argv[1]]()
