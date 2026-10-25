"""Source-bound refresh of a verified public Python payload into a NEW directory.

The caller supplies the independently verified platform runtime. Shared immutable
files may be hardlinked; every changed byte is written to a new file first.
"""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys,zipfile
DESK=Path(__file__).resolve().parents[1]

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def refresh(donor,output,version,*,libraries=None,setup=None,downloads=None):
    donor,output=Path(donor).resolve(),Path(output).resolve()
    if output.exists() or donor.is_relative_to(output) or output.is_relative_to(donor):raise ValueError('Use a new separate output directory')
    if not all((donor/name).exists() for name in ['runtime','app/python_app.py','python-app.json']):raise ValueError('Donor must be the verified Helene payload root')
    def omit(directory,names):
        here=Path(directory);ignored={'__pycache__','.git','.webview','node_modules'}
        if here==donor:ignored|={'components','relay-product','helene-relay','helene-relay.exe','data','agents','backups','downloads','logs','owner-stop.json','python-migration.json'}
        if here==donor/'app':ignored|={'static','localharness','deskd'}
        return [n for n in names if n in ignored or n.endswith('.pyc') or n.startswith('.python-window-')]
    def link(source,target):
        try:return os.link(source,target)
        except OSError:return shutil.copy2(source,target)
    shutil.copytree(donor,output,copy_function=link,ignore=omit)
    def put(relative,data,mode=None):
        target=output/relative;target.parent.mkdir(parents=True,exist_ok=True)
        target.unlink(missing_ok=True);target.write_bytes(data)
        if mode is not None:target.chmod(mode)
    inputs={}
    def install(source,relative):
        put(relative,source.read_bytes(),source.stat().st_mode&0o777);inputs[str(source.relative_to(DESK)).replace('\\','/')]=digest(source)
    for name in ['python_app.py','python_migrate.py','python_tray.py','deskapp.py']:
        install(DESK/name,'app/'+name)
    for folder in ['localharness','deskd','resources']:
        for source in (DESK/folder).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix!='.pyc':install(source,'app/'+source.relative_to(DESK).as_posix())
    if not (DESK/'app/dist/index.html').is_file():raise ValueError('Build this source frontend first')
    for source in (DESK/'app/dist').rglob('*'):
        if source.is_file():install(source,'app/static/'+source.relative_to(DESK/'app/dist').as_posix())
    if libraries:
        libraries=Path(libraries)
        for folder in ['runtime/system-libs','THIRD-PARTY-LICENSES']:
            if not (output/folder).exists():shutil.copytree(libraries/folder,output/folder,copy_function=link)
        put('linux-libraries.json',(libraries/'linux-libraries.json').read_bytes())
    if setup:put('helene-setup.exe',Path(setup).read_bytes())
    if sys.platform=='darwin':
        for name in ['mac_migrate.py','resources/mac-migration.html']:install(DESK/name,'app/'+name)
        from python_mac_bundles import build
        # Signing replaces wrapper internals; detach them from the verified donor.
        for folder in ['Helene.app','Migrate Helene.app']:
            for source in list((output/folder).rglob('*')):
                if source.is_file():put(source.relative_to(output),source.read_bytes(),source.stat().st_mode&0o777)
        build(output,version)
    elif sys.platform.startswith('linux'):
        from linux_archive_entry import write
        for name in ['helene','Start Helene.sh','Helene.desktop','START-HERE.md']:(output/name).unlink(missing_ok=True)
        write(output)
    for name in ['python-app.json','helene-build.json','app/desk.json']:
        if (output/name).exists():
            value=json.loads((output/name).read_text(encoding='utf-8'));value['version']=version
            if name=='python-app.json':
                value.update(delivery='portable',local_candidate=True)
                value.get('extensions',{}).pop('toolchain',None)
                for extension in value.get('extensions',{}).values():extension['default']=False
                if 'native_inputs'in value:value['native_inputs']['relay']=False
            put(name,json.dumps(value,ensure_ascii=False,indent=2).encode())
    name='app/component-downloads.json'
    if downloads:put(name,Path(downloads).read_bytes())
    value=json.loads((output/name).read_text(encoding='utf-8'));value['app_version']=version
    for key in value.get('extensions',{}).pop('toolchain',[]):value.get('artifacts',{}).pop(key,None)
    value.get('unavailable',{}).pop('toolchain',None);put(name,json.dumps(value,indent=2).encode())
    put('components/components.json',json.dumps({'format':'helene-components.v1','components':{}}).encode())
    put('source-inputs.json',json.dumps({'version':version,'files':inputs},indent=2).encode())
    if (output/'app/desk.json').is_file():
        files={p.relative_to(output/'app').as_posix():digest(p) for p in (output/'app').rglob('*') if p.is_file() and p.name!='desk.json'}
        meta=json.loads((output/'app/desk.json').read_text(encoding='utf-8'));meta.update(files=files,digest=hashlib.sha256('\n'.join(f'{r} {h}' for r,h in sorted(files.items())).encode()).hexdigest());put('app/desk.json',json.dumps(meta,indent=2).encode())
    files={p.relative_to(output).as_posix():digest(p) for p in output.rglob('*') if p.is_file() and p.relative_to(output).parts[0] not in ('tree','components','data','agents','backups') and p.name not in ('helene.json','python-package-files.json')}
    put('python-package-files.json',json.dumps({'format':'helene-python-program-files.v1','version':version,'files':files},indent=2).encode())
    return output

def archive(root,target):
    with zipfile.ZipFile(target,'x',zipfile.ZIP_DEFLATED,compresslevel=3) as out:
        for path in sorted(Path(root).rglob('*')):
            if path.is_file():out.write(path,'Helene/'+path.relative_to(root).as_posix())
    Path(str(target)+'.sha256').write_text(digest(target)+'  '+Path(target).name+'\n',encoding='ascii')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('donor',type=Path);p.add_argument('output',type=Path);p.add_argument('--version',default='2.1.0');p.add_argument('--libraries',type=Path);p.add_argument('--setup',type=Path);p.add_argument('--archive',type=Path);a=p.parse_args()
    root=refresh(a.donor,a.output,a.version,libraries=a.libraries,setup=a.setup)
    if a.archive:archive(root,a.archive)
    print(json.dumps({'root':str(root),'archive':str(a.archive) if a.archive else None,'sha256':digest(a.archive) if a.archive else None}),flush=True)
