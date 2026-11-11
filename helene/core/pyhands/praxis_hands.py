# -*- coding: utf-8 -*-
"""Руки для Python, shell и foreground coding_run (10.11.2026).

    import praxis_hands as h
    text, version = h.read("workspace/a.py")
    h.edit("workspace/a.py", "old", "new", expected_sha256=version)

Это те же руки, что fs_read, edit и fs_search: дом, зоны записи, сверка версии,
общий замок записи, перечитывание после записи. Скрипт получает всё это вместо `str.replace`.

Отказ и неизвестный исход поднимают исключения: скрипт не может молча пройти мимо них.
Refused значит, что файл не тронут. InDoubt значит, что запись могла произойти: сначала
прочитай файл, вслепую не повторяй.

h.hand('name', **args) вызывает руку через живой родительский диспетчер и возвращает
outcome/content/result_ref/call_id/image_count. outcome — исход самой руки (confirmed, refused,
in_doubt, partial, ok, failed; unknown, если рука его не сообщает). h.describe('name')['content']
содержит полную схему. Канал закрывается с внешним вызовом или его потолком; начатая рука,
которую скрипт перестал ждать, доделывается, расписка приходит после [exit N].
Автономный скрипт/воркер канала не имеет.

В этой папке лежит только этот модуль, поэтому её место в PYTHONPATH не заслоняет модули
проекта. Ядро рук берётся из своего корня и только на время вызова: файлы проекта с такими
же именами (editor.py, forge.py) ему не мешают, и оно не мешает им.
"""
from __future__ import annotations

import contextlib
import functools
import os
import sys
import threading
from pathlib import Path

__all__ = ["read", "edit", "write", "search", "hand", "describe", "Refused", "InDoubt", "Partial"]


class EditError(Exception):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class Refused(EditError):
    """Файл не тронут, причина в тексте исключения."""


class InDoubt(EditError):
    """Запись могла произойти: прочитай файл, прежде чем что-то делать."""


class Partial(EditError):
    """Some files confirmed, others refused. Inspect result; no automatic rollback."""


def hand(name: str, /, *, _request_id: str | None = None, **kwargs):
    """Call a hand in the live parent, returning outcome/content/result_ref/call_id/image_count.

    _request_id identifies one exact intent within this outer invocation. Reuse it
    only for that intent after a lost response. No automatic transport retries.
    After the invocation ends its endpoint expires; inspect the run before retrying.
    """
    import json,socket,struct,uuid
    raw=os.environ.get('PRAXIS_HAND_BRIDGE')
    if not raw:raise Refused('h.hand needs a live parent invocation; no call started.')
    try:
        endpoint=json.loads(raw)
        data=json.dumps({'token':endpoint['token'],'request_id':uuid.uuid4().hex if _request_id is None else _request_id,
                         'name':name,'args':kwargs},ensure_ascii=False).encode('utf8')
    except (TypeError,ValueError,KeyError) as exc:
        raise Refused('Invalid hand endpoint or JSON arguments; no call started.') from exc
    def receive(sock,count):
        value=bytearray()
        while len(value)<count:
            chunk=sock.recv(count-len(value))
            if not chunk:raise EOFError('parent connection closed')
            value.extend(chunk)
        return bytes(value)
    maximum = 16 * 1024 * 1024
    if len(data) > maximum:
        raise Refused('Hand request too large; no call started.')
    try:
        if endpoint.get('transport') == 'pipe':
            import _winapi
            from multiprocessing.connection import PipeConnection
            # Exact client rights omit FILE_CREATE_PIPE_INSTANCE. Generic write
            # would unnecessarily let a sandbox create a competing server.
            _winapi.WaitNamedPipe(endpoint['socket'], 30000)
            handle = _winapi.CreateFile(endpoint['socket'], 0x12019b, 0, _winapi.NULL,
                                       _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, _winapi.NULL)
            try:
                _winapi.SetNamedPipeHandleState(handle, _winapi.PIPE_READMODE_MESSAGE, None, None)
                connection = PipeConnection(handle)
            except BaseException:
                _winapi.CloseHandle(handle)
                raise
        else:
            connection = socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
            try:
                connection.connect(endpoint['socket'])
            except BaseException:
                connection.close()
                raise
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise Refused('Live hand endpoint unavailable; this request was not sent.') from exc
    with connection:
        try:
            if endpoint.get('transport') == 'pipe':
                connection.send_bytes(data)
                response = json.loads(connection.recv_bytes(maximum).decode('utf8'))
            else:
                connection.sendall(struct.pack('!I',len(data))+data)
                length=struct.unpack('!I',receive(connection,4))[0]
                if length > maximum:
                    raise ValueError('Hand response too large')
                response=json.loads(receive(connection,length).decode('utf8'))
        except (OSError,EOFError,ValueError) as exc:
            raise InDoubt('Hand response lost after sending; inspect the run, do not blindly repeat.') from exc
    if response.get('status')=='refused':raise Refused(response['message'],response)
    if response.get('status')!='completed':raise InDoubt(response.get('message','Hand outcome unknown'),response)
    return response


