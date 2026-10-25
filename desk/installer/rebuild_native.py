"""Rebuild bundled native components offline with the included GNU Rust toolchain."""
from pathlib import Path
import argparse
import os
import subprocess
import sys
import uuid


def build(home: Path, component: str):
    product=home.parent.parent
    source=product/'components'/component/'source'
    if sys.platform=='linux':
        if component!='relay':raise ValueError('This Linux kit rebuilds the headless relay, not the cancelled broker')
        from rebuild_native_linux import rebuild
        result=rebuild(home,source,home.parent/('relay-build-'+uuid.uuid4().hex))
        return Path(result['binary'])
    rust=home/'rust';gcc=home/'w64devkit/bin'
    env=dict(os.environ)
    env.update(RUSTC=str(rust/'bin/rustc.exe'),CARGO_TARGET_DIR=str(home/'build'/component),
        CARGO_TARGET_X86_64_PC_WINDOWS_GNU_LINKER=str(rust/'lib/rustlib/x86_64-pc-windows-gnu/bin/self-contained/x86_64-w64-mingw32-gcc.exe'),
        CC_x86_64_pc_windows_gnu=str(gcc/'gcc.exe'),CXX_x86_64_pc_windows_gnu=str(gcc/'g++.exe'),AR_x86_64_pc_windows_gnu=str(gcc/'ar.exe'),
        RUSTFLAGS='-C link-self-contained=yes -L native='+str(rust/'lib/rustlib/x86_64-pc-windows-gnu/lib/self-contained').replace('\\','/'))
    env['PATH']=str(rust/'bin')+os.pathsep+str(gcc)+os.pathsep+env.get('PATH','')
    manifest=source/('desk/svc/Cargo.toml' if component=='broker' else 'Cargo.toml')
    args=[str(rust/'bin/cargo.exe'),'build','--offline','--locked','--release','--manifest-path',str(manifest),'--target','x86_64-pc-windows-gnu','-j','2',
        '--config','source.crates-io.replace-with="helene-vendor"','--config','source.helene-vendor.directory="'+str(home/'vendor').replace('\\','/')+'"']
    subprocess.run(args,cwd=home,env=env,check=True)
    return home/'build'/component/'x86_64-pc-windows-gnu/release'/('helene-svc.exe' if component=='broker' else 'codex-proxy-server.exe')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('component',choices=('broker','relay'))
    print(build(Path(__file__).resolve().parent,parser.parse_args().component))
