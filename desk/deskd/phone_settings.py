"""Typed agent settings for a paired owner's PWA, without a public shell RPC.

Reads never return stored secrets. Writes address declared settings only and
use the live desktop's normal optimistic config writer and model projection.
"""
from __future__ import annotations
import copy,json,secrets
from pathlib import Path
from urllib.parse import urlsplit
from . import readers
from localharness.config_transaction import read_snapshot

GROUPS=[('agent','Агент'),('brain','Мозг и связь'),('voice','Голос'),('rights','Права на ПК'),('app','Программа'),('look','Вид')]

def field(path,label,group='brain',kind='text',**extra):
    return dict(path=path,label=label,group=group,type=kind,**extra)

FIELDS=[
    field('agent.name','Имя агента','agent'),field('owner.name','Твоё имя','agent'),
    field('owner_context.messages','Свежих сообщений','agent','number',min=50,max=2000,default=600),
    field('owner_context.characters','Знаков разговора','agent','number',min=1000,max=600000,default=128000),
    field('owner_context.tokens','Предел токенов разговора','agent','number',min=2000,max=200000,default=40000),
    field('self_review.after_update','Ревью после обновления','agent','boolean'),
    field('self_review.after_self_edit','Ревью после самостоятельных правок','agent','boolean'),
    field('self_review.depth','Глубина ревью','agent','select',choices=[['quick','Быстрое'],['normal','Обычное'],['deep','Глубокое']]),
    field('self_review.max_parallel','Параллельных ревьюеров','agent','number',min=1,max=8),
    field('self_review.max_iters','Ходов на ревьюера · 0 без ограничения','agent','number',min=0,max=100000),
    field('self_review.models','Модели ревьюеров через запятую','agent','list'),
    field('provider','Подключение',kind='select',choices=[['chatgpt','Подписка ChatGPT'],['openai','Ключ API'],['anthropic','Anthropic API'],['local','Локальная модель']]),
    field('model.model','Модель'),field('model.base_url','Адрес API'),
    field('model.key','Ключ API',kind='password'),
    field('model.reasoning_effort','Усилие рассуждения',kind='select',choices=[[v,v or 'Автоматически'] for v in ('','low','medium','high','xhigh','max')]),
    field('model.service_tier','Скорость обработки',kind='select',choices=[['','По настройке провайдера'],['default','Обычный'],['priority','Fast mode']],
          hint='Fast mode увеличивает расход лимитов подписки или кредитов. Доступность ускорения определяет Codex. Выбор относится к ответам и шагам управления компьютером.'),
    field('model.pinned','Закрепить модель',kind='boolean'),
    field('model.fallback_model','Запасная модель'),field('model.fallback_framework','Протокол запасной',kind='select',choices=[['','Тот же провайдер'],['openai','OpenAI'],['anthropic','Anthropic']]),
    field('model.fallback_base_url','Адрес запасной'),field('model.fallback_key','Ключ запасной',kind='password'),
    field('model.vision_model','Зрячая модель'),
    field('telegram.bot_token','Токен бота Telegram',kind='password'),field('telegram.owner_id','Твой Telegram ID',kind='number',min=1,max=9007199254740991),
    field('telegram.proxy.enabled','Telegram через сервер',kind='boolean'),field('telegram.proxy.url','Адрес сервера Telegram'),
    field('telegram.proxy.key','Ключ сервера Telegram',kind='password'),
    field('images.enabled','Создание и правка картинок',kind='boolean'),field('images.model','Модель изображений',default='gpt-image-2'),
    field('images.quality','Качество изображений',kind='select',default='auto',choices=[['auto','По запросу'],['low','Черновик'],['medium','Обычное'],['high','Подробное']]),
    field('images.size','Размер изображений',default='auto'),
    field('images.background','Фон изображений',kind='select',default='opaque',choices=[['opaque','Обычный'],['transparent','Прозрачный'],['auto','Автоматически']]),
    field('voice.enabled','Распознавать речь','voice','boolean'),field('voice.model','Модель распознавания','voice',default='turbo'),
    field('voice.keep_loaded','Прогревать Whisper при запуске','voice','boolean',default=False),
    field('voice.speak','Озвучивать ответы','voice','boolean'),field('voice.voice','Движок голоса','voice',default='xtts'),
    field('voice.tts_language','Язык озвучивания','voice',default='ru'),field('voice.tts_device','Устройство XTTS','voice','select',default='cpu',choices=[['cpu','Процессор'],['cuda','Видеокарта']]),
    field('voice.tts_speaker','Голос XTTS','voice',default='Ana Florence'),
    field('agent_mode','Режим работы','rights','select',choices=[['sandbox','Песочница'],['interactive','Интерактивный']]),
    field('computer.enabled','Управление компьютером','rights','boolean'),field('sandbox.network','Сеть в песочнице','rights','boolean',default=True),
    field('phone.enabled','Подключать телефон и миниапп','app','boolean'),
    field('phone.mode','Адрес телефона','app','select',choices=[['automatic','Автоматический временный'],['stable','Постоянный HTTPS']]),
    field('phone.external','Постоянный HTTPS-адрес','app'),
    field('backup.every_days','Копия каждые N дней','app','number',min=0,max=3650,default=7),
    field('backup.keep','Хранить копий','app','number',min=1,max=10000,default=8),
    field('update.url','Адрес обновлений','app'),
    field('telegram.mode','Подключение Telegram',kind='select',default='bot',choices=[['bot','Бот'],['account','Личный аккаунт']]),
    field('telegram.status_message','Показывать «думаю…» у долгого хода',kind='boolean'),
    field('telegram.api_id','Telegram api_id'),field('telegram.api_hash','Telegram api_hash',kind='password'),
    field('telegram.phone','Номер Telegram'),
    field('relay.proxy.mode','Маршрут реле',kind='select',default='system',choices=[['system','Системные настройки'],['direct','Напрямую'],['proxy','Через прокси']]),
    field('relay.proxy.protocol','Протокол прокси',kind='select',default='http',choices=[['http','HTTP'],['https','HTTPS'],['socks5h','SOCKS5 · DNS через прокси'],['socks5','SOCKS5 · локальный DNS']]),
    field('relay.proxy.host','Сервер прокси'),field('relay.proxy.port','Порт прокси'),
    field('relay.proxy.username','Пользователь прокси'),field('relay.proxy.password','Пароль прокси',kind='password'),
    field('voice.language','Язык распознавания','voice',default='ru'),
    field('voice.tts_reference','Свой образец голоса · путь к WAV на ПК','voice'),
    field('computer.scopes','Разрешённые действия','rights','multiselect',default=['computer.read','computer.files','computer.process','computer.apps'],choices=[['computer.read','Читать экран и окна'],['computer.files','Работать с файлами'],['computer.process','Управлять процессами'],['computer.apps','Управлять приложениями']]),
    field('backup.dir','Папка копий на ПК','app'),
]

