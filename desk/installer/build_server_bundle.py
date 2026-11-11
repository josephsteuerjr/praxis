"""Create a server-only release input; native Docker qualification is separate.

The archive contains the selected Python agent/channel, browser clients and the
native subscription relay. It contains no desktop interpreter, Qt or user data.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,re,shutil,sys,zipfile
from portable_sources import application,compose_core
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import deskpkg


def digest(raw):return hashlib.sha256(raw).hexdigest()


def relay_bytes(path,arch):
    raw=Path(path).read_bytes()
    if (len(raw)<20 or raw[:6]!=b'\x7fELF\x02\x01'
            or int.from_bytes(raw[18:20],'little')!={'x86_64':62,'arm64':183}[arch]):
        raise ValueError('Relay ELF does not match the selected server architecture')
    return raw


def build(source,output,*,version,arch,relay):
    source,output,relay=(Path(p).resolve() for p in (source,output,relay))
    if arch not in ('arm64','x86_64'):raise ValueError('Unsupported server architecture')
    if not re.fullmatch(r'\d+\.\d+\.\d+',version):raise ValueError('Expected a release version')
    if output.exists():raise FileExistsError(output)
    binary=relay_bytes(relay/'praxis-relay-headless',arch)
    for name in ('LICENSE','BUILD-PASSPORT.json'):
        if not (relay/name).is_file():raise ValueError('Relay provenance/license missing: '+name)
    native=json.loads((relay/'BUILD-PASSPORT.json').read_text(encoding='utf8'))
    if (native.get('format')!='helene-server-relay.v1' or native.get('target')!='linux-'+arch
            or native.get('sha256')!=digest(binary) or not re.fullmatch('[0-9a-f]{40}',str(native.get('source_commit','')))):
        raise ValueError('Native relay provenance does not bind these bytes and architecture')
    core,core_origins=compose_core(source);app,app_origins=application(source)
    output.mkdir(parents=True)
    def write(name,raw,mode=0o644):
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(raw);target.chmod(mode)
    def record(name,value):write(name,(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode())
    for name,entry in core.items():write('tree/'+name,entry.data,int(entry.mode[-3:],8))
    for name,entry in app.items():write(name,entry.data,int(entry.mode[-3:],8))
    # These dependencies power the actual Hélène runner; GUI wheels are excluded.
    requirements=core['requirements.txt'].data.decode('utf8')+'\n# Server channel and local runner\n'
    requirements+='\n'.join(dict.fromkeys([*deskpkg.DEPS_CHANNEL,*deskpkg.DEPS_RUNNER,'PyYAML','psutil','PySocks']))+'\n'
    write('requirements.txt',requirements.encode())
    for name in ('LICENSE','NOTICE'):write(name,(source/name).read_bytes())
    write('THIRD-PARTY.md',(source/'desk/installer/THIRD-PARTY.md').read_bytes())
    for name in ('praxis-relay-headless','LICENSE','LICENSE-APACHE','NOTICE','BUILD-PASSPORT.json'):
        if (relay/name).is_file():write('relay-product/'+name,(relay/name).read_bytes(),0o755 if name=='praxis-relay-headless' else 0o644)
    write('helene.json',(source/'desk/server/helene.server.json').read_bytes())
    for name in ('data','components','component-store','component-state'):(output/name).mkdir(exist_ok=True)
    target='linux-'+arch
    unavailable={name:'Local optional speech payload has not been qualified for this server target' for name in ('whisper','xtts')}
    record('app/component-downloads.json',dict(format='helene-component-downloads.v1',target=target,extensions={name:[] for name in unavailable},artifacts={},unavailable=unavailable))
    record('python-app.json',dict(format='helene-python-app.v1',version=version,target=target,server=True,window=None))
    record('helene-build.json',dict(product='Hélène',version=version,server_target=target,
        format='helene-server-build.v1',complete=True,desk={'flavor':'server-'+target},
        built_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),qualification='Native server qualification required'))
    app_hashes={p.relative_to(output/'app').as_posix():digest(p.read_bytes()) for p in (output/'app').rglob('*') if p.is_file() and p.name!='desk.json'}
    record('app/desk.json',dict(version=version,flavor='server-'+target,files=app_hashes,
        digest=digest('\n'.join(f'{k} {v}' for k,v in sorted(app_hashes.items())).encode())))
    sys.path.insert(0,str(source/'desk/server/updater'))
    import codecarry
    record('server/updater/shipped-code.json',{version+'/server-'+target:codecarry.code_prints(output)})
    record('SERVER-SOURCE.json',dict(format='helene-server-source.v1',version=version,target=target,
        core={name:dict(origin=core_origins[name],sha256=digest(entry.data)) for name,entry in core.items()},
        application={name:dict(origin=app_origins[name],sha256=digest(entry.data)) for name,entry in app.items()},
        relay_sha256=digest(binary)))
    archive=output.parent/f'Helene-{version}-server-{target}.zip'
    if archive.exists():raise FileExistsError(archive)
    with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,strict_timestamps=False) as z:
        for p in sorted(output.rglob('*')):
            if p.is_symlink():raise ValueError('Linked server stage')
            if p.is_file():z.write(p,'Helene/'+p.relative_to(output).as_posix())
    archive.with_name(archive.name+'.sha256').write_text(digest(archive.read_bytes())+'  '+archive.name+'\n')
    return archive


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output','relay'):parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--version',required=True);parser.add_argument('--arch',choices=('arm64','x86_64'),required=True)
    args=parser.parse_args();print(build(args.source,args.output,version=args.version,arch=args.arch,relay=args.relay))
