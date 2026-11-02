"""Explicit primary creation uses the runner's one-use birth authority.

The chosen SOUL is materialized before login. Later code-folder replacement
therefore needs no transfer of an unconsumed birth permission. Retry is never
permission to re-prepare a started or lost home.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from . import boot
from .vision_policy import _document, _write

SOURCE='first_start.create'


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def _path(root):
    from .first_start import plain
    return plain(Path(root)/'kit-birth.json')


def _read(path):
    raw=path.read_bytes();value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('Повреждена запись первого создания')
    return value,_sha(raw)


def _soul(data):
    from .first_start import plain
    path=plain(Path(data)/'soul/SOUL.md')
    return path.read_bytes() if path.is_file() else None


def intent(config,seed):
    text=boot.soul_text(config) if seed is None else seed
    value={'agent':boot.agent_name(config),'owner':boot.owner_name(config),'seed_sha256':_sha(text.encode('utf-8'))}
    return _sha(_document(value))


def admit(root,data,choice,*,configured,has_agent):
    from .first_start import plain
    path=_path(root);data=plain(data)
    if not path.exists():
        if has_agent or configured:raise ValueError('Агент уже создавался; существующие или потерянные данные не разрешают новое рождение')
        return None
    value,generation=_read(path);created=value.get('creation')
    if (type(value.get('schema')) is not int or value.get('schema')!=1 or value.get('source')!=SOURCE or value.get('tree')!=str(data)
            or not isinstance(created,dict) or created.get('published') is not False
            or created.get('intent_sha256')!=choice or not isinstance(created.get('id'),str)
            or len(created['id'])!=32 or set(created['id'])-set('0123456789abcdef')
            or not isinstance(created.get('soul_sha256'),str) or len(created['soul_sha256'])!=64
            or set(created['soul_sha256'])-set('0123456789abcdef')
            or not isinstance(value.get('gaps',[]),list) or any(not isinstance(item,str) for item in value.get('gaps',[]))):
        raise ValueError('Прежняя запись рождения не разрешает повторное создание этого агента')
    if (data/'memory/.control/profile-identity.json').exists():
        raise ValueError('В этой папке уже есть созданный агент')
    state=value.get('state');soul=_soul(data)
    if state=='prepared':
        claim=path.with_name(path.name+'.claim')
        if configured or has_agent or soul is not None or os.path.lexists(claim):
            raise ValueError('Рождение уже заявлено или данные изменились; повторный Create не выдаёт нового полномочия')
    elif state in ('started','partial','complete'):
        if soul is None or _sha(soul)!=created.get('soul_sha256'):
            raise ValueError('Начатая конституция потеряна или изменена; повторное рождение запрещено, данные сохранены')
    else:raise ValueError('Неизвестное состояние первого рождения')
    return {'value':value,'generation':generation}


@contextmanager
def _imports(root,config):
    # The first-start process has no agent scheduler; birth_custody serializes
    # its Create calls. Use the same lazy core/layout imports as the runner.
    from .first_start import plain
    code=Path(config.get('code') or 'tree');code=plain(code if code.is_absolute() else Path(root)/code)
    previous=list(sys.path)
    sys.path[:0]=[str(Path(__file__).resolve().parent),str(code)]
    try:yield
    finally:sys.path[:]=previous


def materialize(root,data,config,choice,ticket,seed):
    from .first_start import plain
    path=_path(root);data=plain(data)
    text=boot._names(seed,config) if seed and seed.strip() else boot.soul_text(config)
    expected=_sha(text.encode('utf-8'))
    if ticket is None:
        from .first_start import agent_exists,load
        if admit(root,data,choice,configured=load(Path(root)/'helene.json').get('setup_complete') is True,
                 has_agent=agent_exists(data)) is not None:
            raise ValueError('Во время подготовки появилась другая запись рождения')
        value={'schema':1,'state':'prepared','source':SOURCE,'tree':str(data),
               'creation':{'id':uuid.uuid4().hex,'intent_sha256':choice,'soul_sha256':expected,'published':False}}
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        original=os.fstat(descriptor);os.close(descriptor)
        try:_write(path,_document(value),private=True)
        except BaseException:
            # Only this call's still-empty reservation can be removed. A
            # persisted or foreign authority is never replaced on failure.
            try:
                current=path.stat()
                if (current.st_dev,current.st_ino,current.st_size)==(original.st_dev,original.st_ino,0):path.unlink()
            except OSError:pass
            raise
    else:
        value,generation=_read(path)
        if generation!=ticket['generation'] or value['creation'].get('soul_sha256')!=expected:
            raise ValueError('Запись или выбранный текст рождения изменились; данные не переписываю')
        # Recheck immediately before touching authority, after a potentially
        # slow relay install. A retained SOUL is never re-seeded.
        from .first_start import agent_exists
        admit(root,data,choice,configured=False,has_agent=agent_exists(data))
        if value['state']=='started':
            value['state']='partial'
            value['gaps']=list(dict.fromkeys([*value.get('gaps',[]),'interrupted-first-create']))
            value['creation']['recovery']='explicit retry with retained SOUL'
            _write(path,_document(value),private=True)
    with _imports(root,config):
        boot.arm_git()
        boot.ensure_layout(data,config,soul_seed=seed,birth_receipt=path)
    actual=_soul(data);after,_=_read(path)
    if (actual is None or _sha(actual)!=expected or after.get('state') not in ('partial','complete')
            or after.get('creation',{}).get('id')!=value['creation']['id']):
        raise ValueError('Первое создание не подтвердило выбранную конституцию; настройки и личность не опубликованы')
    return value['creation']['id']


def published(root,identity):
    path=_path(root);value,_=_read(path)
    if value.get('creation',{}).get('id')!=identity:raise ValueError('Запись создания сменилась перед подтверждением')
    value['creation']['published']=True
    _write(path,_document(value),private=True)
