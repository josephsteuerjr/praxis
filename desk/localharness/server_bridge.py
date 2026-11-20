"""Saved server bridges, activated by the application owner for selected agents."""
from __future__ import annotations
import contextlib
import json
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import server_connections as connections
from .private_files import write_private
from .server_bridge_runtime import Bridge,ports

FORMAT='helene-server-bridge.v1'
# Config sections that may route through a saved bridge.
CONSUMERS=('phone','network','relay')


class BridgeCheckError(ValueError):
    def __init__(self,message,code):
        super().__init__(message)
        self.code=code


def component():
    marker=connections.root()/'bridge-component.json'
    return {'id':'server-bridge','ready':True,'installed':marker.is_file(),'restart_required':False}


def install():
    write_private(connections.root()/'bridge-component.json',json.dumps({'format':FORMAT}).encode())
    return {'ok':True,'state':'completed','restart_required':False}


def path(identity):
    return connections._path(identity).parent/'bridges'/(identity+'.json')


def read(identity):
    value=json.loads(path(identity).read_text(encoding='utf-8'))
    if value.get('format')!=FORMAT or value.get('id')!=identity:raise ValueError('Запись моста повреждена; прежние данные сохранены.')
    return value


def listing():
    folder=connections.root()/'bridges'
    result=[]
    if folder.exists():
        for item in sorted(folder.glob('*.json')):
            try:
                value=read(item.stem)
                result.append({key:value[key] for key in ('id','name','server_profile','external','ports','phase') if key in value})
            except (OSError,ValueError):result.append({'id':item.stem,'name':'Мост требует проверки','phase':'error'})
    return result


AUTO_PORTS=range(18888,19000)


def port_free(port):
    """Bind exactly as the bridge will; a live foreign listener refuses."""
    probe=socket.socket()
    try:
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        else:probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        probe.bind(('127.0.0.1',port));return True
    except OSError:return False
    finally:probe.close()


def free_local_port(preferred=None,*,exclude=()):
    """Explicit port must be free; otherwise take one no saved bridge promised.

    8888 is where Tray Bridge and manual tunnels usually live, so the automatic
    range starts elsewhere and never displaces them.
    """
    taken={row.get('ports',{}).get('local_proxy_port') for row in listing()}|set(exclude)
    if preferred not in (None,'',0,'0'):
        port=ports({'local_proxy_port':preferred,'remote_proxy_port':1})['local_proxy_port']
        if port in taken or not port_free(port):
            raise BridgeCheckError(f'Локальный порт {port} уже занят: там, вероятно, Tray Bridge или другой туннель. '
                                   'Оставь поле пустым, и Hélène выберет свободный порт сама.','local_port_unavailable')
        return port
    for port in AUTO_PORTS:
        if port not in taken and port_free(port):return port
    raise BridgeCheckError('Свободный локальный порт 18888–18999 не найден.','local_port_unavailable')


def probe_local_proxy(port):
    """Does something on 127.0.0.1:port actually pass HTTPS (Tray Bridge, a tunnel)?

    Only a CONNECT request line goes out; no TLS, credentials or payload.
    """
    port=ports({'local_proxy_port':port,'remote_proxy_port':1})['local_proxy_port']
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=3) as peer:
            peer.settimeout(15)
            peer.sendall(b'CONNECT api.telegram.org:443 HTTP/1.1\r\nHost: api.telegram.org:443\r\n\r\n')
            head=peer.recv(256)
    except ConnectionRefusedError:return {'ok':False,'state':'absent','port':port}
    except OSError:return {'ok':False,'state':'silent','port':port}
    parts=head.split(b'\r\n',1)[0].split()
    status=int(parts[1]) if len(parts)>1 and parts[0].startswith(b'HTTP/') and parts[1].isdigit() else None
    return {'ok':status==200,'state':'proxy' if status==200 else 'other','status':status,'port':port}


