"""Native HTTPS update/rollback with synthetic data; never a release artifact."""
from pathlib import Path
import hashlib,http.server,json,os,shutil,ssl,subprocess,sys,threading,time,urllib.request,zipfile


def run(root,evidence):
    root=Path(root).resolve();evidence=Path(evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    assert root.name in ('Helene','Helene portable') and (root/'python-app.json').is_file()
    python=root/'runtime/bin/python3';env=dict(os.environ,PYTHONNOUSERSITE='1')
    result={'synthetic_archive_fixture':True,'steps':{}}
    def save():(evidence/'update.json').write_text(json.dumps(result,indent=2))
    def command(args):return subprocess.check_output(list(map(str,args)),env=env,cwd=evidence,stderr=subprocess.STDOUT,text=True)
    def sha(p):
        with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
    def wait(fn,seconds=180):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            v=fn()
            if v:return v
            time.sleep(.2)
        raise TimeoutError('Native update condition not observed')
    def control():
        for p in root.glob('.python-window-*.json'):
            try:
                v=json.loads(p.read_text());os.kill(v['pid'],0)
                if Path(v['root']).resolve()==root:return v
            except (ValueError,OSError):pass
    def invoke(name,args=None):
        v=wait(control)
        req=urllib.request.Request(v['origin']+'/__python__/invoke',data=json.dumps({'command':name,'args':args or {}}).encode(),headers={'Content-Type':'application/json','X-Helene-Window':v['token']})
        with urllib.request.urlopen(req,timeout=180) as r:return json.load(r)['result']
    def close():
        if control():invoke('python_window',{'action':'exit','relaunch':False});wait(lambda:control() is None,40);time.sleep(.5)
    def launch():
        if sys.platform=='darwin':command(['open','-n',root/'Helene.app'])
        else:command([root/'Start Helene.sh'])
        wait(control)
    def facts():
        cfg=json.loads((root/'helene.json').read_text());data=(root/cfg['tree']).resolve();code=(root/cfg['code']).resolve()
        return {'memory':sha(data/'memory/history.txt'),'identity':sha(data/'soul/identity.md'),
            'index':sha(code/'.git/index'),'head':command(['git','-C',code,'rev-parse','HEAD']).strip(),
            'authored':sha(code/'fixture_author.py'),'untracked':sha(code/'fixture_untracked.py'),'custom':cfg['fixture_custom']}
    close()
    cert=evidence/'test-cert.pem';key=evidence/'test-key.pem'
    command(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost','-keyout',key,'-out',cert])
    env['SSL_CERT_FILE']=str(cert)
    # Finder doesn't inherit shell env. launchctl affects only this disposable
    # GitHub runner user and is restored in finally.
    if sys.platform=='darwin':command(['launchctl','setenv','SSL_CERT_FILE',cert])
    serve=evidence/'serve';serve.mkdir(exist_ok=True)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(serve),**kw)
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cert,key);server.socket=ctx.wrap_socket(server.socket,server_side=True)
    threading.Thread(target=server.serve_forever,daemon=True).start();url='https://localhost:'+str(server.server_port)
    selected=[]
    for p in root.rglob('*'):
        if not p.is_file():continue
        rel=p.relative_to(root).as_posix();top=p.relative_to(root).parts[0]
        if top.startswith('.') or top in ('profile','data','backups','agents','downloads','components') or '__pycache__' in p.parts or p.suffix=='.pyc':continue
        if p.name in ('installed-package.json','python-migration.json','window-preferences.json') or p.suffix=='.log':continue
        if rel.startswith('runtime/system-libs/'):continue
        if '/site-packages/' in rel and not rel.split('/site-packages/',1)[1].startswith(('psutil/','socks.py','socksipy')):continue
        selected.append((rel,p))
    meta=json.loads((root/'python-app.json').read_text());target=meta['target']
    archives=[]
    for version,broken in [('2.1.1',False),('2.1.2',True)]:
        changes={'python-app.json':json.dumps(dict(meta,version=version,delivery='portable',synthetic_update_fixture=True)).encode(),
                 'tree/fixture_product_version.py':('VERSION='+repr(version)+'\n').encode()}
        if broken:changes['app/python_app.py']=b'import os;os._exit(73)\n'
        shipped={'format':'helene-shipped-code.v1','files':{p.relative_to(root/'tree').as_posix():sha(p) for p in (root/'tree').rglob('*') if p.is_file() and '__pycache__' not in p.parts and '.git' not in p.parts}}
        shipped['files']['fixture_product_version.py']=hashlib.sha256(changes['tree/fixture_product_version.py']).hexdigest()
        changes['shipped-code.json']=json.dumps(shipped).encode()
        inventory={rel:hashlib.sha256(changes.get(rel,p.read_bytes())).hexdigest() for rel,p in selected if not rel.startswith('tree/') and rel not in ('helene.json','python-package-files.json')}
        changes['python-package-files.json']=json.dumps({'format':'helene-python-program-files.v1','version':version,'files':inventory}).encode()
        archive=serve/('Helene-'+version+'-'+target+'.zip');assert not archive.exists()
        with zipfile.ZipFile(archive,'x',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            added=set()
            for rel,p in selected:
                item=zipfile.ZipInfo('Helene/'+rel);item.external_attr=(0o100000|p.stat().st_mode&0o777)<<16
                z.writestr(item,changes.get(rel,p.read_bytes()),compress_type=zipfile.ZIP_DEFLATED);added.add(rel)
            for rel,data in changes.items():
                if rel not in added:z.writestr('Helene/'+rel,data)
        archives.append((archive,version,broken))
    cfg=json.loads((root/'helene.json').read_text())
    if 'fixture_custom' not in cfg:
        cfg.update(tree='data',code='tree',setup_complete=True,fixture_custom={'retained':'exact'})
        (root/'helene.json').write_text(json.dumps(cfg))
        data=root/'data';(data/'memory').mkdir(parents=True,exist_ok=True);(data/'soul').mkdir(exist_ok=True)
        (data/'memory/history.txt').write_text('Synthetic retained history');(data/'soul/identity.md').write_text('Synthetic identity')
        code=root/'tree';(code/'fixture_author.py').write_text('BASE=1\n')
        def git(*a):return command(['git','-C',code,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid',*a])
        git('init','-q');git('add','.');git('commit','-qm','Synthetic fixture')
        (code/'fixture_author.py').write_text('STAGED=2\n');git('add','fixture_author.py');(code/'fixture_author.py').write_text('UNSTAGED=3\n')
        (code/'fixture_untracked.py').write_text('UNTRACKED=4\n')
    command([python,'-c',"import sys;sys.path.insert(0,sys.argv[1]);from localharness import owner_stop;owner_stop.latch_client('synthetic-native-fixture')",root/'app'])
    expected=facts();save()
    try:
        launch();prior=invoke('app_info')['version']
        bad=serve/('Helene-9.9.7-'+target+'.zip');bad.write_bytes(b'Invalid bytes')
        try:invoke('update_download',{'url':url+'/'+bad.name,'sha256':'0'*64,'extensions':[]})
        except urllib.error.HTTPError:result['steps']['wrong_sha_refused']=True
        else:raise AssertionError('Wrong SHA accepted')
        for archive,version,broken in archives:
            downloaded=invoke('update_download',{'url':url+'/'+archive.name,'sha256':sha(archive),'extensions':[]})
            assert downloaded['sha_ok']
            response=invoke('update_install',{'path':downloaded['path'],'forceExtensions':False})
            (evidence/(version+'-handoff.json')).write_text(json.dumps(response,indent=2))
            if broken:
                wait(lambda:'previous program restored' in Path(response['log']).read_text(errors='replace'),200)
            else:
                def complete():
                    n=json.loads((root/'python-migration.json').read_text())
                    return n.get('state')=='completed' and Path(n.get('update_source',''))==Path(response['staging'])/'Helene'
                wait(complete,200);prior=version
            wait(control,120);assert invoke('app_info')['version']==prior
            assert facts()==expected
            cfg=json.loads((root/'helene.json').read_text());assert (root/cfg['tree']).resolve().is_relative_to(root)
            assert (root/cfg['code']/'fixture_product_version.py').read_text()=='VERSION='+repr(prior)+'\n'
            result['steps'][version]={'broken':broken,'actual_version':prior,'same_folder':True,'profile_git_exact':True}
            save()
        close()
    except BaseException as exc:
        result['error']=type(exc).__name__+': '+str(exc);save();raise
    finally:
        server.shutdown()
        if sys.platform=='darwin':command(['launchctl','unsetenv','SSL_CERT_FILE'])
    return result


if __name__=='__main__':run(Path(sys.argv[1]),Path(sys.argv[2]))
