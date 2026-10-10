"""Read-only connection checks for onboarding; never consumes Telegram updates."""
from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request
from . import telegram_proxy

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def check_telegram(config: dict, fields: dict | None = None) -> dict:
    tg = dict(config.get('telegram') or {})
    patch = fields if isinstance(fields,dict) else {}
    for key in ('bot_token','owner_id','mode'):
        if key in patch:tg[key]=patch[key]
    if 'proxy' in patch and isinstance(patch['proxy'],dict):
        tg['proxy']={**(tg.get('proxy') or {}),**patch['proxy']}
    out={'ok':False,'checks':[],'restart_needed':False}
    rows=out['checks']
    opener=urllib.request.build_opener(NoRedirect())
    try:
        url,key=telegram_proxy.settings({'telegram':tg})
    except ValueError:
        rows.append({'name':'Прокси','ok':False,'message':'Нужны HTTPS-адрес сервера и ключ доступа. Их выдаёт тот, кто подготовил подключение Telegram.'})
        return out
    def read(req):
        with opener.open(req,timeout=15) as response:raw=response.read(262145)
        if len(raw)>262144:raise ValueError('response too large')
        return json.loads(raw)
    try:
        if url:
            health=read(urllib.request.Request(url+'/health',headers={'Authorization':'Bearer '+key}))
            if not isinstance(health,dict) or health.get('ok') is not True:
                raise ValueError('unhealthy gateway')
            caps=health.get('capabilities') or []
            if not isinstance(caps,list):caps=[]
            rows.append({'name':'Прокси','ok':True,'message':'Сервер отвечает по защищённому соединению.'})
            files='file' in caps
            rows.append({'name':'Фото и стикеры','ok':files,'message':'Сервер умеет передавать файлы.' if files else 'На сервере стоит прежний шлюз: сообщения проходят, а для фото и стикеров его нужно обновить.'})
            if tg.get('mode')=='account':
                ready='mtproto' in caps
                rows.append({'name':'Аккаунт Telegram','ok':ready,'message':'Сервер поддерживает подключение аккаунта.' if ready else 'Шлюз нужно обновить для подключения аккаунта Telegram.'})
        else:
            files=True
            if tg.get('mode')=='account':rows.append({'name':'Прокси','ok':True,'message':'Прокси выключен: аккаунт будет использовать своё прямое подключение.'})
        token=str(tg.get('bot_token') or '').strip()
        if tg.get('mode')!='account':
            if not token:
                rows.append({'name':'Бот','ok':False,'message':'Сначала вставь токен своего бота из BotFather.'})
            else:
                req=urllib.request.Request('https://api.telegram.org/bot'+token+'/getMe',data=b'')
                if url:req=telegram_proxy.bot_request(req,url,key)
                reply=read(req)
                profile=reply.get('result') if isinstance(reply,dict) else None
                valid=isinstance(reply,dict) and reply.get('ok') is True and isinstance(profile,dict) and profile.get('is_bot') is True
                rows.append({'name':'Бот','ok':valid,'message':('Бот @'+str(profile.get('username') or '')+' подключается.') if valid else 'Telegram не принял токен бота. Скопируй его из BotFather заново.'})
                if valid and profile.get('username'):out['bot_url']='https://t.me/'+urllib.parse.quote(str(profile['username']),safe='')
                owner=str(tg.get('owner_id') or '').strip()
                valid_owner=owner.isdigit() and int(owner)>0
                if valid_owner:owner=str(int(owner))
                if valid and valid_owner:
                    try:
                        req=urllib.request.Request('https://api.telegram.org/bot'+token+'/getChat',data=urllib.parse.urlencode({'chat_id':owner}).encode())
                        if url:req=telegram_proxy.bot_request(req,url,key)
                        chat=read(req);details=chat.get('result') if isinstance(chat,dict) else None
                        started=isinstance(chat,dict) and chat.get('ok') is True and isinstance(details,dict) and details.get('type')=='private' and str(details.get('id'))==owner
                    except urllib.error.HTTPError as error:
                        if error.code!=400:raise
                        started=False
                    rows.append({'name':'Владелец миниаппа','ok':started,'message':'Бот видит твой личный чат. Кнопку миниаппа можно подключать.' if started else 'Открой своего бота и нажми «Начать», затем повтори проверку. Если уже нажал — проверь свой Telegram ID.'})
                else:
                    rows.append({'name':'Владелец миниаппа','ok':valid_owner,'message':'ID владельца указан.' if valid_owner else 'Для кнопки миниаппа нужен твой числовой Telegram ID.'})
        out['ok']=bool(rows) and all(row['ok'] for row in rows)
    except urllib.error.HTTPError as error:
        if error.code in (401,403):message='Сервер или Telegram не принял ключ. Проверь ключ прокси и токен бота.'
        elif error.code==404:message='По этому адресу нет нужного подключения. Проверь адрес или обнови шлюз на сервере.'
        else:message='Сервер ответил ошибкой. Попробуй ещё раз.'
        rows.append({'name':'Соединение','ok':False,'message':message})
    except (OSError,ValueError,TimeoutError):
        rows.append({'name':'Соединение','ok':False,'message':'Подключиться не удалось. Проверь интернет и адрес сервера, затем повтори.'})
    return out