def section(path):
    if path.startswith(('agent.','owner.')):return 'Имена'
    if path.startswith('owner_context.'):return 'Контекст владельца'
    if path.startswith('self_review.'):return 'Саморевью'
    if path.startswith('relay.proxy.'):return 'Подключение реле'
    if path.startswith('telegram.'):return 'Telegram'
    if path.startswith('images.'):return 'Изображения'
    if path.startswith('voice.'):return 'Озвучивание' if path.startswith(('voice.tts_','voice.speak','voice.voice')) else 'Распознавание речи'
    if path.startswith('phone.'):return 'Телефон и миниапп'
    if path.startswith('backup.'):return 'Резервные копии'
    if path.startswith('update.'):return 'Обновления'
    if path.startswith('computer.'):return 'Управление компьютером'
    if path.startswith('sandbox.') or path=='agent_mode':return 'Песочница'
    return 'Модель'

for row in FIELDS:row['section']=section(row['path'])
BY_PATH={row['path']:row for row in FIELDS}

class SettingsError(ValueError):
    def __init__(self,message,status=400):super().__init__(message);self.status=status

def get(value,path,default=''):
    for key in path.split('.'):
        if not isinstance(value,dict) or key not in value:return default
        value=value[key]
    return value

def put(value,path,item):
    keys=path.split('.')
    for key in keys[:-1]:
        value[key]=dict(value.get(key) or {});value=value[key]
    value[keys[-1]]=item

