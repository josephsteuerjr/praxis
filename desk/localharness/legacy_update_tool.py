"""Give the existing agent the incoming update hand without another model."""
from contextlib import contextmanager
from pathlib import Path
import hashlib,json,os,subprocess,sys,urllib.request
from urllib.parse import urlsplit
if __package__ in (None,''):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]));__package__='localharness'
from .package_notice import read,write
from .agent_source_update import require_patch_tool,AgentResolutionUnavailable


def replace_owned(path,expected,data):
    import uuid
    part=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        part.write_bytes(data)
        if path.read_bytes()!=expected:raise RuntimeError('Update hand changed before replacement')
        part.replace(path)
    finally:part.unlink(missing_ok=True)


def live_control(old):
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in Path(old).glob('.python-window-*.json'):
        control=read(path)
        if Path(control.get('root') or '.').resolve()!=Path(old).resolve():continue
        url=urlsplit(control.get('origin',''))
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.username:continue
        probe=urllib.request.Request(control['origin']+'/__python__/invoke',
            data=b'{"command":"agents_list","args":{}}',
            headers={'Content-Type':'application/json','X-Helene-Window':control['token']})
        try:
            with http.open(probe,timeout=2) as response:payload=json.load(response)
            status=payload.get('result',payload)
            if not any(row.get('raised') for row in status.get('agents',[])):continue
        except (OSError,ValueError):continue
        return control
    raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Прежнее окно не предоставляет управление раннером. Запусти прежнюю Hélène для передачи задачи агенту или выбери чистый код с сохранением данных.')


def restart_engine(old):
        control=live_control(old)
        request=urllib.request.Request(control['origin']+'/__python__/invoke',
            data=b'{"command":"engine_restart","args":{}}',
            headers={'Content-Type':'application/json','X-Helene-Window':control['token']})
        http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with http.open(request,timeout=60) as response:
            result=json.load(response)
        if result.get('error'):raise RuntimeError(result['error'])
        if result.get('result',result).get('ok') is not True:raise RuntimeError('Legacy runner restart not acknowledged')
        return