def describe(name: str):
    """Fetch the exact current hand schema through the live parent."""
    return hand('describe',name=name)


_CORE_LOCK = threading.RLock()
_CORE_CACHE: dict = {}
_CORE_NAMES: dict = {}
_core_depth = 0


def _root() -> str:
    return os.environ.get("PRAXIS_HANDS_ROOT") or str(Path(__file__).resolve().parent.parent)


def _core_names(root: Path) -> set:
    key = str(root)
    if key not in _CORE_NAMES:
        found = {p.stem for p in root.glob("*.py")} | {p.parent.name for p in root.glob("*/__init__.py")}
        _CORE_NAMES[key] = found - set(sys.stdlib_module_names)
    return _CORE_NAMES[key]


def _is_core(name: str, module, root: Path) -> bool:
    """Лежит прямо в корне рук. Рабочая копия внутри корня (.proposals/x/forge.py) ядром не считается."""
    try:
        first = Path(module.__file__).resolve().relative_to(root).parts[0]
    except (AttributeError, TypeError, ValueError):
        return False
    return Path(first).stem == name.partition(".")[0]


@contextlib.contextmanager
def _core():
    """На время вызова имена ядра значат ядро; после вызова модули проекта снова на месте."""
    global _core_depth
    with _CORE_LOCK:
        if _core_depth:
            yield
            return
        text = _root()
        root = Path(text).resolve()
        names = _core_names(root)
        theirs = {n: m for n, m in list(sys.modules.items())
                  if n.partition(".")[0] in names and not _is_core(n, m, root)}
        for name in theirs:
            del sys.modules[name]
        present = set(sys.modules)
        for name, module in _CORE_CACHE.items():
            sys.modules.setdefault(name, module)
        sys.path.insert(0, text)
        _core_depth = 1
        try:
            yield
        finally:
            _core_depth = 0
            sys.path.remove(text)
            if text not in sys.path:
                sys.path.append(text)
            for name, module in list(sys.modules.items()):
                if name not in present and _is_core(name, module, root):
                    _CORE_CACHE[name] = sys.modules.pop(name)
            sys.modules.update(theirs)