def save_existing(raw,agent):
    """Register an already configured server. No packages or remote files touched.

    With verify, a temporary bridge proves proxy/HTTPS first and nothing is
    written on failure, so a wrong port or route does not leave a dead record.
    """
    profile=raw.get('server_profile');checked=connections.inspect({'server_profile':profile})
    if not checked.get('ok'):return checked
    selected=dict(raw.get('ports') or {})
    external=str(raw.get('external') or '').strip()
    if external:
        from .phone_access import stable_origin
        external=stable_origin({'phone':{'mode':'stable','external':external}}).rstrip('/')
        selected['local_app_port']=agent.port
    elif selected.get('remote_app_port'):
        raise ValueError('Для входа с телефона укажи постоянный HTTPS-адрес сервера.')
    if selected.get('remote_proxy_port') or 'local_proxy_port' in selected:
        selected['local_proxy_port']=free_local_port(selected.get('local_proxy_port'))
    selected=ports(selected)
    identity=secrets.token_hex(16)
    value={'format':FORMAT,'id':identity,'name':str(raw.get('name') or connections.read(profile)['name'])[:100],
           'server_profile':profile,'external':external,'ports':selected,'phase':'configured'}
    checks=[]
    if raw.get('verify'):
        bridge=Bridge(connections.resolve({'server_profile':profile}),configuration(value,agent))
        bridge.start()
        try:checks=verify(bridge,value,agent)['checks']
        finally:bridge.stop()
    write_private(path(identity),(json.dumps(value,ensure_ascii=False)+'\n').encode())
    return {'ok':True,'bridge':{k:v for k,v in value.items() if k!='format'},'checks':checks,
            'message':'Мост проверен и сохранён.' if checks else 'Подключение добавлено. Проверь прокси и HTTPS перед выбором в настройках.'}


def selected_by(identity,owner):
    """Agents whose saved settings route through this bridge."""
    from . import agents
    names=[]
    for agent in agents.roster(owner.root):
        cfg=agents.read_config(agent.config)
        if identity in {(cfg.get(name) or {}).get('bridge_profile') for name in CONSUMERS}:
            names.append(str(getattr(agent,'name','') or getattr(agent,'id','') or agent.config))
    return names


def _in_use(identity,owner):
    used=selected_by(identity,owner)
    if used:raise ValueError('Этот мост выбран у агента «'+used[0]+'». Сначала выбери другой маршрут в «Сеть и прокси» и сохрани настройки.')


def _stop(manager,identity):
    with manager.lock:
        current=manager.bridges.pop(identity,None);manager.errors.pop(identity,None)
        if current:current[0].stop()


def remove(owner,manager,identity):
    """Forget a bridge on this computer. Server services and files stay."""
    read(identity);_in_use(identity,owner);_stop(manager,identity)
    path(identity).unlink()
    shutil.rmtree(path(identity).parent/identity,ignore_errors=True)
    return {'ok':True,'message':'Мост удалён с этого компьютера. На сервере ничего не менялось.'}


def repair_port(owner,manager,identity):
    """Move an unused bridge off a local port that another program now holds."""
    value=read(identity);_in_use(identity,owner)
    old=value['ports'].get('local_proxy_port')
    if not old:raise ValueError('У этого моста нет локального прокси.')
    _stop(manager,identity)
    value['ports']={**value['ports'],'local_proxy_port':free_local_port(exclude={old})}
    write_private(path(identity),(json.dumps(value,ensure_ascii=False)+'\n').encode())
    port=value['ports']['local_proxy_port']
    return {'ok':True,'bridge':{k:v for k,v in value.items() if k!='format'},
            'message':f'Мост перенесён на свободный локальный порт {port}. Проверь соединение.'}


def configuration(value,agent):
    selected=dict(value['ports'])
    if value.get('external'):selected['local_app_port']=agent.port
    return ports(selected)