def provider(config):
    if (config.get('relay') or {}).get('enabled') is True:return 'chatgpt'
    model=config.get('model') or {}
    if model.get('framework')=='anthropic':return 'anthropic'
    host=urlsplit(str(model.get('base_url') or '')).hostname or ''
    return 'local' if host in ('localhost','127.0.0.1','::1') else 'openai'

def scope_id():
    from localharness.package_notice import read
    identity=read(readers.tree()/'memory/.control/profile-identity.json').get('id')
    return identity if isinstance(identity,str) and identity else __import__('hashlib').sha256(str(readers.tree().resolve()).encode()).hexdigest()

def snapshot():
    path=readers.config_path()
    if path is None:raise SettingsError('Настройки агента недоступны',503)
    config,seen=read_snapshot(path);rows=[]
    from localharness.self_review import settings as review_settings
    effective=copy.deepcopy(config);effective['self_review']=review_settings({'self_review':config.get('self_review') or {}})
    from localharness import modes
    effective['agent_mode']=modes.resolve(config,installed=False)['name']
    for spec in FIELDS:
        row=dict(spec)
        value=provider(config) if row['path']=='provider' else get(effective,row['path'],row.get('default',False if row['type']=='boolean' else ''))
        if row['type']=='list':value=', '.join(value) if isinstance(value,list) else ''
        if row['type']=='password':row.update(value='',stored=bool(value))
        else:row['value']=value
        rows.append(row)
    return {'groups':GROUPS,'fields':rows,'mtime_ns':seen,'scope_id':scope_id(),'agent':get(config,'agent.name'),'schema':'helene.phone-settings.v1'}

def _value(spec,value):
    kind=spec['type']
    if kind=='boolean':
        if type(value) is not bool:raise SettingsError(spec['label']+': нужен переключатель')
    elif kind=='multiselect':
        if not isinstance(value,list) or len(value)>len(spec['choices']) or any(not isinstance(item,str) or item not in [row[0] for row in spec['choices']] for item in value):
            raise SettingsError(spec['label']+': неизвестное разрешение')
        value=list(dict.fromkeys(value))
    elif kind=='number':
        if type(value) is not int or not spec['min']<=value<=spec['max']:
            raise SettingsError(f"{spec['label']}: от {spec['min']} до {spec['max']}")
    else:
        if not isinstance(value,str) or len(value)>16384:raise SettingsError(spec['label']+': неверное значение')
        if spec['path']=='model.service_tier' and value=='fast':value='priority'
        if kind=='select' and value not in [row[0] for row in spec['choices']]:raise SettingsError(spec['label']+': выбери значение из списка')
        value=value.strip()
        if kind=='list':value=list(dict.fromkeys(item.strip() for item in value.split(',') if item.strip()))
    if spec['path']=='phone.external' and value:
        url=urlsplit(value)
        if url.scheme!='https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise SettingsError('Для телефона нужен HTTPS-адрес без пути и ключей')
        value=value.rstrip('/')
    return value