def _in_core(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        with _core():
            return function(*args, **kwargs)
    return wrapped


def _workshop():
    root = _root()
    if root not in sys.path:
        sys.path.append(root)
    import workshop
    _desktop_paths(workshop)
    return workshop


def _desktop_paths(workshop):
    """Mirror the parent's desktop routing; OS permissions still bound the script.

    Relative paths, code proposals and task identities keep core semantics. Only
    absolute paths outside both code/data homes use approved mounts or open mode.
    """
    import json
    raw = os.environ.get('PRAXIS_HAND_FILE_POLICY')
    if not raw or getattr(workshop._resolve_read, '_desktop_paths', False):
        return
    try:
        policy = json.loads(raw)
        homes = [Path(p).resolve() for p in policy['homes']]
        reads = [Path(p).resolve() for p in policy['read']]
        writes = [Path(p).resolve() for p in policy['write']]
        opened = policy.get('open') is True
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise Refused('Invalid inherited desktop file policy') from exc
    read_original, write_original = workshop._resolve_read, workshop._resolve_write
    def external(value, write):
        p = Path(value)
        if not p.is_absolute(): return None
        p = p.resolve()
        if any(p.is_relative_to(home) for home in homes): return None
        return p if opened or any(p.is_relative_to(root) for root in (writes if write else reads)) else None
    def read_path(path):
        return read_original(path) or external(path, False)
    def write_path(path, proposal_id=''):
        found, error = write_original(path, proposal_id)
        if found is not None or proposal_id: return found, error
        found = external(path, True)
        return (found, '') if found is not None else (None, error)
    read_path._desktop_paths = True
    workshop._resolve_read, workshop._resolve_write = read_path, write_path


def _finish(result: dict):
    if result['status'] in {'confirmed','ok'}:
        return result if 'files' in result or result['status']=='ok' else result['after_sha256']
    error={'refused':Refused,'partial':Partial,'in_doubt':InDoubt}[result['status']]
    raise error(result['message'],result)


@_in_core
def read(path: str, *, task_id: str='') -> tuple[str,str]:
    """Read exact UTF-8 and SHA, in home or the explicit/inherited local task."""
    workshop=_workshop();task_id,context=_context(task_id)
    if not task_id:
        result=workshop.fs_read_result(path)
        if result['status']!='ok':raise Refused(result['message'],result)
        return result['text'],result['sha256']
    import forge,hands,forge_script
    if context is not None:
        try:forge_script.validate(forge,task_id,context)
        except (ValueError,OSError,TypeError) as exc:raise Refused(str(exc)) from exc
    task,root,error=forge._task_root(task_id)
    if error:raise Refused(error)
    if forge._remote_client(task) is not None:raise Refused('Python task reads require a local task; use fs_read(task_id=...) for remote tasks.')
    target,error=forge._inside(root,path)
    if error:raise Refused(error)
    try:
        raw,version=hands.snapshot(target)
        if raw is None:raise Refused('File does not exist')
        return raw.decode('utf-8'),version
    except (OSError,UnicodeError) as exc:raise Refused(str(exc)) from exc


@_in_core
def edit(path: str='', old: str='', new: str='', expected_sha256: str='', proposal_id: str='',
         files=None, mode: str='snapshot', *, action: str='', task_id: str='', content: str='',
         patch: str='', overwrite: bool=False, force: bool=False, all: bool=False):
    """The common editor: inferred replace/batch, or explicit write/patch.

    A local Forge foreground command inherits its task and verified custody.
    An explicit conflicting task is refused. Errors carry the complete .result.
    """
    _workshop()  # make the core importable, without shadowing project modules
    import editor
    task_id,context=_context(task_id)
    return _finish(editor.edit_result(action=action or ('batch' if files is not None else 'replace'),
        path=path,old=old,new=new,expected_sha256=expected_sha256,proposal_id=proposal_id,
        files=files,mode=mode,task_id=task_id,content=content,patch=patch,overwrite=overwrite,
        force=force,_script_context=context,all=all))


@_in_core
def write(path: str, content: str, overwrite: bool=False, force: bool=False,
          expected_sha256: str='', proposal_id: str='', *, task_id: str=''):
    """Full-content edit in home or explicit/inherited Forge task."""
    return edit(path=path,action='write',content=content,overwrite=overwrite,force=force,
                expected_sha256=expected_sha256,proposal_id=proposal_id,task_id=task_id)


@_in_core
def search(pattern: str, glob: str = "**/*.py", root: str = "", *, task_id: str = "") -> str:
    """Same address as read/edit; inherited task identity is validated before dispatch."""
    workshop = _workshop();task_id,context = _context(task_id)
    if not task_id:return workshop.fs_search(pattern, glob=glob, root=root)
    import forge,forge_script
    if context is not None:
        try:forge_script.validate(forge,task_id,context)
        except (ValueError,OSError,TypeError) as exc:raise Refused(str(exc)) from exc
    return forge.inspect(task_id, 'search', path=root, query=pattern, glob=glob)


def _context(task_id):
    import forge_script
    try:context=forge_script.inherited()
    except (ValueError,TypeError) as exc:raise Refused('Invalid inherited edit context') from exc
    if context is not None:
        bound=context.get('task_id')
        if not isinstance(bound,str) or not bound or (task_id and task_id!=bound):
            raise Refused('Explicit task differs from inherited command context')
        task_id=bound
    return task_id,context
