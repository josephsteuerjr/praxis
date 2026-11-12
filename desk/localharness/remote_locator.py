"""Find a moved agent through its owner bot, proving the origin before sending a key."""
import hashlib
import hmac
import json
import secrets
import urllib.parse
import urllib.request
import telegram_proxy
from deskd.telegram_http import Client
from deskd.network_route import Route

CONTEXT='helene.phone.origin.v1'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def signature(key:str,challenge:str,instance:str)->str:
    return hmac.new(key.encode(),f'{CONTEXT}\n{challenge}\n{instance}'.encode(),hashlib.sha256).hexdigest()

def read(request,config=None):
    with Route(config or {}).open(request,timeout=15) as reply:
        raw=reply.read(262145)
    if len(raw)>262144:raise ValueError('Ответ подключения слишком большой')
    return json.loads(raw)

def locate(config:dict)->str:
    key=str(config.get('key') or '').strip()
    tg=config.get('telegram') or {}
    token=str(tg.get('bot_token') or '').strip()
    owner=str(tg.get('owner_id') or '').strip()
    if not key or not token or not owner.isdigit() or int(owner)<=0:
        raise ValueError('Для автоматического поиска сервера нужны подключённый бот, ID владельца и ключ окна')
    button=Client.from_config(config).call('getChatMenuButton',chat_id=owner,_http_timeout=15) or {}
    if not isinstance(button,dict) or not isinstance(button.get('web_app'),dict):raise ValueError('Кнопка бота пока не ведёт к Hélène')
    target=urllib.parse.urlsplit(str(button['web_app'].get('url') or ''))
    if button.get('type')!='web_app' or target.scheme!='https' or not target.hostname or target.username or target.password or target.query or target.fragment or target.path!='/tg/':
        raise ValueError('Кнопка бота пока не ведёт к Hélène')
    base=urllib.parse.urlunsplit((target.scheme,target.netloc,'','',''))
    challenge=secrets.token_urlsafe(32)
    # No Authorization header here: the candidate must first prove possession of our key.
    proof=read(urllib.request.Request(base+'/api/phone/probe?challenge='+challenge),config)
    if not isinstance(proof,dict) or not isinstance(proof.get('instance'),str) or not isinstance(proof.get('proof'),str) or proof.get('challenge')!=challenge \
            or not hmac.compare_digest(proof['proof'],signature(key,challenge,proof['instance'])):
        raise ValueError('Адрес из бота не подтвердил, что это твой агент. Ключ окна туда не отправлен')
    return base