def prepare(config,changes):
    if not isinstance(changes,dict) or not changes:raise SettingsError('Нет изменений')
    if set(changes)-set(BY_PATH):raise SettingsError('Эти параметры не относятся к настройкам агента')
    result=copy.deepcopy(config);before=provider(config);selected=changes.get('provider',before)
    if selected!='chatgpt' and changes.get('model.service_tier') in ('priority','fast'):
        raise SettingsError('Fast mode доступен в подключении Codex через подписку ChatGPT')
    for path,value in changes.items():
        spec=BY_PATH[path]
        if path=='model.reasoning_effort' and selected=='chatgpt':
            if not isinstance(value,str) or value not in ('','minimal','low','medium','high','xhigh','max','ultra'):
                raise SettingsError('Выбери усилие модели Codex или её настройку по умолчанию')
            # The client intersects the live model catalog with transport
            # levels. The adapter validates model availability again at send.
            spec=dict(spec,choices=[[value,value]])
        value=_value(spec,value)
        if path=='model.service_tier' and not value:
            result.setdefault('model',{}).pop('service_tier',None)
        elif path!='provider':put(result,path,value)
    model=result.setdefault('model',{});relay=result.setdefault('relay',{})
    if selected!=before:
        if selected!='chatgpt':model.pop('service_tier',None)
        keys=dict((config.get('model') or {}).get('keys') or {})
        old=(config.get('model') or {}).get('key')
        if old:keys[before if before!='openai' else 'api']=old
        if 'model.key' not in changes:model['key']=keys.get('api' if selected=='openai' else selected,'')
        model['keys']=keys
        for path in ('api_mode','provider','provider_id'):model.pop(path,None)
    model['provider']='api' if selected=='openai' else selected
    if selected=='chatgpt':
        if 'model.key' in changes:raise SettingsError('Для подписки ChatGPT ключ API не нужен')
        port=relay.get('port') or readers.RELAY_PORT_DEFAULT
        key=(config.get('relay') or {}).get('key') or ((config.get('model') or {}).get('key') if before=='chatgpt' else (model.get('keys') or {}).get('chatgpt'))
        if not isinstance(key,str) or not key.strip():key='sk-frame-'+secrets.token_hex(24)
        relay.update(enabled=True,key=key,port=port)
        model.update(framework='openai',key=key,base_url=f'http://127.0.0.1:{port}')
    else:
        relay['enabled']=False;model['framework']='anthropic' if selected=='anthropic' else 'openai'
        if selected!=before and 'model.base_url' not in changes:
            model['base_url']={'openai':'https://api.openai.com/v1','anthropic':'https://api.anthropic.com','local':'http://127.0.0.1:11434/v1'}[selected]
    if get(result,'images.enabled',False) and not relay.get('key'):
        relay.update(key=(model.get('keys') or {}).get('chatgpt') or 'sk-frame-'+secrets.token_hex(24),port=relay.get('port') or readers.RELAY_PORT_DEFAULT)
    proxy=get(result,'relay.proxy',{})
    if isinstance(proxy,dict) and proxy.get('mode')=='proxy':
        import re
        host=str(proxy.get('host') or '').strip();port=str(proxy.get('port') or '')
        if not host or re.search(r'[\s/@?#\\]',host) or not port.isdecimal() or not 1<=int(port)<=65535:
            raise SettingsError('Для прокси нужны сервер без пути и порт 1–65535')
        if proxy.get('password') and not proxy.get('username'):raise SettingsError('Для пароля прокси укажи пользователя')
    if get(result,'phone.mode')=='stable':
        if not get(result,'phone.external'):raise SettingsError('Укажи постоянный HTTPS-адрес телефона')
        # The fixed local ingress is profile data, independent of code folders.
        put(result,'phone.local_port',config.get('port'))
    if 'agent_mode' in changes:
        from localharness import modes
        modes.apply(result,installed=False)
    return result

def _owner(path,tree):
    from localharness import session_host,agents
    root=path.parent.parent.parent if path.parent.parent.name=='agents' else path.parent
    descriptor=session_host.read(session_host.control_path(root))
    if descriptor.get('program')!=str(root) or session_host.process_state(descriptor)!='alive':
        raise SettingsError('Действующий сеанс программы не подтвердился. Обнови подключение.',503)
    identity=agents.BASE_ID if path.parent==root else path.parent.name
    agent=agents.find(root,identity)
    if agent is None or agent.config.resolve()!=path or agent.tree.resolve()!=tree.resolve():raise SettingsError('Настройки относятся к другому агенту',409)
    return descriptor,identity

