"""Saved server bridges, activated by the application owner for selected agents."""
from __future__ import annotations
import contextlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import server_connections as connections
from .private_files import write_private
from .server_bridge_runtime import Bridge,ports

FORMAT='helene-server-bridge.v1'


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


def save_existing(raw,agent):
    """Register an already configured server. No packages or remote files touched."""
    profile=raw.get('server_profile');checked=connections.inspect({'server_profile':profile})
    if not checked.get('ok'):return checked
    configuration=dict(raw.get('ports') or {})
    external=str(raw.get('external') or '').strip()
    if external:
        from .phone_access import stable_origin
        external=stable_origin({'phone':{'mode':'stable','external':external}}).rstrip('/')
        configuration['local_app_port']=agent.port
    elif configuration.get('remote_app_port'):
        raise ValueError('Для входа с телефона укажи постоянный HTTPS-адрес сервера.')
    configuration=ports(configuration)
    identity=secrets.token_hex(16)
    value={'format':FORMAT,'id':identity,'name':str(raw.get('name') or connections.read(profile)['name'])[:100],
           'server_profile':profile,'external':external,'ports':configuration,'phase':'configured'}
    write_private(path(identity),(json.dumps(value,ensure_ascii=False)+'\n').encode())
    return {'ok':True,'bridge':{k:v for k,v in value.items() if k!='format'},
            'message':'Подключение добавлено. Проверь прокси и HTTPS перед выбором в настройках.'}


def configuration(value,agent):
    selected=dict(value['ports'])
    if value.get('external'):selected['local_app_port']=agent.port
    return ports(selected)


def verify(bridge,value,agent,*,timeout=20):
    """Real outbound HTTP through the proxy, and exact public app identity."""
    end=time.monotonic()+timeout
    while bridge.state()['state'] in ('off','connecting') and time.monotonic()<end:time.sleep(.05)
    if bridge.state()['state']!='connected':raise ValueError(bridge.state()['message'])
    checks=[];selected=bridge.ports
    if selected.get('local_proxy_port'):
        from deskd.network_route import Route
        route=Route({'network':{'proxy':{'mode':'proxy','protocol':'http','host':'127.0.0.1','port':selected['local_proxy_port']}}})
        try:
            with route.open('https://api.telegram.org/',timeout=12) as response:code=response.status
        except urllib.error.HTTPError as response:
            code=response.code;response.close()
        except Exception:raise ValueError('SSH работает, но запрос через прокси не прошёл. Проверь tinyproxy и доступ сервера в интернет.') from None
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
                for identity in {(cfg.get(name) or {}).get('bridge_profile') for name in ('phone','network')} - {None,''}:
                    try:
                        value=read(identity);selected=configuration(value,agent)
                        phone=cfg.get('phone') or {};network=cfg.get('network') or {}
                        if phone.get('bridge_profile')!=identity or phone.get('mode')!='stable' or not phone.get('enabled'):
                            selected.pop('local_app_port',None);selected.pop('remote_app_port',None)
                        elif str(phone.get('external') or '').rstrip('/')!=value.get('external'):
                            raise ValueError('HTTPS-адрес отличается от сохранённого моста. Выбери подключение заново или отключи его привязку.')
                        if network.get('bridge_profile')!=identity:
                            selected.pop('local_proxy_port',None);selected.pop('remote_proxy_port',None)
                        else:
                            proxy=network.get('proxy') or {}
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
