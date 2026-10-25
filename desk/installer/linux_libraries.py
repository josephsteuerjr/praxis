"""Bundle the measured GUI/library closure, excluding host glibc and drivers."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,re,shutil,subprocess

HOST={'libc.so.6','libm.so.6','libpthread.so.0','libdl.so.2','librt.so.1','libresolv.so.2',
      'libutil.so.1','ld-linux-x86-64.so.2','linux-vdso.so.1'}

def licenses(root,libraries,source_licenses=None):
    """Retain the distributor's exact copyright/license files with each DSO."""
    def license_file(name):
        p=Path(name)
        return p.is_file() and (name.startswith('/usr/share/licenses/') or (name.startswith('/usr/share/doc/') and any(part in p.name.upper() for part in ('COPYING','LICENSE','COPYRIGHT'))))
    destination=root/'THIRD-PARTY-LICENSES/system-libraries';packages={};missing=[]
    for row in libraries.values():
        source=Path(row['source'])
        if shutil.which('dpkg-query'):
            package=None
            for path in dict.fromkeys((str(source),str(source).removeprefix('/usr'))):
                found=subprocess.run(['dpkg-query','-S',path],capture_output=True,text=True)
                if found.returncode==0:package=found.stdout.split(': ',1)[0].strip();break
            if not package:raise RuntimeError('No package provenance for '+str(source))
            name=package.split(':')[0];files=[Path('/usr/share/doc')/name/'copyright']
            version=subprocess.check_output(['dpkg-query','-W','-f=${Version}',package],text=True).strip()
        else:
            package=subprocess.check_output(['rpm','-qf','--qf','%{NAME}',str(source)],text=True).strip();name=package
            version=subprocess.check_output(['rpm','-q','--qf','%{VERSION}-%{RELEASE}',package],text=True).strip()
            paths=subprocess.check_output(['rpm','-ql',package],text=True).splitlines()
            files=[Path(p) for p in paths if license_file(p)]
            if not files:
                # RPM subpackages may share license payloads with a sibling from
                # the exact same source RPM (for example libstdc++ and libgcc).
                source_rpm=subprocess.check_output(['rpm','-q','--qf','%{SOURCERPM}',package],text=True).strip()
                installed=subprocess.check_output(['rpm','-qa','--qf','%{NAME} %{SOURCERPM}\n'],text=True).splitlines()
                siblings=[line.split()[0] for line in installed if line.split()[-1]==source_rpm]
                for sibling in siblings:
                    paths=subprocess.check_output(['rpm','-ql',sibling],text=True).splitlines()
                    files.extend(Path(p) for p in paths if license_file(p))
                files=sorted(set(files))
                if files:row['license_source_rpm']=source_rpm

        if not files and source_licenses and not shutil.which('dpkg-query'):
            catalog=json.loads(Path(source_licenses).read_text())
            source_rpm=subprocess.check_output(['rpm','-q','--qf','%{SOURCERPM}',package],text=True).strip()
            source=catalog.get(source_rpm)
            if source:
                files=[Path(item['path']) for item in source['files']]
                for path,item in zip(files,source['files']):
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']:raise ValueError('Source license changed: '+str(path))
                row['license_source_rpm']=source_rpm
                row['license_source_rpm_sha256']=source['source_rpm_sha256']
        row['package']=package
        if package in packages:continue
        if not files or any(not p.is_file() for p in files):
            missing.append(package);continue
        saved=[]
        for index,path in enumerate(files):
            target=destination/name/(str(index)+'-'+path.name);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
            saved.append(target.relative_to(root).as_posix())
        packages[package]={'version':version,'license_files':saved}
    if missing:raise RuntimeError('No distributed license files for '+', '.join(sorted(set(missing))))
    return packages

def bundle(root,workers=12,source_licenses=None):
    root=Path(root).resolve();dest=root/'runtime/system-libs';dest.mkdir(exist_ok=True)
    binaries=[]
    for p in root.rglob('*'):
        if not p.is_file() or p.is_relative_to(dest):continue
        with p.open('rb') as f:
            if f.read(4)==b'\x7fELF':binaries.append(p)
    def linked(p):
        r=subprocess.run(['ldd',str(p)],capture_output=True,text=True,timeout=30)
        return p,r.stdout+r.stderr
    libraries={};missing=[];optional=[]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for binary,text in pool.map(linked,binaries):
            for line in text.splitlines():
                if '=> not found' in line:
                    relative=binary.relative_to(root).as_posix()
                    unused=any(part in relative for part in ('/sqldrivers/','/designer/','/QtWayland/Compositor/','/egldeviceintegrations/','/texttospeech/','QtNfc.','libQt6Nfc.','/_tkinter.'))
                    (optional if unused else missing).append({'binary':relative,'dependency':line.strip()})
                match=re.search(r'=> (/\S+) \(',line)
                if not match:continue
                source=Path(match[1]).resolve()
                name=line.strip().split()[0]
                if name in HOST or source.is_relative_to(root):continue
                # GL/EGL loaders are portable; actual DRI/Vulkan driver modules
                # remain tied to the host kernel and GPU and are never shipped.
                if '/dri/' in str(source) or '/vulkan/' in str(source):continue
                with source.open('rb') as f:sha=hashlib.file_digest(f,'sha256').hexdigest()
                if name in libraries and libraries[name]['sha256']!=sha:raise ValueError('Library basename collision: '+name)
                libraries[name]={'source':str(source),'sha256':sha}
    if missing:raise RuntimeError('Unresolved packaged libraries: '+json.dumps(missing))
    for name,row in libraries.items():
        target=dest/name
        shutil.copy2(row['source'],target);target.chmod(0o644)
    receipt={'format':'helene-linux-library-closure.v1','elf_files':len(binaries),'libraries':libraries,'host_libraries':sorted(HOST),'unused_optional_plugins':optional,'packages':licenses(root,libraries,source_licenses)}
    (root/'linux-libraries.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt

if __name__=='__main__':
    import sys
    result=bundle(Path(sys.argv[1]));print(json.dumps({'libraries':len(result['libraries']),'elf_files':result['elf_files']}))
