"""Subscription-login child custody and private relay transport for Python UI."""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.parse
import urllib.request

from .essential_backup import load


SLOTS = ('primary', 'secondary')


@contextmanager
def auth_write_lock(path):
    """Same sidecar/OS lock as the relay's Rust refresh and OAuth writer."""
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.with_suffix('.json.write-lock').open('a+b') as lock:
        lock.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_LOCK,1)
        else:
            import fcntl
            fcntl.flock(lock,fcntl.LOCK_EX)
        try:yield
        finally:
            lock.seek(0)
            if os.name=='nt':msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(lock,fcntl.LOCK_UN)


def selected_slot(args):
    slot=args.get('slot','primary')
    if slot not in SLOTS:raise ValueError('Expected primary or secondary subscription slot')
    return slot


def auth_paths(home,slot):
    paths=[home/'local_auth/accounts'/slot/'auth.json']
    if slot=='primary':paths.append(home/'local_auth/auth.json')
    return paths


def signature(home,slot='primary'):
    for path in auth_paths(home,slot):
        if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def changed(home):
    marker=home/'local_auth/login-generation';marker.parent.mkdir(parents=True,exist_ok=True)
    temporary=marker.with_suffix('.tmp');temporary.write_text(str(time.time_ns()),encoding='ascii');os.replace(temporary,marker)


def relay_request(agent,path,payload=None):
    cfg=load(agent.config);relay=cfg.get('relay') or {};port=int(relay.get('port',5011))
    key=relay.get('key') or (cfg.get('model') or {}).get('key') or ''
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*a,**kw):return None
    request=urllib.request.Request(f'http://127.0.0.1:{port}/v1/'+path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=15) as response:
        return json.load(response)


def control(agent,op,**arguments):
    address=json.loads((agent.tree/'relay/local_auth/control.json').read_text(encoding='utf-8'))
    port=address['port']
    if type(port) is not int or not 1<=port<=65535:raise ValueError('Invalid relay control address')
    request=urllib.request.Request(f'http://127.0.0.1:{port}/_relay/control',
        data=json.dumps(dict(op=op,**arguments)).encode(),
        headers={'X-Relay-Control':address['token'],'Content-Type':'application/json'})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect()).open(request,timeout=20) as response:
        return json.load(response)


def hot_settings(agent):
    config=load(agent.config);model=config.get('model') or {}
    return control(agent,'configure',settings={'defaults':{'model':model.get('model',''),
        'reasoning':model.get('reasoning_effort') or 'none'}})


def invoke(owner,command,args):
    agent=owner.current();home=agent.tree/'relay';home.mkdir(parents=True,exist_ok=True)
    slot=selected_slot(args)
    if command in ('relay_login','relay_logout','relay_switch','relay_cancel_login'):
        operation={'relay_login':'login','relay_logout':'logout','relay_switch':'switch','relay_cancel_login':'cancel_login'}[command]
        result=control(agent,operation,slot=slot)
        if command=='relay_login':return 'Открываю страницу входа ChatGPT. Ссылка доступна ниже, пока вход выполняется.'
        if command=='relay_switch':
            active=next((a['slot'] for a in result.get('accounts',[]) if a['active']),None)
            if active!=slot:raise ValueError('Relay did not confirm the selected subscription')
            return {'active_slot':active}
        return {'slot':slot,'stored':False,'phase':'applied'}
    if command in ('relay_status','relay_login_url','open_login_page','relay_account'):
        if command=='open_login_page':control(agent,'open_login');return None
        try:result=control(agent,'status')
        except OSError:
            if command!='relay_account':raise
            return {'slots':[{'slot':name,'stored':signature(home,name) is not None,'pending':False} for name in SLOTS],
                    'reachable':False}
        login=result.get('login') or {}
        if command=='relay_login_url':return result.get('login_url')
        accounts=result.get('accounts',[])
        if command=='relay_status':
            return 'pending' if login.get('pending') and login.get('slot')==slot else 'authorized' if any(a['slot']==slot and a['configured'] for a in accounts) else 'no-auth'
        return {'slots':[dict(slot=a['slot'],stored=a['configured'],serving=a['active'],loaded=True,
            pending=bool(login.get('pending') and login.get('slot')==a['slot']),login_error=login.get('error')) for a in accounts],
            'reachable':True,'port':result['port'],'active_slot':next((a['slot'] for a in accounts if a['active']),None)}
    if command=='relay_network_status':return control(agent,'network')
    if command=='relay_network_check':return control(agent,'network_check')
    if command=='relay_auth_transfer':
        if slot!='primary':raise ValueError('This server transfer endpoint supports only the primary slot')
        cfg=load(agent.config)
        if cfg.get('mode')!='remote':raise ValueError('Select a saved remote connection')
        base=str(cfg.get('base') or '').rstrip('/')
        parsed=urllib.parse.urlsplit(base)
        if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('localhost','127.0.0.1','::1')):raise ValueError('Transfer needs HTTPS or a local tunnel')
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Invalid transfer destination')
        key=cfg.get('key')
        if not key:raise ValueError('Saved server owner key missing')
        path=home/'local_auth/accounts/primary/auth.json'
        if not path.is_file():path=home/'local_auth/auth.json'
        raw=path.read_bytes()
        if len(raw)>128*1024:raise ValueError('Auth file too large')
        auth=json.loads(raw)
        if auth.get('OPENAI_API_KEY') or any(not (auth.get('tokens') or {}).get(k) for k in ('access_token','refresh_token','id_token')):raise ValueError('Full subscription login required')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        request=urllib.request.Request(base+'/api/relay/auth/import',data=json.dumps({'auth':auth,'replace':bool(args.get('replace'))}).encode(),headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        with urllib.request.build_opener(NoRedirect()).open(request,timeout=25) as response:
            row=json.loads(response.read(8193))
        if not re.fullmatch('[a-fA-F0-9]{32}',str(row.get('id',''))):raise ValueError('Transfer receipt missing')
        return {'id':row['id'],'phase':'queued','note':'Вход передан выбранному серверу; ждём применения'}
    raise ValueError('Unknown relay command')
