"""Publisher-bound optional native tools; private mutable npm cache per update.

No PATH discovery, downloads, credentials, global installers or authority to
weaken the source battery. The verification manifest binds every shipped byte.
"""
from pathlib import Path
import hashlib,json,os,shutil,stat,sys
from . import source_verification_bundle as bundle

def inventory(root):
    root=Path(root)
    if root.is_symlink() or not root.is_dir() or getattr(root.lstat(),'st_file_attributes',0)&0x400:
        raise ValueError('Native verification tools unavailable')
    result={}
    for directory,dirs,names in os.walk(root):
        for name in sorted([*dirs,*names]):
            p=Path(directory)/name;relative=p.relative_to(root).as_posix();bundle.relative(relative)
            mode=p.lstat()
            if stat.S_ISLNK(mode.st_mode) or getattr(mode,'st_file_attributes',0)&0x400:
                raise ValueError('Linked native verification input: '+relative)
            # Windows chmod does not represent POSIX group/other write bits.
            # Pin its regular files with the same canonical mode as the ZIP,
            # rather than rejecting every file reported as 0666 by NTFS.
            if p.is_file():result[relative]={'sha256':bundle.digest(p),'size':mode.st_size,
                'mode':0o644 if os.name=='nt' else stat.S_IMODE(mode.st_mode)}
            elif not p.is_dir():raise ValueError('Special native verification input: '+relative)
    return result

def describe(root,*,platform=None):
    if platform not in (None,'win32','linux','darwin'):raise ValueError('Unsupported native verification platform')
    files=inventory(root)
    tools=({'node':'bin/node.exe','npm':'npm/bin/npm-cli.js'} if platform=='win32'
           else {'node':'bin/node','npm':'bin/npm'})
    row={'root':'verification-tools','tools':tools,'npm_cache':'cache','files':files}
    declaration(row)
    return row

def declaration(row):
    if not isinstance(row,dict) or row.get('root')!='verification-tools':raise ValueError('Invalid native tool root')
    if row.get('tools') not in ({'node':'bin/node','npm':'bin/npm'},
            {'node':'bin/node.exe','npm':'npm/bin/npm-cli.js'}) or row.get('npm_cache')!='cache':
        raise ValueError('Unsupported native verification tools')
    files=row.get('files')
    if not isinstance(files,dict) or not files:raise ValueError('Missing native tool inventory')
    for name,value in files.items():
        bundle.relative(name)
        if (not isinstance(value,dict) or set(value)!={'sha256','size','mode'}
                or not isinstance(value['size'],int) or value['size']<0
                or not isinstance(value['sha256'],str) or len(value['sha256'])!=64
                or not isinstance(value['mode'],int) or value['mode'] & ~0o755):
            raise ValueError('Invalid native tool file: '+name)
    if not {*row['tools'].values(),'npm/bin/npm-cli.js'}<=set(files) or not any(n.startswith('cache/_cacache/') for n in files):
        raise ValueError('Incomplete offline native verification tools')

def provision(program,manifest,directory,stopped=lambda:False):
    row=manifest.get('bundled_executors',{}).get(sys.platform)
    if row is None:return {}
    declaration(row)
    program=Path(program).resolve();root=program/row['root']
    if inventory(root)!=row['files']:raise ValueError('Bundled native verification inputs changed')
    expected=manifest['executor_locks'][sys.platform]['tools']
    for tool,name in row['tools'].items():
        if expected.get(tool)!={'sha256':row['files'][name]['sha256']}:
            raise ValueError('Bundled tool differs from native executor lock: '+tool)
    identity=hashlib.sha256(bundle.encoded(row)).hexdigest()
    directory=Path(directory).resolve()
    target=directory/'native-executor-cache'/identity;receipt=target.with_suffix('.json')
    if target.parent.is_symlink() or receipt.is_symlink():raise ValueError('Linked native cache admission path')
    if target.exists():
        if target.is_symlink() or not receipt.is_file() or json.loads(receipt.read_text())!={'input_sha256':identity}:
            raise ValueError('Native cache admission is unresolved; retained private work requires inspection')
        inventory(target)  # Mutable content is npm-integrity checked; links never qualify.
    else:
        if stopped():raise InterruptedError('Owner stopped native tool preparation')
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(root/row['npm_cache'],target)
        if stopped():raise InterruptedError('Owner stopped native tool preparation; private cache retained')
        receipt.write_bytes(bundle.encoded({'input_sha256':identity}))
    return {**{tool:str(root/name) for tool,name in row['tools'].items()},'npm_config_cache':str(target)}
