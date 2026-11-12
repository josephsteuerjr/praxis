"""Exercise Hélène's actual Python controls against an isolated native relay."""
from pathlib import Path
import argparse,json,os,socket,socketserver,subprocess,sys,threading,time
from types import SimpleNamespace


def qualify(binary,output):
    binary,output=Path(binary).resolve(),Path(output).resolve();output.mkdir(exist_ok=False)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    from localharness import python_relay
    end=threading.Event()
    class Sink(socketserver.BaseRequestHandler):
        def handle(self):
            # A local proxy that holds CONNECT. Provider I/O cannot occur, and
            # the pending device operation remains observable until cancellation.
            self.request.settimeout(5)
            try:self.request.recv(4096);end.wait(20)
            except OSError:pass
    class Server(socketserver.ThreadingTCPServer):daemon_threads=True
    proxy=Server(('127.0.0.1',0),Sink)
    threading.Thread(target=proxy.serve_forever,daemon=True).start()
    with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    tree=output/'tree';auth=tree/'relay/local_auth';auth.mkdir(parents=True)
    config=output/'helene.json';config.write_text(json.dumps({'relay':{'enabled':True,'port':port,'key':'synthetic-private-client-key'}}))
    agent=SimpleNamespace(config=config,tree=tree);owner=SimpleNamespace(current=lambda:agent)
    route='http://127.0.0.1:'+str(proxy.server_address[1])
    env=dict(os.environ,RELAY_AUTH_DIR=str(auth),RELAY_PORT=str(port),RELAY_NO_BROWSER='1',
             RELAY_MODEL_DISCOVERY='off',RELAY_API_KEY='synthetic-private-client-key',RELAY_NETWORK_MODE='proxy',
             RELAY_PROXY_URL=route,HTTP_PROXY=route,HTTPS_PROXY=route,ALL_PROXY=route,NO_PROXY='127.0.0.1,localhost')
    process=None;checks=[]
    try:
        with (output/'process.log').open('wb') as log:
            process=subprocess.Popen([str(binary),'serve','--headless'],env=env,stdin=subprocess.DEVNULL,
                stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        deadline=time.monotonic()+15
        while not (auth/'control.json').is_file() and time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Native relay exited; inspect process.log')
            time.sleep(.1)
        first=python_relay.invoke(owner,'relay_account',{});assert first['reachable']
        checks.append('real private control reachable')
        for slot in ('primary','secondary'):
            started=python_relay.invoke(owner,'relay_login',{'slot':slot,'method':'device_code'})
            ident=started['login']['id'];assert ident and started['login']['method']=='device_code' and started['login']['slot']==slot
            view=python_relay.invoke(owner,'relay_account',{})
            assert view['login']['id']==ident and view['login']['pending']
            assert 'synthetic-private-client-key' not in json.dumps(view)
            python_relay.invoke(owner,'relay_cancel_login',{'slot':slot,'login_id':'00000000-0000-0000-0000-000000000000'})
            assert python_relay.invoke(owner,'relay_account',{})['login']['pending'],'Stale cancel affected new login'
            python_relay.invoke(owner,'relay_cancel_login',{'slot':slot,'login_id':ident})
            stopped=python_relay.invoke(owner,'relay_account',{})['login']
            assert not stopped['pending'] and not stopped['device_code'] and not stopped['url']
            checks.append(slot+' device start, stale cancel fenced, exact cancel cleared')
        assert not list(auth.rglob('auth.json')),'Fixture must not write account credentials'
        value={'passed':True,'binary':str(binary),'checks':checks,'provider_contact':False,'owner_credentials':False}
        (output/'PASS.json').write_text(json.dumps(value,indent=2));print(json.dumps(value))
    finally:
        end.set();proxy.shutdown();proxy.server_close()
        if process is not None and process.poll() is None:process.terminate();process.wait(timeout=10)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--binary',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();qualify(args.binary,args.output)