def verify(bridge,value,agent,*,timeout=20):
    """Real outbound HTTP through the proxy, and exact public app identity."""
    end=time.monotonic()+timeout
    while bridge.state()['state'] in ('off','connecting') and time.monotonic()<end:time.sleep(.05)
    if bridge.state()['state']!='connected':
        state=bridge.state()
        raise BridgeCheckError(state['message'],state.get('error_code','ssh_unavailable'))
    checks=[];selected=bridge.ports
    if selected.get('local_proxy_port'):
        from deskd.network_route import Route
        route=Route({'network':{'proxy':{'mode':'proxy','protocol':'http','host':'127.0.0.1','port':selected['local_proxy_port']}}})
        for attempt in (1,2):
            # Telegram answers 302 here; any HTTP answer proves the CONNECT path.
            try:
                with route.open('https://api.telegram.org/',timeout=12) as response:code=response.status
                break
            except urllib.error.HTTPError as response:
                code=response.code;response.close();break
            except Exception:
                if attempt==2:raise ValueError('SSH работает, но запрос через прокси не прошёл. Проверь tinyproxy и доступ сервера в интернет.') from None
                time.sleep(1)
        if not 200<=code<400:raise ValueError('Прокси не подтвердил выход в Telegram (HTTP '+str(code)+').')
        checks.append({'id':'proxy','ok':True,'message':'HTTPS-запрос в Telegram прошёл через этот прокси.'})
    if value.get('external'):
        from urllib.parse import urlsplit
        from .server_probe import issue
        direct=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        from deskd.network_route import Route
        try:
            with direct.open(f'http://127.0.0.1:{agent.port}/api/phone/probe',timeout=8) as response:
                local=json.loads(response.read(32769))
            with issue(agent.tree,urlsplit(value['external']).hostname) as headers:
                request=urllib.request.Request(value['external']+'/api/phone/probe',headers=headers)
                with Route({'network':{'proxy':{'mode':'direct'}}}).open(request,timeout=12) as response:
                    public=json.loads(response.read(32769))
        except Exception:raise ValueError('Публичный HTTPS ещё не отвечает. Проверь домен, сертификат и обратный туннель.') from None
        if not local.get('instance') or public.get('instance')!=local['instance']:
            raise ValueError('HTTPS-адрес ведёт в другую копию Hélène. Подключение не подтверждено.')
        checks.append({'id':'https','ok':True,'message':'HTTPS и сертификат проверены; адрес ведёт к этому агенту.'})
    return {'ok':True,'checks':checks}


class Manager:
    def __init__(self,owner):self.owner=owner;self.bridges={};self.errors={};self.lock=threading.RLock()

    def sync(self):
        with self.lock:self._sync()

    def _sync(self):
        from . import agents
        desired={};self.errors={}
        if component()['installed']:
            for agent in agents.roster(self.owner.root):
                if not agent.enabled or agent.conflict:continue
                cfg=agents.read_config(agent.config)
                if cfg.get('mode')=='remote':continue
                # A bridge serves its local proxy while anyone routes through it:
                # Telegram and Mini App (network), the ChatGPT relay, or both.
                for identity in {(cfg.get(name) or {}).get('bridge_profile') for name in CONSUMERS} - {None,''}:
                    try:
                        value=read(identity);selected=configuration(value,agent)
                        phone=cfg.get('phone') or {}
                        if phone.get('bridge_profile')!=identity or phone.get('mode')!='stable' or not phone.get('enabled'):
                            selected.pop('local_app_port',None);selected.pop('remote_app_port',None)
                        elif str(phone.get('external') or '').rstrip('/')!=value.get('external'):
                            raise ValueError('HTTPS-адрес отличается от сохранённого моста. Выбери подключение заново или отключи его привязку.')
                        routes=[name for name in ('network','relay') if (cfg.get(name) or {}).get('bridge_profile')==identity]
                        if not routes:
                            selected.pop('local_proxy_port',None);selected.pop('remote_proxy_port',None)
                        for name in routes:
                            proxy=(cfg.get(name) or {}).get('proxy') or {}
                            if (proxy.get('mode'),proxy.get('protocol'),proxy.get('host'),str(proxy.get('port'))) != ('proxy','http','127.0.0.1',str(selected.get('local_proxy_port'))) or proxy.get('username') or proxy.get('password'):
                                raise ValueError('Маршрут прокси отличается от сохранённого моста. Выбери подключение заново или отключи его привязку.')
                        if not selected:continue
                        if identity in desired:
                            previous=desired[identity][1]
                            if any(previous[key]!=selected[key] for key in previous.keys()&selected.keys()):
                                raise ValueError('Один HTTPS-мост выбран для двух разных агентов. Создай каждому отдельный адрес.')
                            selected={**previous,**selected}
                        desired[identity]=(value,selected)
                    except (OSError,ValueError) as error:self.errors[identity]=str(error)
        for identity,(bridge,selected) in list(self.bridges.items()):
            if identity not in desired or identity in self.errors or desired[identity][1]!=selected:
                bridge.stop();self.bridges.pop(identity)
        for identity,(value,selected) in desired.items():
            if identity in self.bridges or identity in self.errors:continue
            try:
                raw=connections.resolve({'server_profile':value['server_profile']})
                bridge=Bridge(raw,selected,state_dir=path(identity).parent/identity)
                self.bridges[identity]=(bridge,selected);bridge.start()
            except (OSError,ValueError) as error:self.errors[identity]=str(error)

    def state(self):
        with self.lock:return {**{identity:bridge.state() for identity,(bridge,_) in self.bridges.items()},
                **{identity:{'state':'error','message':error} for identity,error in self.errors.items()}}

    def close(self):
        with self.lock:self._close()

    def _close(self):
        failures=[]
        for identity,(bridge,_) in list(self.bridges.items()):
            try:bridge.stop();self.bridges.pop(identity)
            except RuntimeError as error:failures.append(str(error))
        if failures:raise RuntimeError('; '.join(failures))


