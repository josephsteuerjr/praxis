"""Desktop commands implemented in Python; privileged operations stay explicit."""
from __future__ import annotations
import asyncio
import ctypes
import json
import ipaddress
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import webbrowser
import urllib.parse
import urllib.request

from .essential_backup import load
from . import config_cli


def probe_model(base_url, key="", framework="openai"):
    from .python_updates import secure_url
    base = str(base_url).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Model API base must not contain a query or fragment")
    anthropic = framework == "anthropic"
    url = base + ("/v1/models" if not parsed.path or (anthropic and not parsed.path.rstrip("/").endswith("/v1")) else "/models")
    headers = {"anthropic-version": "2023-06-01"} if anthropic else {}
    if str(key).strip():
        headers["x-api-key" if anthropic else "Authorization"] = str(key).strip() if anthropic else "Bearer " + str(key).strip()
    host = (parsed.hostname or "").lower()
    local = host == "localhost" or host.endswith(".local")
    try:
        address = ipaddress.ip_address(host)
        local = local or address.is_loopback or address.is_private or address in ipaddress.ip_network("100.64.0.0/10")
    except ValueError:
        pass
    if not (local and parsed.scheme == "http"):
        secure_url(base)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    handlers = [NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    http = urllib.request.build_opener(*handlers)
    try:
        with http.open(urllib.request.Request(url, headers=headers), timeout=15) as response:
            got = json.load(response)
        rows = got.get("data", []) if isinstance(got, dict) else []
        models = sorted({row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"].strip()}) if isinstance(rows, list) else []
        details = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict) or row.get('id') not in models:
                continue
            detail = {'id': row['id']}
            efforts = row.get('reasoning_efforts')
            if isinstance(efforts, list) and all(isinstance(value, str) for value in efforts):
                detail['reasoning_efforts'] = efforts
            default = row.get('default_reasoning_effort')
            if isinstance(default, str):
                detail['default_reasoning_effort'] = default
            tiers = row.get('service_tiers')
            if isinstance(tiers, list) and all(isinstance(value, dict) and isinstance(value.get('id'), str) for value in tiers):
                detail['service_tiers'] = [{key:value[key] for key in ('id','name','description') if isinstance(value.get(key),str)} for value in tiers]
            speeds = row.get('additional_speed_tiers')
            if isinstance(speeds, list) and all(isinstance(value, str) for value in speeds):
                detail['additional_speed_tiers'] = speeds
            details.append(detail)
        return {"ok": bool(models), "models": models, "model_details": details,
                "note": "Список моделей получен; генерация и доступ к выбранной модели не проверялись" if models else "Список моделей пуст или недоступен; это не проверка генерации"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "models": [], "note": str(exc)}


def invoke(owner, command, args):
    if command=='restore_upstream':
        if args.get('confirmed') is not True:raise ValueError('Подтверди возврат к upstream-коду')
        from . import upstream_recovery
        if upstream_recovery.modern(owner.root):
            from .desktop_update_request import admit_restore
            return admit_restore(owner,confirmed=True)
        return upstream_recovery.launch(owner.root)
    agent=owner.current()
    cfg=load(agent.config)
    if command=='window_retire_ack': return {'ok':True}
    if command in ('broker_ping','broker_request','broker_resume','install_service','remove_service','service_state','admin_state'):
        raise RuntimeError('Брокер и привилегированная служба недоступны в Python-поставке Hélène.')
    if command=='app_info':
        from .python_updates import version
        from .python_platform import desktop_identity
        return {'version':version(owner.root),'exe_dir':str(owner.root),'root':str(owner.root),
                'log':str(agent.tree/'deskapp.log'),**desktop_identity()}
    if command=='cleanup_status':
        from .cleanup_notice import status
        return status(owner.root)
    if command=='owner_state':
        from . import owner_stop
        try:state=owner.status().get('agents',{}).get(agent.id,{})
        except RuntimeError as error:state={'problems':{'engine':str(error)}}
        return {'agent_id':agent.id,'supported':True,'stopped':owner_stop.stopped(),
                'runner_alive':'runner' in state.get('roles',[]),'pid':state.get('pids',{}).get('runner'),
                'enabled':agent.enabled,'service':False,
                'note':owner.error or '; '.join(dict.fromkeys(state.get('problems',{}).values()))}
    if command=='owner_control':
        from . import owner_stop
        if args.get('action')=='panic':
            owner_stop.latch_client('python-window','owner emergency stop')
            owner.close()
            if sys.platform == 'darwin':
                from .darwin_process import RESIDUAL
                return 'Остановлено. Наблюдаемая принадлежавшая группа очищена. ' + RESIDUAL
            return 'Остановлено. Очистка принадлежавших процессов подтверждена.'
        if args.get('action')=='resume':
            from . import server_transfer
            if server_transfer.saved_transfer(agent.config).get('phase') not in (None,'server-stopped'):
                raise RuntimeError('Сначала останови серверную копию через восстановление переноса')
            if owner.transfer_lock.locked():raise RuntimeError('Server transfer is in progress')
            if not owner.resume_lock.acquire(blocking=False):raise RuntimeError('Возобновление уже выполняется')
            from . import owner_resume,startup_diagnostics
            try:
                removed=owner_resume.clear(cancelled=lambda:getattr(owner,'exiting',False))
                deadline=time.monotonic()+30
                owner.start()
                proof=owner.readiness(timeout=30,require_runner=True,
                    cancelled=lambda:getattr(owner,'exiting',False) or owner_stop.stopped())
                if cfg.get('mode','local')=='local' and (cfg.get('relay') or {}).get('enabled'):
                    while True:
                        if getattr(owner,'exiting',False) or owner_stop.stopped():raise InterruptedError('Возобновление отменено владельцем')
                        state=owner.status().get('agents',{}).get(agent.id,{})
                        problem=state.get('problems',{}).get('relay')
                        if problem:raise RuntimeError('Реле не запустилось: '+str(problem))
                        if 'relay' in state.get('roles',[]) and state.get('pids',{}).get('relay'):
                            proof={**proof,'relay_pid':state['pids']['relay']};break
                        if time.monotonic()>=deadline:raise TimeoutError('Реле не подтвердило запуск')
                        time.sleep(.1)
                startup_diagnostics.owner_result(owner.root,agent.tree,'resume',proof=proof,removed=removed)
                return 'Движок запущен.'
            except Exception as error:
                try:startup_diagnostics.owner_result(owner.root,agent.tree,'resume',error=error)
                except Exception:pass
                raise
            finally:owner.resume_lock.release()
        raise ValueError('Unknown owner action')
    if command in ('open_path','reveal_path'):
        value=str(args['path'])
        if command=='reveal_path':
            target=Path(value)
            if not target.is_absolute():target=owner.root/target
            if os.name=='nt':subprocess.Popen(['explorer.exe','/select,',str(target.resolve())])
            else:webbrowser.open(target.parent.as_uri())
        elif value.startswith(('https://','http://')):webbrowser.open(value)
        elif os.name=='nt':os.startfile(str(Path(value).resolve()))
        else:webbrowser.open(Path(value).resolve().as_uri())
        return None
    if command=='lan_ip':
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
            try:sock.connect(('192.0.2.1',9));return sock.getsockname()[0]
            except OSError:return None
    if command=='tailscale_ip':
        binary=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Tailscale/tailscale.exe'
        if not binary.is_file():return None
        from .update_process import background_flags
        result=subprocess.run([str(binary),'ip','-4'],capture_output=True,timeout=5,
                              creationflags=background_flags())
        return result.stdout.decode().strip().splitlines()[0] if result.returncode==0 and result.stdout else None
    if command.startswith('update_'):
        from . import python_updates
        return getattr(python_updates,command)(owner,**args)
    if command.startswith('relay_') or command=='open_login_page':
        from . import python_relay
        return python_relay.invoke(owner,command,args)
    if command=='remote_computer_pair':
        from .remote_computer_client import pair
        before=config_cli.mtime_ns(agent.config)
        seen=args.get('mtime_ns',args.get('mtimeNs'))
        if seen!=before:raise ValueError('Settings changed; reload before pairing')
        receipt=asyncio.run(pair(agent.config,args['scopes'],args['system'],seen))
        receipt.update(path=str(agent.config),before_mtime_ns=before,mtime_ns=config_cli.mtime_ns(agent.config))
        owner.sync_publishers()
        return receipt
    if command=='remote_computer_disable':
        seen=args.get('mtime_ns',args.get('mtimeNs'))
        if config_cli.mtime_ns(agent.config)!=seen:raise ValueError('Settings changed; reload before disabling')
        if cfg.get('mode')!='remote':raise ValueError('Not a remote connection')
        remote=cfg['computer']['remote'];remote['enabled']=False
        result=config_cli.save(agent.config,json.dumps(cfg),seen)
        if result.get('ok'):owner.sync_publishers()
        return dict(result,id=remote.get('id'),path=str(agent.config),before_mtime_ns=seen)
    if command=='server_transfer':
        from . import server_transfer, remote_locator
        payload=json.loads(args.get('request','{}'))
        action=args['action']
        if action=='probe':return server_transfer.probe(payload,agent.config)
        if action=='status':return server_transfer.status(agent.config)
        if action=='locate':return {'ok':True,'base':remote_locator.locate(cfg)}
        if action=='telegram-check':return server_transfer.check_telegram(cfg)
        if action in ('run','recover-local'):
            if Path(str(payload.get('expected_config',''))).resolve()!=agent.config.resolve():
                raise ValueError('Selected agent changed; reload server transfer')
            if owner.status().get('agents',{}).get(agent.id,{}).get('roles'):
                raise RuntimeError('Stop the local agent before server transfer')
            if not owner.transfer_lock.acquire(blocking=False):raise RuntimeError('Server transfer already in progress')
            try:return server_transfer.run(payload,agent.config,owner.root) if action=='run' else server_transfer.recover_local(payload,agent.config)
            finally:owner.transfer_lock.release()
        raise ValueError('Unknown transfer action')
    if command=='server_install':
        # 11.11 (Yegor): install a new server agent and update an installed one from the
        # window; no manual scripts. A new agent becomes its own remote profile; the local
        # agent and its memory are not touched.
        from . import server_install, server_transfer
        payload=json.loads(args.get('request','{}'))
        action=args['action']
        if action=='probe':return server_transfer.probe(payload)
        if action=='status':
            row=server_install.saved(agent.config)
            return {'pending':row.get('phase') in ('prepared','installing','installed'),'phase':row.get('phase'),
                    'host':(row.get('destination') or [None])[0],'deployment':row.get('deployment'),'name':row.get('name','')}
        if action in ('install','connect','update'):
            if not owner.transfer_lock.acquire(blocking=False):raise RuntimeError('Server operation already in progress')
            try:
                if action=='update':return server_install.update(payload,owner.root)
                if action=='install':
                    result=server_install.install_new(payload,agent.config,owner.root)
                    if result.get('trust_needed') or not result.get('ok'):return result
                    row=server_install.saved(agent.config)
                    row.update(key=result['key'],telegram=result['telegram'],name=result['name'])
                    server_install.save(agent.config,row)
                row=server_install.saved(agent.config)
                if row.get('phase')!='installed' or not row.get('key'):raise ValueError('Нет установленного сервера, ожидающего подключения')
                base=server_install.locate(row['key'],row['telegram'],cfg)
                from . import agents
                created=agents.create(owner.root,row.get('name') or 'Агент на сервере',brain_from_base=False)
                remote=agents.read_config(created.config)
                proxy=(cfg.get('telegram') or {}).get('proxy')
                remote.update(mode='remote',base=base,key=row['key'],server_locator='telegram-menu',
                              telegram={**row['telegram'],**({'proxy':proxy} if proxy else {})})
                agents._write_config(created.config,remote)
                owner.refresh()
                row.update(phase='ready',agent_id=created.id);row.pop('key',None);row.pop('telegram',None)
                server_install.save(agent.config,row)
                return {'ok':True,'agent_id':created.id,'base':base,'version':row.get('version')}
            finally:owner.transfer_lock.release()
        raise ValueError('Unknown server install action')
    if command=='carry_export':
        from . import carry
        return carry.export(agent.config)['archive']
    if command=='probe_model':
        return probe_model(args['baseUrl'], args.get('key', ''), args.get('framework', 'openai'))
    if command in ('autostart_get','autostart_set','service_state','admin_state','install_service','remove_service','firewall_allow','firewall_clear','notify','open_privacy_pane'):
        if os.name == 'nt':
            from . import python_windows
            return python_windows.invoke(owner,command,args)
        from . import python_posix
        return python_posix.invoke(owner,command,args)
    if command=='telegram_account':
        env=dict(os.environ)
        for name,key in [('API_ID','apiId'),('API_HASH','apiHash'),('PHONE','phone'),('CODE','code'),('PASSWORD','password')]:
            env['HELENE_TG_'+name]=str(args.get(key,''))
        return owner.helper([sys.executable,str(owner.root/'app/localharness/mtproto_login.py'),
            '--session',str(agent.tree/'telegram/account'),'--config',str(agent.config),args['step']],env=env,timeout=90)
    if command=='component_install':
        from .component_install_job import start
        return start(owner,str(args.get('name') or ''))
    if command=='voice_fetch':
        from .voice import CATALOG
        flag='--get-voice' if args.get('kind')=='speak' else '--get'
        if args.get('model') not in ({'xtts'} if flag=='--get-voice' else CATALOG):raise ValueError('Unknown voice model')
        key=(str(agent.tree.resolve()),flag);children=getattr(owner,'voice_jobs',{})
        previous=children.get(key)
        if previous is not None and previous.handle is not None:
            if previous.poll() is None:return 'Загрузка уже выполняется; состояние в карточке голоса.'
            previous.stop()
        children[key]=owner.background([sys.executable,str(owner.root/'app/localharness/voice.py'),'--tree',str(agent.tree),flag,args['model']],
            agent.tree/'voice-download.log',dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1'))
        owner.voice_jobs=children
        return 'Загрузка компонента началась; состояние появится в карточке голоса.'
    if command=='logs_bundle':
        from .python_support import bundle
        return str(bundle(owner.root,agent))
    raise ValueError('Unknown Python desktop command: '+command)
