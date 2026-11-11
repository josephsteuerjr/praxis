"""Build/qualify the ARM server archive on an owned native Linux CI runner."""
from pathlib import Path
import argparse,hashlib,json,os,platform,shutil,subprocess,sys,time,uuid,zipfile


def qualify(source,relay_source,output):
    source,relay_source,output=(Path(p).resolve() for p in (source,relay_source,output))
    if sys.platform!='linux' or platform.machine().lower() not in ('aarch64','arm64'):
        raise ValueError('Use a native Linux ARM64 runner')
    output.mkdir(exist_ok=False);evidence=output/'evidence';evidence.mkdir()
    prefix='helene-arm-'+uuid.uuid4().hex[:10];owned=[];rows=[]
    def run(name,args,*,timeout=1800,cwd=None):
        with (evidence/(name+'.log')).open('w') as log:
            p=subprocess.run(list(map(str,args)),cwd=cwd,stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        rows.append(dict(name=name,exit=p.returncode));(evidence/'CHECKS.json').write_text(json.dumps(rows,indent=2))
        if p.returncode:raise RuntimeError(name+' failed; original log retained')
    def capture(args):return subprocess.check_output(list(map(str,args)),text=True).strip()
    commit=capture(['git','-C',relay_source,'rev-parse','HEAD'])
    if commit!='d5a5bfed9d12cf0b7d052d310edceb03b66a77b3':raise ValueError('Unexpected relay source')
    if capture(['git','-C',relay_source,'status','--porcelain']):raise ValueError('Relay source is modified')
    try:
        native_image=prefix+'-relay'
        run('native-relay-build',['docker','build','--progress=plain','-f',source/'desk/installer/server-relay.Dockerfile','-t',native_image,relay_source],timeout=3600)
        container=capture(['docker','create','--name',prefix+'-export',native_image,'/unused']);owned.append(container)
        relay=output/'native-relay';relay.mkdir()
        for name in ('praxis-relay-headless','LICENSE','LICENSE-APACHE','NOTICE'):
            run('relay-export-'+name,['docker','cp',container+':/'+name,relay/name])
        binary=relay/'praxis-relay-headless';binary.chmod(0o755)
        (relay/'BUILD-PASSPORT.json').write_text(json.dumps(dict(format='helene-server-relay.v1',target='linux-arm64',source_commit=commit,version='1.1.3',sha256=hashlib.sha256(binary.read_bytes()).hexdigest()),indent=2))
        sys.path[:0]=[str(source/'desk'),str(source/'desk/installer')]
        import deskpkg
        from build_server_bundle import build
        version=deskpkg.product_version()[0]
        program=output/'Helene'
        archive=build(source,program,version=version,arch='arm64',relay=relay)
        with zipfile.ZipFile(archive) as z:
            if z.testzip() is not None:raise ValueError('Server ZIP CRC failure')
            if any('/runtime/' in n for n in z.namelist()):raise ValueError('Desktop runtime in server archive')
            names=z.namelist();assert 'Helene/app/miniapp/index.html' in names and 'Helene/app/static/index.html' in names
        # The image uses the actual distributed Dockerfile and requirements.
        image=prefix+'-server'
        run('server-image',['docker','build','--progress=plain','-f',program/'server/Dockerfile','-t',image,program],timeout=3600)
        common=['docker','run','--rm','-v',str(relay)+':/opt/helene/relay-product:ro',
                '-e','PYTHONPATH=/opt/helene/tree:/opt/helene/app:/opt/helene/app/localharness',
                '-e','PRAXIS_TEST=1','-e','PRAXIS_BASE=/tmp/helene-import','--entrypoint','python',image]
        run('actual-runtime-imports',[*common,'-c',"import platform,agent,faster_whisper,av,psutil,telethon; assert platform.machine()=='aarch64'"],timeout=180)
        run('actual-relay',[*common,'-c',"import subprocess;subprocess.run(['/opt/helene/relay-product/praxis-relay-headless','--help'],check=True)"],timeout=120)
        # Use the package's composed core and its hermetic entrypoint, never an owner.
        test=['docker','run','--rm','-w','/opt/helene/tree','-e','PYTHONPATH=/opt/helene/tree:/opt/helene/app:/opt/helene/app/localharness','-e','PRAXIS_TEST=1','--entrypoint','python',image]
        run('native-python-hands',[*test,'praxis_test.py','-q','test_hand_bridge_1011','test_python_hand_1011','test_python_tail_1011','test_read_outcomes_1011'],timeout=600)
        run('native-host-broker',['docker','run','--rm','-v',str(source)+':/review-source:ro',
            '--entrypoint','python',image,'/review-source/desk/tests/t_server_host_broker.py'],timeout=180)
        # Start a separate paused, credential-free profile and verify the real
        # channel. This container owns only task data; no Telegram/provider login.
        config=json.loads((program/'helene.json').read_text());config.update(autonomy={'paused':True},phone={'enabled':False},relay={'enabled':False})
        config_file=output/'fixture-config.json';config_file.write_text(json.dumps(config));config_file.chmod(0o644)
        data=output/'fixture-data';data.mkdir()
        name=prefix+'-live'
        container=capture(['docker','run','-d','--name',name,'-p','127.0.0.1::8094',
            '-v',str(config_file)+':/opt/helene/helene.json','-v',str(data)+':/opt/helene/data',
            '-v',str(relay)+':/opt/helene/relay-product:ro',image]);owned.append(container)
        import urllib.request
        port=json.loads(capture(['docker','inspect',container]))[0]['NetworkSettings']['Ports']['8094/tcp'][0]['HostPort']
        url='http://127.0.0.1:'+port
        import urllib.error
        # The channel admits only its window key (serverboot writes it into the data
        # volume). Read it inside the container: never in argv, logs or evidence.
        def desk_key():
            return subprocess.run(['docker','exec',container,'cat','/opt/helene/data/.serverboot/desk-token'],
                                  capture_output=True,text=True).stdout.strip()
        deadline=time.monotonic()+90;last='';key='';anonymous=None
        while time.monotonic()<deadline:
            try:
                key=key or desk_key()
                if not key:raise RuntimeError('window key not written yet')
                if anonymous is None:
                    try:urllib.request.urlopen(url+'/api/health',timeout=3).close();anonymous=200
                    except urllib.error.HTTPError as denied:anonymous=denied.code
                request=urllib.request.Request(url+'/api/health',headers={'Authorization':'Bearer '+key})
                with urllib.request.urlopen(request,timeout=3) as response:
                    health=json.load(response)
                break
            except urllib.error.HTTPError as error:last=f'HTTP {error.code}';time.sleep(1)
            except Exception as error:last=type(error).__name__+': '+str(error)[:120];time.sleep(1)
        else:raise RuntimeError('Actual server did not become healthy: '+last)
        if anonymous not in (401,403):raise RuntimeError(f'Channel answered health without its key: {anonymous}')
        (evidence/'HTTP.json').write_text(json.dumps(dict(ok=True,anonymous_status=anonymous,health=health),indent=2))
        run('native-target-check',['docker','exec',container,'python','server/check_target.py','/opt/helene'])
        from server_install_check import check as check_install
        check_install(program,image,evidence/'real-application-install')
        (evidence/'COMPLETE.json').write_text(json.dumps(dict(ok=True,version=version,arch=platform.machine(),archive=archive.name,sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),checks=rows,scope='Native ARM Docker runtime, relay, hands, paused channel; no owner/provider acceptance'),indent=2))
    finally:
        for container in owned:
            with (evidence/('container-'+container[:12]+'.log')).open('w') as log:
                subprocess.run(['docker','logs',container],stdout=log,stderr=subprocess.STDOUT)
            subprocess.run(['docker','rm','-f',container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        # Images remain on this disposable CI host; no broad Docker cleanup.


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','relay-source','output'):parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();qualify(args.source,args.relay_source,args.output)