def ensure_agent_window(old,new):
    """A closed legacy Python window can be opened with its existing profile."""
    try:live_control(old);return True
    except AgentResolutionUnavailable:pass
    entry=Path(old)/'app/python_app.py'
    controller=Path(old)/'app/localharness/python_controller.py'
    if (not entry.is_file() or not controller.is_file()
            or 'def publish_control' not in entry.read_text(encoding='utf-8-sig')
            or 'engine_restart' not in controller.read_text(encoding='utf-8-sig')):
        raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Прежняя программа не предоставляет Python-управление агентом. Доступен чистый код с сохранением данных.')
    from .update_process import interpreter_for
    with (Path(new).parent/'legacy-agent-window.log').open('ab') as log:
        child=subprocess.Popen([str(interpreter_for(old)),'-s',str(entry),'--root',str(old)],
            cwd=old,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            env=dict(os.environ,PYTHONPATH=str(Path(old)/'app'),PYTHONUTF8='1',PYTHONIOENCODING='utf-8'),
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    import time
    deadline=time.monotonic()+30
    while time.monotonic()<deadline:
        if child.poll() is not None:break
        try:live_control(old);return False
        except AgentResolutionUnavailable:time.sleep(.2)
    raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Не удалось открыть прежнего агента для подключения инструмента. Подробности сохранены в legacy-agent-window.log; доступен чистый код с сохранением данных.')


def shim(new,interpreter,tool,bridge_id):
    """Appended to the old module; its other legacy functions stay available."""
    return '\n# Incoming update hand, temporary; no model process.\n'+f'''
TOOL = {tool!r}
_incoming_python = {str(interpreter)!r}
_incoming_hand = {str(Path(new)/'app/localharness/legacy_update_tool.py')!r}
_incoming_bridge_id = {bridge_id!r}
def command(tree, action='status', **args):
    import subprocess,os
    result=subprocess.run([_incoming_python,'-B','-s',_incoming_hand,'--tree',str(tree)],
        input=json.dumps(dict(args,action=action)).encode('utf-8'),capture_output=True,
        timeout=90,env=dict(os.environ,PYTHONUTF8='1',PYTHONIOENCODING='utf-8'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-1000:])
    return json.loads(result.stdout)
def pending(tree):
    request=read(path(tree))
    return request if request.get('resolution_owner')=='agent' and request.get('state') in ('pending','waiting_agent') else None
def install(agent,tree):
    def hand(action='status',**args):
        try:return json.dumps(command(tree,action,**args),ensure_ascii=False)
        except Exception as error:return json.dumps({{'error':str(error)}},ensure_ascii=False)
    agent.TOOL_IMPL['source_update']=hand
    agent.BASE_TOOLS[:]=[t for t in agent.BASE_TOOLS if t.get('name')!='source_update']
    agent.BASE_TOOLS.append(TOOL)
    write(path(tree).with_name('source-update-hand-ready.json'),{{'bridge':_incoming_bridge_id,'pid':__import__('os').getpid()}})
def notify_due(tree,turn):
    request=pending(tree)
    if not request or int(request.get('tries',0))>=3 or time.time()<request.get('retry_at',0):return
    from . import owner_stop
    if owner_stop.stopped() or owner_stop.paused():return
    request.update(tries=int(request.get('tries',0))+1,retry_at=time.time()+120);write(path(tree),request)
    return turn('source-update-'+request['id'],speaker='Hélène',origin_text=
        'Владелец начал обновление с сохранением правок. Новая сборка предоставила тебе source_update: '
        'status, diff/read с пагинацией, patch(before/after, expected_candidate_sha256), finish. '
        'Разреши оставшиеся конфликты точечными патчами в изолированной копии, сохраняя оба намерения. '
        'Живой код не меняй. После finish новый координатор проведёт независимое ревью и продолжит обновление. '
        'Приёмка передана тебе: сам исправляй замечания панели и повторяй finish; не проси владельца запускать ревью. '
        'Stop и отмена обязательны.')
'''


@contextmanager
def provide(old,new):
    old,new=Path(old).resolve(),Path(new).resolve()
    try:require_patch_tool(old)
    except AgentResolutionUnavailable:pass
    else:
        yield;return
    from . import owner_stop,app_migration,source_update
    if owner_stop.stopped() or owner_stop.paused():
        raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Stop/Pause запрещает перезапуск агента для подключения инструмента.')
    # Never append bootstrap code to an authored source tree.
    target=old/'app/localharness/source_update.py'
    for agent in app_migration.discover(old)['agents']:
        if source_update._owner_blocked(agent['tree']):
            raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Stop/Pause выбранного агента запрещает подключение инструмента.')
        if target.is_relative_to(Path(agent['code']).resolve()):
            raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Старый инструмент находится внутри авторского кода; выбери чистый код или отдельную программу.')
    if not target.is_file():raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Прежний Python-инструмент обновления отсутствует.')
    from .source_update_host import verified_runtime
    verified_runtime(new)
    from .update_process import interpreter_for
    import uuid,time
    bridge_id=uuid.uuid4().hex
    trees=[Path(a['tree']) for a in app_migration.discover(old)['agents'] if a['value'].get('mode')!='remote']
    original=target.read_bytes();patched=original+shim(new,interpreter_for(new),source_update.TOOL,bridge_id).encode('utf-8')
    backup=new.parent/'legacy-update-tool-backup.py';backup.write_bytes(original)
    try:replace_owned(target,original,patched)
    except OSError as error:
        raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Нет доступа для подключения инструмента к прежней программе. Прежний файл сохранён; можно установить чистый код с текущими данными.') from error
    try:
        if ensure_agent_window(old,new):restart_engine(old)
        deadline=time.monotonic()+30
        while not all(read(source_update.path(tree).with_name('source-update-hand-ready.json')).get('bridge')==bridge_id for tree in trees):
            if owner_stop.stopped() or owner_stop.paused():raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Подключение инструмента остановлено владельцем.')
            if time.monotonic()>=deadline:raise AgentResolutionUnavailable('SELECTED_AGENT_UPDATE_UNAVAILABLE: Раннер не подтвердил подключение нового инструмента; доступен чистый код с сохранением данных.')
            time.sleep(.2)
        yield
    finally:
        if target.read_bytes()==patched:
            replace_owned(target,patched,original)
            if not owner_stop.stopped() and not owner_stop.paused():restart_engine(old)
        else:raise RuntimeError('Temporary update hand changed; original retained at '+str(backup))
        for tree in trees:
            receipt=source_update.path(tree).with_name('source-update-hand-ready.json')
            if read(receipt).get('bridge')==bridge_id:receipt.unlink(missing_ok=True)


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--tree',type=Path,required=True);args=parser.parse_args()
    new=Path(__file__).resolve().parents[2]
    from localharness.source_update_host import verified_runtime
    verified_runtime(new)
    from localharness import source_update
    from localharness import source_merge
    request_state=read(source_update.path(args.tree))
    _,plan=source_update._bound(request_state)
    if (request_state.get('version')!=read(new/'python-app.json').get('version')
            or plan['files']['incoming']!=source_merge.inventory(new/'tree')):
        raise ValueError('Incoming hand does not match this requested update')
    request=json.load(sys.stdin);action=request.pop('action','status')
    result=source_update.command(args.tree,action,**request)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':
    if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    main()
