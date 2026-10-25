"""Broker-free DEB/RPM adapter over one qualified Python portable seed.

Packages own the immutable seed and desktop entry only. The first launch copies
real files into the user's writable XDG home; subsequent launches never reapply
that seed over configuration, memory, Git or a self-updated program. No maintainer
script starts services or deletes per-user state on removal.
"""
from pathlib import Path
import argparse
import json
import shutil
import subprocess
import tempfile
import re
import os
import errno
import stat
from python_posix_bundle import normalize_output_modes


def stage_file(source, target):
    """Immutable build staging shares bytes locally; cross-device builds copy."""
    # rpmbuild normalizes permissions in its hard-linked staging tree. Only
    # already-normalized files may share an inode with the immutable seed.
    if stat.S_IMODE(os.stat(source).st_mode) not in (0o644, 0o755):
        return shutil.copy2(source, target)
    try:
        os.link(source, target)
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
        shutil.copy2(source, target)
    return target


LAUNCHER = r'''#!/usr/lib/helene-python/seed/runtime/bin/python3
import fcntl,json,os,shutil,sys,uuid
from pathlib import Path
seed=Path('/usr/lib/helene-python/seed')
home=Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share')/'helene-python'
home.mkdir(parents=True,exist_ok=True)
target=home/'program'
with (home/'.seed.lock').open('a+b') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 if not target.exists():
  stage=home/('.seed-'+uuid.uuid4().hex)
  try:
   shutil.copytree(seed,stage,symlinks=False)
   (stage/'bootstrap-seed.json').write_text(json.dumps({'seed':str(seed),'version':json.loads((seed/'python-app.json').read_text())['version']}))
   stage.replace(target)
  except BaseException:
   if stage.exists():shutil.rmtree(stage)
   raise
 if not (target/'app/python_app.py').is_file() or not (target/'runtime/bin/python3').is_file():
  raise RuntimeError('Existing Hélène working folder is incomplete; retained without seed overwrite: '+str(target))
 if not os.access(target,os.W_OK) or not os.access(home,os.W_OK):
  raise PermissionError('Hélène program and update directory must be writable by this user')
python=target/'runtime/bin/python3'
os.execv(str(python),[str(python),'-s',str(target/'app/python_app.py'),'--root',str(target),*sys.argv[1:]])
'''
DEBIAN = ('libxcb-shape0','libxfixes3','libfontconfig1','ca-certificates','git','bubblewrap','procps','libstdc++6','libglib2.0-0','libegl1','libopengl0','libnss3','libasound2','libxcb-cursor0','libxcb-icccm4','libxcb-image0','libxcb-keysyms1','libxcb-render-util0','libxcb-xinerama0','libxcb-xkb1','libxkbcommon-x11-0','libdbus-1-3','libxdamage1','libxcomposite1','libxrandr2','libgbm1','xdotool','x11-utils','xclip','libxtst6','libatspi2.0-0','at-spi2-core','ffmpeg')
FEDORA = ('libXfixes','fontconfig','ca-certificates','git-core','bubblewrap','procps-ng','libstdc++','glib2','mesa-libEGL','libglvnd-opengl','nss','alsa-lib','xcb-util-cursor','xcb-util-wm','xcb-util-image','xcb-util-keysyms','xcb-util-renderutil','libxcb','libxkbcommon-x11','dbus-libs','libXdamage','libXcomposite','libXrandr','mesa-libgbm','xdotool','/usr/bin/xprop','/usr/bin/xdpyinfo','/usr/bin/xwininfo','xclip','libXtst','at-spi2-core')