def invoke(owner,action,raw):
    agent=owner.current()
    if action in ('operations','prepare','install'):
        from . import server_bridge_install
        if action=='operations':return server_bridge_install.status()
        if not component()['installed']:raise ValueError('Сначала установи компонент «Серверный мост» в разделе программы.')
        if action=='prepare':return server_bridge_install.prepare(raw,agent)
        return server_bridge_install.start(owner,raw)
    with owner.lock:
        if not hasattr(owner,'server_bridges'):owner.server_bridges=Manager(owner)
        manager=owner.server_bridges
    if action=='list':return {'bridges':listing(),'states':manager.state(),'component':component()}
    if action=='add-existing':return save_existing(raw,agent)
    if action=='remove':return remove(owner,manager,raw.get('id'))
    if action=='repair-port':return repair_port(owner,manager,raw.get('id'))
    if action=='retry':
        with manager.lock:
            current=manager.bridges.get(raw.get('id'))
            if current:current[0].retry()
            else:manager.sync()
        return {'ok':True}
    if action=='check':
        with manager.lock:
            value=read(raw.get('id'));current=manager.bridges.get(value['id']);wanted=configuration(value,agent)
            checks=[]
            if current:
                active=current[0].ports
                if any(active[key]!=wanted[key] for key in active.keys()&wanted.keys()):
                    raise ValueError('Этот HTTPS-мост уже обслуживает другого агента. Создай отдельное подключение.')
                active_value=value if active.get('local_app_port') else {**value,'external':''}
                checks.extend(verify(current[0],active_value,agent)['checks'])
                wanted={key:port for key,port in wanted.items() if key not in active}
                if not wanted:return {'ok':True,'checks':checks}
            # A partly selected profile may already own its proxy port. Verify
            # only missing forwards temporarily; never replace the live route.
            bridge=Bridge(connections.resolve({'server_profile':value['server_profile']}),wanted,
                          state_dir=None if current else path(value['id']).parent/value['id'])
            bridge.start()
            try:
                extra_value=value if wanted.get('local_app_port') else {**value,'external':''}
                checks.extend(verify(bridge,extra_value,agent)['checks']);return {'ok':True,'checks':checks}
            finally:bridge.stop()
    raise ValueError('Unknown server bridge action')