def _desktop_commit(path,tree,value,seen,operation):
    from localharness import session_host
    descriptor,identity=_owner(path,tree)
    return session_host._rpc(descriptor,'invoke',{'agent':identity,'command':'config_save','arguments':{
        'config':json.dumps(value,ensure_ascii=False),'mtimeNs':seen,'phone_operation':operation}},timeout=60)

def subscription(body=None):
    from types import SimpleNamespace
    from localharness import standalone_relay,python_relay,session_host
    path=readers.config_path()
    if path is None:raise SettingsError('Настройки агента недоступны',503)
    path=Path(path).resolve();config,_=read_snapshot(path)
    root=path.parent.parent.parent if path.parent.parent.name=='agents' else path.parent
    tree=readers.tree();agent=SimpleNamespace(config=path,tree=tree)
    installed=standalone_relay.executable(root,config).is_file()
    if body is not None:
        if not isinstance(body,dict) or body.get('action') not in ('login','logout','switch','cancel_login','install'):
            raise SettingsError('Недоступное действие подписки')
        action=body['action'];slot=body.get('slot','primary')
        if slot not in python_relay.SLOTS:raise SettingsError('Неизвестный слот подписки')
        if action=='install':
            standalone_relay.install(root)
            descriptor,identity=_owner(path,tree)
            session_host._rpc(descriptor,'refresh',{'agent':identity})
            installed=True
        elif not ((config.get('relay') or {}).get('enabled') or (config.get('images') or {}).get('enabled')):
            raise SettingsError('Сначала сохрани выбор подписки ChatGPT')
        else:python_relay.control(agent,action,slot=slot)
    component=standalone_relay.status(root)
    if not installed:return {'available':False,'component_ready':component['ready'],'installed':False,'note':'Для подписки нужен компонент praxis-relay.'}
    if not ((config.get('relay') or {}).get('enabled') or (config.get('images') or {}).get('enabled')):
        return {'available':False,'installed':True,'note':'Сохрани выбор подписки ChatGPT, затем войди в аккаунт.'}
    try:state=python_relay.control(agent,'status')
    except (OSError,ValueError):return {'available':False,'installed':True,'note':'Реле ещё не подтвердило запуск. Обнови состояние через несколько секунд.'}
    login=state.get('login') or {}
    return {'available':True,'installed':True,'slots':[{'slot':row.get('slot'),'stored':bool(row.get('configured')),
        'active':bool(row.get('active'))} for row in state.get('accounts',[])],
        'login':{'pending':bool(login.get('pending')),'slot':login.get('slot'),'error':login.get('error'),
                 'url':state.get('login_url') if login.get('pending') else None}}

def save(body):
    if not isinstance(body,dict):raise SettingsError('Нужен объект настроек')
    path=readers.config_path()
    if path is None:raise SettingsError('Настройки агента недоступны',503)
    path=Path(path).resolve();current,seen=read_snapshot(path)
    if body.get('scope_id')!=scope_id():raise SettingsError('Выбран другой агент. Перечитай настройки.',409)
    if not body.get('mtime_ns') or str(body['mtime_ns'])!=seen:raise SettingsError('Настройки изменились. Перечитай их и перенеси свою правку.',409)
    operation=body.get('operation')
    if not isinstance(operation,str) or not __import__('re').fullmatch('[a-f0-9]{32}',operation):raise SettingsError('Не указан номер сохранения')
    value=prepare(current,body.get('changes'))
    answer=_desktop_commit(path,readers.tree(),value,seen,operation)
    if not answer.get('ok'):raise SettingsError(answer.get('error') or 'Настройки не сохранились',409 if answer.get('code') in ('stale','conflict') else 400)
    return {'ok':True,'mtime_ns':answer.get('mtime_ns'),'operation':operation,
            'note':answer.get('warning') or 'Настройки агента сохранены. Модель перечитает их перед следующим запросом.',
            'restart_needed':any(path.split('.')[0] in ('telegram','voice','computer','sandbox','agent_mode','owner_context','phone') for path in body['changes'])}
