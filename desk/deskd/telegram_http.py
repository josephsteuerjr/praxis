"""Bot API requests shared by the receiver, checks and menu publisher."""
import json
import hashlib
import urllib.error
import urllib.parse
import urllib.request
from . import network_route,telegram_proxy


def connection_identity(config):
    tg=config.get('telegram') or {}
    value={'telegram':{key:tg.get(key) for key in ('bot_token','owner_id','mode','proxy')},
           'network':config.get('network') or {}}
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


class BotApiError(RuntimeError):
    def __init__(self,method,code,description,retry_after=0.0):
        super().__init__(f'{method}: [{code}] {description}')
        self.method,self.code=method,int(code)
        self.description=str(description)
        self.retry_after=float(retry_after)

    @property
    def permanent(self):
        return 400<=self.code<500 and self.code!=429


class Client:
    def __init__(self,token,*,proxy_url='',proxy_key='',network_config=None):
        self._base=f'https://api.telegram.org/bot{token}/'
        self._proxy_url,self._proxy_key=proxy_url,proxy_key
        self.route=network_route.Route(network_config or {})
        # Direct constructor remains usable by transport fixtures and extensions.
        self._open=self.route.open if network_config is not None else lambda *args,**kw:urllib.request.urlopen(*args,**kw)

    @classmethod
    def from_config(cls,config):
        url,key=telegram_proxy.settings(config)
        return cls(str((config.get('telegram') or {}).get('bot_token') or '').strip(),
                   proxy_url=url,proxy_key=key,network_config=config)

    def call(self,method,_http_timeout=30.0,**params):
        data={key:json.dumps(value,ensure_ascii=False) if isinstance(value,(dict,list,bool)) else str(value)
              for key,value in params.items() if value is not None}
        request=urllib.request.Request(self._base+method,data=urllib.parse.urlencode(data).encode('utf-8'),
            headers={'Content-Type':'application/x-www-form-urlencoded'})
        return self._read(method,request,_http_timeout)

    def _read(self,method,request,timeout):
        if self._proxy_url: request=telegram_proxy.bot_request(request,self._proxy_url,self._proxy_key)
        try:
            with self._open(request,timeout=timeout) as response:
                raw=response.read(16*1024*1024+1)
                if len(raw)>16*1024*1024: raise ValueError('Telegram response is too large')
                payload=json.loads(raw)
        except urllib.error.HTTPError as error:
            try: payload=json.loads(error.read(262145))
            except (OSError,ValueError): payload={}
            finally: error.close()
            if not isinstance(payload,dict): payload={}
            raise BotApiError(method,payload.get('error_code') or error.code,
                payload.get('description') or 'Telegram rejected the request',
                (payload.get('parameters') or {}).get('retry_after') or 0) from None
        if not isinstance(payload,dict): raise BotApiError(method,0,'Invalid Telegram response')
        if not payload.get('ok'):
            raise BotApiError(method,payload.get('error_code') or 0,payload.get('description') or 'not ok',
                (payload.get('parameters') or {}).get('retry_after') or 0)
        return payload.get('result')