def stage(seed,destination):
    seed,destination=Path(seed).resolve(),Path(destination)
    meta=json.loads((seed/'python-app.json').read_text(encoding='utf-8'))
    if meta.get('target')!='linux-x86_64':raise ValueError('Only an assembled Linux x86_64 seed can be packaged')
    if destination.exists():raise FileExistsError(destination)
    destination.mkdir(parents=True)
    shutil.copytree(seed,destination/'usr/lib/helene-python/seed',symlinks=False,copy_function=stage_file)
    normalize_output_modes(destination/'usr/lib/helene-python/seed')
    entry=destination/'usr/bin/helene';entry.parent.mkdir(parents=True);entry.write_text(LAUNCHER,encoding='utf-8');entry.chmod(0o755)
    desktop=destination/'usr/share/applications/helene-python.desktop';desktop.parent.mkdir(parents=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nName=Hélène\nComment=Python-owned agent desktop\nExec=/usr/bin/helene\nIcon=helene-python\nTerminal=false\nCategories=Utility;\n',encoding='utf-8')
    icon=destination/'usr/share/icons/hicolor/256x256/apps/helene-python.png';icon.parent.mkdir(parents=True)
    shutil.copy2(Path(__file__).resolve().parents[1]/'shell/icons/python-app.png',icon)
    license=destination/'usr/share/doc/helene-python';license.mkdir(parents=True)
    for name in ('LICENSE','NOTICE','THIRD-PARTY.md'):
        shutil.copy2(seed/name,license/name)
    normalize_output_modes(destination)
    return meta


def glibc_floor(seed):
    versions=set()
    for path in Path(seed).rglob('*'):
        if not path.is_file():continue
        with path.open('rb') as stream:elf=stream.read(4)==b'\x7fELF'
        if not elf:continue
        text=subprocess.check_output(['readelf','--version-info',str(path)],text=True)
        versions.update(tuple(map(int,m)) for m in re.findall(r'Name: GLIBC_(\d+)\.(\d+)',text))
    if not versions:raise ValueError('No measured GLIBC requirement in the Linux seed')
    return '.'.join(map(str,max(versions)))


def build(seed,output,*,formats=('deb','rpm')):
    if not formats or any(f not in ('deb','rpm') for f in formats) or len(set(formats))!=len(formats):
        raise ValueError('Expected distinct deb/rpm formats')
    seed,output=Path(seed).resolve(),Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    version=json.loads((seed/'python-app.json').read_text(encoding='utf-8'))['version']
    if not all(s.isdecimal() for s in version.split('.')) or len(version.split('.'))!=3:raise ValueError('Expected numeric package version')
    abi=glibc_floor(seed)
    with tempfile.TemporaryDirectory(prefix='helene-native-packages-') as temporary:
        work=Path(temporary);payload=work/'payload';stage(seed,payload)
        built=[]
        if 'deb' in formats:
            control=payload/'DEBIAN';control.mkdir()
            (control/'control').write_text('Package: helene-python\nVersion: '+version+'+local\nArchitecture: amd64\nMaintainer: Hélène local candidate\nDepends: '+', '.join(('libc6 (>= '+abi+')',*DEBIAN))+'\nDescription: Broker-free Python Hélène desktop local candidate\n Immutable bootstrap seed; writable per-user application and native updates.\n',encoding='utf-8')
            deb=output/('helene-python_'+version+'+local_amd64.deb')
            if deb.exists():raise FileExistsError(deb)
            subprocess.run(['dpkg-deb','--build','--root-owner-group','-Zzstd','-z3','--threads-max=1',str(payload),str(deb)],check=True)
            built.append(deb);shutil.rmtree(control)
        if 'rpm' not in formats:return built
        top=work/'rpm';(top/'SPECS').mkdir(parents=True)
        spec=top/'SPECS/helene-python.spec'
        spec.write_text('Name: helene-python\nVersion: '+version+'\nRelease: 0.local\nSummary: Broker-free Python Hélène desktop local candidate\nLicense: Apache-2.0 AND MIT AND BSD-3-Clause\nBuildArch: x86_64\nRequires: '+', '.join(('glibc >= '+abi,*FEDORA))+'\nAutoReqProv: no\n%global debug_package %{nil}\n%global __os_install_post %{nil}\n%description\nImmutable bootstrap seed with a writable per-user Python application.\n%prep\n%build\n%install\nmkdir -p %{buildroot}\ncp -al '+str(payload)+'/usr %{buildroot}/\n%files\n/usr/bin/helene\n/usr/lib/helene-python\n/usr/share/applications/helene-python.desktop\n/usr/share/icons/hicolor/256x256/apps/helene-python.png\n/usr/share/doc/helene-python\n',encoding='utf-8')
        subprocess.run(['rpmbuild','--define','_topdir '+str(top),'--define','_binary_payload w3.zstdio','-bb',str(spec)],check=True)
        rpms=list((top/'RPMS').rglob('*.rpm'))
        if len(rpms)!=1:raise ValueError('Expected exactly one RPM artifact')
        rpm=output/rpms[0].name
        if rpm.exists():raise FileExistsError(rpm)
        shutil.copy2(rpms[0],rpm)
        return [*built,rpm]


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--format',choices=('deb','rpm','both'),default='both')
    a=p.parse_args()
    for path in build(a.seed,a.output,formats=('deb','rpm') if a.format=='both' else (a.format,)):print(path,flush=True)
