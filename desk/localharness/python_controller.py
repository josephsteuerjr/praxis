"""Python-owned desktop control: no helene-host subprocess or native retry loop."""
from __future__ import annotations
import json
import os
from pathlib import Path
from queue import Queue
import secrets
import threading
import time

from . import agents, config_cli
from .essential_backup import load


class Controller:
    def __init__(self, root: Path, event, origin='', agent=''):
        self.root = root.resolve()
        self.event = event
        self.origin = origin
        self.selected = agent or self._default()
        self.thread = None
        self.stop = threading.Event()
        self.commands = Queue()
        self.nonce = secrets.token_hex(16)
        self.error = None
        self.lock = threading.RLock()
        self.helpers = []
        self.publishers = {}
        self.transfer_lock=threading.Lock()

    def _default(self):
        try:
            value = json.loads((self.root/'agent-default.json').read_text(encoding='utf-8'))
            found = agents.find(self.root, value['id'])
            if found and found.enabled:
                return found.id
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return agents.BASE_ID

    def current(self):
        found = agents.find(self.root, self.selected)
        if found is None:
            raise ValueError('Selected agent no longer exists')
        return found

    def channel(self):
        agent = self.current()
        cfg = agents.read_config(agent.config)
        common = {'agent': agent.name, 'agent_id': agent.id, 'product':'Hélène',
                  'agents':[a.as_dict() for a in agents.roster(self.root)]}
        if cfg.get('mode') == 'remote':
            return dict(common, base=cfg.get('base',''), key=cfg.get('key',''),
                        local_agent=False, needs_remote=not cfg.get('base'),
                        discover_server=cfg.get('server_locator') == 'telegram-menu')
        from .desktop_supervisor import _token
        return dict(common, base=f'http://127.0.0.1:{agent.port}', key=_token(agent.tree),
                    needs_local_setup=cfg.get('setup_complete') is False)

    def start(self):
        """Jobs live in this Python process: process death closes every Job handle."""
        with self.lock:
            from . import owner_stop
            # Portable delivery: inherit the absent broker in every child.
            os.environ['HELENE_BROKER_DISABLED'] = '1'
            if owner_stop.stopped():
                return  # keep the control window available; only explicit Resume clears Stop
            if any(child.handle is not None and getattr(child, 'custody_uncertain', False)
                   for child in self.helpers):
                raise RuntimeError('Earlier child custody remains unconfirmed: ' + str(self.error))
            if self.thread and self.thread.is_alive():
                return
            if agents.read_config(self.root/'helene.json').get('mode') == 'remote':
                self.stop=threading.Event()
                self.sync_publishers()
                return
            self.stop = threading.Event()
            self.commands = Queue()
            self.nonce = secrets.token_hex(16)
            self.error = None
            def own():
                try:
                    from . import desktop_supervisor
                    desktop_supervisor.run(self.root/'helene.json', stop=self.stop,
                        desktop_roster=True, status_nonce=self.nonce, commands=self.commands)
                except BaseException as exc:
                    # Transfer the same uncertain handles, not just their text.
                    # Do not take self.lock: close() may be joining this thread.
                    for child in getattr(exc, 'children', ()):
                        child.custody_uncertain = True
                        if child.handle is not None and child not in self.helpers:
                            self.helpers.append(child)
                    self.error = str(exc)
                    self.event('engine-error', {'error':self.error})
            if self.origin:
                os.environ['HELENE_ORIGINS'] = self.origin
            self.thread = threading.Thread(target=own, name='helene-python-owner', daemon=True)
            self.thread.start()
            self.sync_publishers()

    def status(self):
        if self.error:
            raise RuntimeError(self.error)
        if not self.thread or not self.thread.is_alive():
            return {'agents':{}}
        tree = agents.roster(self.root)[0].tree
        try:
            value = json.loads((tree/'.python-desktop-status').read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise RuntimeError('Python owner status not yet available') from exc
        if value.get('nonce') != self.nonce:
            raise RuntimeError('Python owner generation differs')
        return value

    def refresh(self):
        if not self.thread or not self.thread.is_alive():
            self.sync_publishers()
            return
        if agents.read_config(self.root/'helene.json').get('mode')=='remote':
            self.sync_publishers()
            return
        identity = secrets.token_hex(16)
        self.commands.put(identity)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                if self.status().get('ack') == identity:
                    self.sync_publishers()
                    return
            except RuntimeError:
                if self.error: raise
            time.sleep(.1)
        raise TimeoutError('Configuration saved; process refresh is unconfirmed. Inspect before retrying.')

    def sync_publishers(self):
        """Remote desktop export is Python-owned too, without a local brain."""
        import sys
        desired={}
        for agent in agents.roster(self.root):
            cfg=load(agent.config)
            if cfg.get('mode')=='remote' and agent.enabled and not agent.conflict:
                if ((cfg.get('computer') or {}).get('remote') or {}).get('enabled') is True:
                    desired[agent.id]=agent
        for identity,child in list(self.publishers.items()):
            if identity not in desired or child.poll() is not None:
                child.stop();self.helpers.remove(child);del self.publishers[identity]
        for identity,agent in desired.items():
            if identity in self.publishers:continue
            argv=[sys.executable,str(self.root/'app/localharness/remote_computer_client.py'),
                  'run','--config',str(agent.config),'--data',str(agent.tree)]
            self.publishers[identity]=self.background(argv,agent.tree/'remote-computer.log')

    def readiness(self, timeout=30, require_runner=False, agent=None):
        """Identify anonymously before sending a tree key; measure owned runner."""
        import urllib.request
        import urllib.error
        import urllib.parse
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        http=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        target=agent or self.current()
        cfg=load(target.config)
        if cfg.get('mode')=='remote':
            from .python_updates import request_json
            base=str(cfg.get('base','')).rstrip('/')
            value=request_json(base+'/api/home?key='+urllib.parse.quote(str(cfg.get('key',''))))
            if not isinstance(value,dict) or not value.get('tree'):raise ValueError('Remote channel identity unavailable')
            return {'remote':True,'base':cfg.get('base'),'connected':True}
        from .desktop_supervisor import _token
        base=f'http://127.0.0.1:{target.port}'
        end=time.monotonic()+timeout
        last='channel is starting'
        while True:
            try:
                state=self.status().get('agents',{}).get(target.id,{})
                who=json.load(http.open(base+'/api/who',timeout=2))
                if who.get('product')!='Hélène' or Path(who.get('root','')).resolve()!=target.config.parent.resolve():
                    raise ValueError('Foreign process owns the selected channel port')
                home=json.load(http.open(base+'/api/home?key='+urllib.parse.quote(_token(target.tree)),timeout=2))
                if Path(home.get('tree','')).resolve()!=target.tree.resolve():
                    raise ValueError('Channel data identity differs')
                if not state.get('raised') or who.get('pid')!=state.get('pids',{}).get('channel'):
                    raise RuntimeError('Channel is not owned by this Python generation')
                proof={'id':target.id,'tree':str(target.tree.resolve()),'channel_pid':who['pid']}
                from .desktop_supervisor import runner_required
                if require_runner and runner_required(cfg, target.id):
                    reader=load(target.tree/'memory/.control/desk_inbox/.reader.json')
                    code=Path(str(cfg.get('code') or '../../live'))
                    code=(code if code.is_absolute() else target.config.parent/code).resolve()
                    if reader.get('pid')!=state.get('pids',{}).get('runner') or time.time()-reader.get('at',0)>15:
                        raise RuntimeError('Runner ownership heartbeat unconfirmed')
                    if Path(reader.get('code','')).resolve()!=code:
                        raise ValueError('Runner did not import the preserved source')
                    proof.update(runner_pid=reader['pid'],code=str(code))
                return proof
            except (OSError, RuntimeError) as exc:
                last=str(exc)
            if time.monotonic()>=end: raise TimeoutError(last)
            time.sleep(.2)

    def close(self):
        from .desktop_supervisor import stop_children
        with self.lock:
            self.stop.set()  # also wakes the primary supervisor's own fanout
            failures = []
            def stop_helpers():
                snapshot = tuple(self.helpers)
                try:
                    stop_children({f'helper:{i}': c for i, c in enumerate(snapshot)
                                   if c.handle is not None})
                except (OSError, RuntimeError) as exc:
                    failures.append(str(exc))
                # Keep unknown handles even if a concurrent helper returned.
                self.helpers[:] = list(dict.fromkeys(
                    c for c in (*self.helpers, *snapshot) if c.handle is not None))
            stop_helpers()
            if self.thread:
                self.thread.join(15)
                if self.thread.is_alive():
                    failures.append('Python child shutdown unconfirmed')
                else:
                    self.thread = None
                    stop_helpers()  # may have received the supervisor's faults
                if self.error:
                    failures.append(self.error)
            if failures:
                self.error = '; '.join(dict.fromkeys(failures))
                raise RuntimeError(self.error)
            self.publishers.clear()

    def background(self, argv, log, env=None):
        from .desktop_supervisor import JobChild, CustodyFault
        from . import owner_stop
        with self.lock:
            if any(c.handle is not None and getattr(c, 'custody_uncertain', False)
                   for c in self.helpers):
                raise CustodyFault('Earlier helper admission remains unconfirmed')
            try:
                child=JobChild.spawn(argv,cwd=self.root,env=env or dict(os.environ),log=log,
                                     stopped=lambda:self.stop.is_set() or owner_stop.stopped())
            except CustodyFault as exc:
                for child in getattr(exc, 'children', ()):
                    child.custody_uncertain = True
                    if child.handle is not None and child not in self.helpers:
                        self.helpers.append(child)
                self.error = str(exc)
                raise
            self.helpers.append(child)
        return child

    def helper(self, argv, env=None, timeout=90):
        import tempfile
        with tempfile.TemporaryDirectory(prefix='helene-helper-') as temporary:
            log=Path(temporary)/'output'
            child=self.background(argv,log,env)
            try:
                deadline=time.monotonic()+timeout
                while time.monotonic()<deadline:
                    code=child.poll()
                    if code is not None:
                        if code:raise RuntimeError('Helper failed; inspect its component state')
                        return json.loads(log.read_text(encoding='utf-8').splitlines()[-1])
                    time.sleep(.1)
                raise TimeoutError('Helper did not finish')
            finally:
                try:
                    if child.handle is not None:
                        child.stop()
                except (OSError, RuntimeError) as exc:
                    child.custody_uncertain = True
                    self.error = str(exc)
                    raise
                finally:
                    if child.handle is None and child in self.helpers:
                        self.helpers.remove(child)

    def invoke(self, command, args):
        if command == 'update_progress':
            from .update_progress import status
            return status(self.root)
        agent = self.current()
        if command == 'config_load':
            return {'config':load(agent.config), 'path':str(agent.config),
                    'tree':str(agent.tree), 'exe_dir':str(self.root),
                    'agent_id':agent.id,'agent_name':agent.name,
                    'mtime_ns':config_cli.mtime_ns(agent.config)}
        if command == 'config_save':
            result = config_cli.save(agent.config,args['config'],args.get('mtimeNs'))
            if result.get('ok'):
                from . import boot, brain_trail
                saved=load(agent.config)
                code=Path(str(saved.get('code') or '../../live'))
                if not code.is_absolute():code=(agent.config.parent/code).resolve()
                try:
                    brain_trail.project(agent.tree,agent.config,saved,
                        lambda tree,cfg:boot.project_brain(tree,cfg,code_dir=code))
                except (OSError,ValueError,RuntimeError) as exc:
                    # The config save already committed. Report the un-applied
                    # projection rather than pretending the save never happened.
                    result['warning']='Settings saved; selected model consumer has not applied them: '+str(exc)
                self.refresh()
                if (load(agent.config).get('relay') or {}).get('enabled'):
                    from .python_relay import hot_settings
                    try:
                        hot_settings(agent)
                        result['relay']='Настройки реле применены без перезапуска'
                    except (OSError,ValueError) as exc:
                        result['warning']='Settings saved; relay has not applied them: '+str(exc)
                        result['relay']=result['warning']
            return result
        if command == 'engine_restart':
            self.close(); self.start()
            return {'ok':True}
        if command == 'agents_list':
            states=self.status().get('agents',{})
            return {'agents':[dict(a.as_dict(), current=a.id==self.selected,
                         default=a.id==self._default(),
                         raised=states.get(a.id,{}).get('raised',False))
                    for a in agents.roster(self.root)],'current':self.selected,'owner':'python'}
        if command == 'switch_agent':
            target=agents.find(self.root,args['id'])
            if not target or not target.enabled or target.conflict:
                raise ValueError('Agent unavailable')
            self.readiness(agent=target)
            self.selected=target.id
            self.event('replace-window',{'script':'window.DESK_CONFIG_OVERRIDE = '+json.dumps(self.channel())+';'})
            return {'ok':True,'id':target.id,'name':target.name}
        if command == 'agent_default_set':
            target=agents.set_default(self.root,args['id'])
            return {'ok':True,'id':target.id,'name':target.name}
        if command == 'agent_add':
            soul=args.get('soul') or {'kind':args.get('soulKind','canonical'),'text':args.get('soulText','')}
            result=agents.create(self.root,args['name'],soul=soul,donor_id=args.get('soulFrom'))
            self.refresh()
            return result.as_dict()
        if command == 'agent_enabled_set':
            result=agents.set_enabled(self.root,args['id'],args['enabled'])
            self.refresh()
            return {'ok':True,'id':result.id,'enabled':result.enabled,'owner':'python'}
        if command == 'agent_remove':
            target=agents.find(self.root,args['id'])
            if target is None or target.base or target.enabled or target.id==self.selected:
                raise ValueError('Disable the non-base agent before removal')
            self.refresh()
            if self.status().get('agents',{}).get(target.id,{}).get('roles'):
                raise RuntimeError('Agent still has owned processes')
            return {'ok':True,'attic':str(agents.remove_agent(self.root,target.id,self.root/'agents-attic'))}
        if command.startswith('local_files_'):
            from . import local_files_cli
            return local_files_cli.handle(dict(args,action=command.removeprefix('local_files_')))
        from .python_desktop_commands import invoke
        return invoke(self,command,args)
