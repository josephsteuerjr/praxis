"""Independent old-public/candidate update benchmark on disposable synthetic state.

Run on the permitted server, not a new local Docker stand:
 python desk/tests/benchmark_update_2610.py --baseline /path/to/public-2.1.1 --candidate .

The baseline HEAD is pinned; each side is a fresh interpreter importing that
side's actual updater. No live app, model, download or owner configuration is
used. 'Quiescent switch' is NOT a measured real-window absence interval; a native
rendered-ready consumer must separately measure that product-level interval.
"""
from __future__ import annotations
import argparse
import ctypes
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

PUBLIC_BASE='2d9b9eb3428450c4b6fbadf90c8dc757e0a9d3ba'
BASE=b'def first():\n    return 1\n\n\n# independent region\n\ndef second():\n    return 1\n\n\ntitle = "original"\n'


def put(root,name,content):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content);return path


def sha(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):value.update(chunk)
    return value.hexdigest()


def tree(root):
    return {p.relative_to(root).as_posix():sha(p) for p in sorted(root.rglob('*')) if p.is_file()}


def process_reads():
    """Observed process I/O, cache reads included; children are explicitly excluded."""
    if os.name=='nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[(name,ctypes.c_ulonglong) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.GetCurrentProcess.restype=wintypes.HANDLE
        kernel.GetProcessIoCounters.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters)]
        value=Counters()
        if not kernel.GetProcessIoCounters(kernel.GetCurrentProcess(),ctypes.byref(value)):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'read_transfer_bytes':value.read_bytes}
    path=Path('/proc/self/io')
    if path.is_file():
        values=dict(line.split(': ',1) for line in path.read_text().splitlines())
        return {key:int(values[key]) for key in ('rchar','read_bytes')}
    return {}  # macOS: run the native benchmark under Instruments/fs_usage


@contextmanager
def measured(result,name):
    before=process_reads();at=time.monotonic()
    try:yield
    finally:
        after=process_reads()
        result[name]={'seconds':time.monotonic()-at,'process_io':{key:after[key]-before[key] for key in before}}


