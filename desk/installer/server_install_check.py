"""Real install.sh and updater with a prewritten GUI-style config on disposable CI."""
from pathlib import Path
import json,os,socket,subprocess,tarfile,time,urllib.request,uuid


def check(program, image, output):
    program, output = Path(program).resolve(), Path(output).resolve()
    output.mkdir(exist_ok=False)
    name='helene-install-check-'+uuid.uuid4().hex[:10]
    target=output/'installed';stage=output/'uploaded';target.mkdir();stage.mkdir()
    names=[name,name+'-updater'];owned=[];rows=[]
    def call(label,args,timeout=900):
        with (output/(label+'.log')).open('wb') as log:
            result=subprocess.run(list(map(str,args)),stdout=log,stderr=subprocess.STDOUT,timeout=timeout)
        rows.append({'name':label,'exit':result.returncode})
        if result.returncode:raise RuntimeError(label+' failed; read retained log')
    for entry in names:
        if subprocess.run(['docker','inspect',entry],capture_output=True).returncode==0:
            raise ValueError('Fixture container name already exists')
    with socket.socket() as selected:
        selected.bind(('127.0.0.1',0));port=selected.getsockname()[1]
    config=json.loads((program/'helene.json').read_text())
    config.update(phone={'enabled':False},relay={'enabled':False},autonomy={'paused':True},telegram={})
    config['agent']={'name':'Installer qualification fixture'}
    raw=(json.dumps(config,ensure_ascii=False,indent=2)+'\n').encode()
    (target/'helene.json').write_bytes(raw);(target/'helene.json').chmod(0o600)
    (target/'.window-install.json').write_text(json.dumps({'id':name,'kind':'fresh'}))
    try:
        # The same actual package selector used by the application's SSH upload.
        call('gui-kit',['docker','run','--rm','-v',str(program)+':/input:ro','-v',str(output)+':/output',
            '-e','PYTHONPATH=/opt/helene/tree:/opt/helene/app:/opt/helene/app/localharness',
            '-e','PRAXIS_TEST=1','--entrypoint','python',image,'-c',
            "from pathlib import Path;from localharness.server_transfer import make_program;import platform;make_program(Path('/input'),Path('/output/program.tar.gz'),arch=platform.machine())"])
        with tarfile.open(output/'program.tar.gz') as archive:archive.extractall(stage,filter='data')
        env=['HELENE_DIR='+str(target),'HELENE_PROJECT='+name,'HELENE_CONTAINER='+name,'HELENE_PORT='+str(port)]
        call('actual-install',['sudo','env',*env,'sh',stage/'server/install.sh'],timeout=3600)
        for entry in names:
            row=json.loads(subprocess.check_output(['docker','inspect',entry]))[0];owned.append(row['Id'])
        # The actual installer deliberately makes its installation private to
        # root. Read only this disposable fixture through the same authority.
        if subprocess.check_output(['sudo','cat',str(target/'helene.json')])!=raw:raise ValueError('Installer replaced the prewritten application configuration')
        key=subprocess.check_output(['docker','exec',name,'cat','/opt/helene/data/.serverboot/desk-token'],text=True).strip()
        url='http://127.0.0.1:'+str(port)+'/api/health'
        deadline=time.monotonic()+60
        while True:
            try:
                with urllib.request.urlopen(urllib.request.Request(url,headers={'Authorization':'Bearer '+key}),timeout=3) as response:
                    health=json.load(response)
                break
            except Exception:
                if time.monotonic()>deadline:raise
                time.sleep(1)
        call('updater-status',['docker','exec',name+'-updater','python3',str(target/'server/updater/updater.py'),'status'])
        # Repeating the same action must retain the profile and existing version.
        call('actual-install-repeat',['sudo','env',*env,'sh',stage/'server/install.sh'],timeout=180)
        if subprocess.check_output(['sudo','cat',str(target/'helene.json')])!=raw:raise ValueError('Repeated installer replaced configuration')
        (output/'COMPLETE.json').write_text(json.dumps({'passed':True,'health':health,'checks':rows,
            'prewritten_config_retained':True,'real_install_script':True,'real_docker':True,
            'real_telegram_bot':False},indent=2))
    finally:
        for entry in names:
            result=subprocess.run(['docker','inspect',entry],capture_output=True,text=True)
            if result.returncode==0:
                row=json.loads(result.stdout)[0]
                if row['Name']=='/'+entry and row['Id'] not in owned:owned.append(row['Id'])
        for identifier in owned:
            with (output/('container-'+identifier[:12]+'.log')).open('wb') as log:
                subprocess.run(['docker','logs',identifier],stdout=log,stderr=subprocess.STDOUT)
            subprocess.run(['docker','rm','-f',identifier],stdout=subprocess.DEVNULL,check=True)
        # No broad cleanup: fixture directories and images remain on the CI host.
