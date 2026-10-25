"""Rebuild the packaged headless relay offline with the optional Linux kit.

No rustup, pip, system compiler or network fetch. The output is a candidate,
never a replacement of the installation's running executable. Linux x86_64,
glibc >= 2.36; the kit carries Rust, Zig's C/linker/sysroot and static OpenSSL.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def rebuild(kit, source, output, *, jobs=2):
    kit,source,output=(Path(p).resolve() for p in (kit,source,output))
    if sys.platform!='linux' or platform.machine().lower() not in ('x86_64','amd64'):
        raise ValueError('This toolchain requires Linux x86_64')
    note=json.loads((kit/'toolchain.json').read_text(encoding='utf-8'))
    if note.get('format')!='helene-linux-toolchain.v1' or note.get('target')!='x86_64-unknown-linux-gnu':
        raise ValueError('Wrong native toolchain target')
    if type(jobs) is not int or not 1<=jobs<=8:raise ValueError('Expected 1..8 build jobs')
    if not (source/'Cargo.lock').is_file():raise ValueError('Packaged locked source required')
    if output.exists():raise FileExistsError('Choose a new output; earlier build retained')
    if output.is_relative_to(kit) or output.is_relative_to(source):
        raise ValueError('Build output must be outside immutable kit and source')
    output.mkdir(parents=True)
    home=output/'home';home.mkdir()
    target='x86_64-unknown-linux-gnu'
    rustc=kit/'rust/bin/rustc';cargo=kit/'rust/bin/cargo'
    cc=kit/'bin/cc';ar=kit/'bin/ar'
    for path in (rustc,cargo,cc,ar):
        if not path.is_file() or not os.access(path,os.X_OK):raise ValueError('Missing executable: '+str(path))
    env={'PATH':str(kit/'bin')+os.pathsep+str(kit/'rust/bin')+':/usr/bin:/bin',
         'HOME':str(home),'CARGO_HOME':str(home/'cargo'),'RUSTUP_HOME':str(home/'rustup'),
         'RUSTC':str(rustc),'CARGO_NET_OFFLINE':'true','CARGO_INCREMENTAL':'0',
         'CARGO_TARGET_DIR':str(output/'target'),'ZIG_GLOBAL_CACHE_DIR':str(home/'zig'),
         'CC':str(cc),'AR':str(ar),'CC_x86_64_unknown_linux_gnu':str(cc),
         'AR_x86_64_unknown_linux_gnu':str(ar),
         'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER':str(cc),
         'OPENSSL_STATIC':'1','OPENSSL_NO_VENDOR':'1',
         'OPENSSL_INCLUDE_DIR':str(kit/'openssl/include'),'OPENSSL_LIB_DIR':str(kit/'openssl/lib')}
    command=[str(cargo),'--config','source.crates-io.replace-with="kit-vendor"',
             '--config','source.kit-vendor.directory='+json.dumps(str(kit/'vendor')),
             'build','--manifest-path',str(source/'Cargo.toml'),'--release','--locked','--offline',
             '--target',target,'--bin','praxis-relay-headless','-j',str(jobs)]
    before=digest(source/'Cargo.lock')
    with (output/'build.log').open('wb') as log:
        result=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError('Native build failed; see '+str(output/'build.log'))
    binary=output/'target'/target/'release/praxis-relay-headless'
    if before!=digest(source/'Cargo.lock'):raise ValueError('Build changed source lock')
    receipt={'format':'helene-native-rebuild.v1','target':target,'source_lock_sha256':before,
             'binary':str(binary),'sha256':digest(binary),'size':binary.stat().st_size,
             'rust':subprocess.check_output([str(rustc),'-Vv'],text=True),
             'network':'offline Cargo; consumer must separately enforce network isolation'}
    (output/'receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    return receipt


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('kit','source','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--jobs',type=int,default=2)
    a=p.parse_args();print(json.dumps(rebuild(a.kit,a.source,a.output,jobs=a.jobs),indent=2))