def worker(source,profile_mib,files):
    sys.path.insert(0,str(source/'desk'))
    from localharness import python_source_updater as updater
    from localharness import update_profile
    with tempfile.TemporaryDirectory(prefix='helene-update-benchmark-') as temporary:
        root=Path(temporary);old=root/'Helene';data=root/'profile';code=root/'authored'
        put(old,'tree/module.py',BASE);put(code,'module.py',BASE)
        # Actual Git/index/dirty/untracked preservation, not invented metadata.
        def git(*args):return subprocess.check_output(['git','-C',str(code),*args],stderr=subprocess.DEVNULL)
        subprocess.run(['git','init','-q',str(code)],check=True)
        git('config','core.autocrlf','false');git('add','.')
        git('-c','user.name=Fixture','-c','user.email=fixture@invalid.local','commit','-qm','baseline')
        head=git('rev-parse','HEAD')
        put(code,'staged.py',b'staged = True\n');git('add','staged.py')
        put(code,'draft.py',b'untracked = True\n')
        put(code,'module.py',BASE.replace(b'def second():\n    return 1',b'def second():\n    return 11'))
        index=git('diff','--cached','--binary')
        put(old,'python-app.json',b'{"version":"2.1.1","delivery":"portable"}')
        original_config=json.dumps({'tree':str(data),'code':str(code),'setup_complete':True}).encode()
        put(old,'helene.json',original_config)
        put(old,'run',b'echo original\n').chmod(0o640)
        # High entropy approximates nontrivial media compression; many small
        # records exercise traversal. None of these groups is update input.
        block=os.urandom(1024*1024)
        for group in ('memory/runs','memory/telegram-media'):
            for i in range(profile_mib):put(data,f'{group}/{i:04}.bin',block)
            for i in range(files):put(data,f'{group}/{i:05}.json',json.dumps({'n':i,'text':'retained '*64}).encode())
        put(data,'models/synthetic.bin',block)
        preserved=tree(data);original_source=tree(code)
        for i in range(64):put(old,f'runtime/lib/{i:04}.bin',block[:16384])
        results={'source':str(source),'profile_mib_per_group':profile_mib,'small_files_per_group':files,
                 'io_scope':'main process, including cache/pipe reads; subprocess Git I/O excluded',
                 'profile_bytes':sum(p.stat().st_size for p in data.rglob('*') if p.is_file()),
                 'phases':{},'backup_source_bytes':[],'backup_archive_bytes':[]}
        first_config=None;first_code=None
        for number in (1,2):
            incoming=root/('incoming-'+str(number));(incoming/'app').mkdir(parents=True)
            content=BASE.replace(b'def first():\n    return 1',b'def first():\n    return 2')
            if number==2:content=content.replace(b'"original"',b'"next release"')
            put(incoming,'tree/module.py',content)
            put(incoming,'python-app.json',json.dumps({'version':'2.2.'+str(number),'delivery':'portable'}).encode())
            put(incoming,'run',b'echo updated\n').chmod(0o755)
            for i in range(64):put(incoming,f'runtime/lib/{i:04}.bin',block[:16384] if i else b'new runtime')
            if hasattr(updater,'prepare_install'):
                from localharness.update_progress import Progress
                report=Progress(root/('.helene-update-benchmark-'+str(number)+'.progress.json'),'benchmark-'+str(number),old)
                with measured(results['phases'],f'{number}:preclose'):
                    updater.prepare_install(old,incoming,progress=report)
                with measured(results['phases'],f'{number}:quiescent_switch'):
                    note,recovery=updater.install(old,incoming)
                results.setdefault('receipt_stages',[]).append(json.loads(report.path.read_text())['stages'])
            else:
                # The old public consumer performs its preparation after the
                # owner closes. Time the whole actual install, not a surrogate.
                with measured(results['phases'],f'{number}:quiescent_switch'):
                    note,recovery=updater.install(old,incoming)
            with __import__('zipfile').ZipFile(note['backup']) as backup:
                results['backup_source_bytes'].append(sum(row.file_size for row in backup.infolist()))
            results['backup_archive_bytes'].append(Path(note['backup']).stat().st_size)
            updater.active_update(old).unlink()  # synthetic switch, no startup claim
            config=json.loads((old/'helene.json').read_text());chosen=Path(config['code'])
            assert config['tree']==str(data) and tree(data)==preserved
            assert tree(code)==original_source
            assert subprocess.check_output(['git','-C',str(chosen),'rev-parse','HEAD'])==head
            assert subprocess.check_output(['git','-C',str(chosen),'diff','--cached','--binary'])==index
            assert (chosen/'draft.py').read_bytes()==b'untracked = True\n'
            if os.name!='nt':assert (old/'run').stat().st_mode&0o777==0o755
            if number==1:
                first_config=(old/'helene.json').read_bytes();first_code=chosen
                module=chosen/'module.py';module.write_bytes(module.read_bytes().replace(b'return 11',b'return 111'))
                first_code_bytes=tree(chosen)
            else:
                assert b'return 111' in (chosen/'module.py').read_bytes()
                assert b'next release' in (chosen/'module.py').read_bytes()
                with measured(results['phases'],'rollback_second'):updater.rollback(old,recovery)
                assert (old/'helene.json').read_bytes()==first_config
                assert tree(first_code)==first_code_bytes and tree(data)==preserved
        results['two_updates_profile_git_modes_rollback']='passed'
        return results


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path)
    parser.add_argument('--candidate',type=Path,default=Path(__file__).resolve().parents[2])
    parser.add_argument('--profile-mib',type=int,default=8)
    parser.add_argument('--files',type=int,default=512)
    parser.add_argument('--worker',type=Path)
    args=parser.parse_args()
    if not 1<=args.profile_mib<=128 or not 1<=args.files<=5000:raise ValueError('Choose bounded synthetic profile sizes')
    if args.worker:
        print(json.dumps(worker(args.worker.resolve(),args.profile_mib,args.files),indent=2));return
    if not args.baseline:parser.error('--baseline must name the old public source checkout')
    head=subprocess.check_output(['git','-C',str(args.baseline),'rev-parse','HEAD']).decode().strip()
    if head!=PUBLIC_BASE:raise ValueError('Benchmark baseline is not the declared old public commit: '+head)
    if subprocess.check_output(['git','-C',str(args.baseline),'status','--porcelain']):raise ValueError('Baseline source must be pristine')
    rows={}
    for label,source in (('public_2_1_1',args.baseline),('candidate',args.candidate)):
        output=subprocess.check_output([sys.executable,str(Path(__file__).resolve()),'--worker',str(source.resolve()),'--profile-mib',str(args.profile_mib),'--files',str(args.files)])
        rows[label]=json.loads(output)
    print(json.dumps({'baseline_commit':head,'results':rows,
                      'native_window_interval':'not measured by this synthetic benchmark'},indent=2))


if __name__=='__main__':main()
