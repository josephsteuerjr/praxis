"""One saved outbound HTTP route for the application; no process-wide env mutation."""
import ipaddress
import base64
import io
import email.message
import ssl
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SelectedProxy(urllib.request.ProxyHandler):
    """An explicit app route cannot be bypassed by unrelated NO_PROXY settings."""
    def proxy_open(self,request,proxy,kind):
        if urllib.parse.urlsplit(request.full_url).hostname in ('localhost','127.0.0.1','::1'):
            return None
        parsed=urllib.parse.urlsplit(proxy)
        host=parsed.hostname
        if ':' in host: host='['+host+']'
        if parsed.port: host+=':'+str(parsed.port)
        if parsed.username is not None:
            credentials=urllib.parse.unquote(parsed.username)+':'+urllib.parse.unquote(parsed.password or '')
            request.add_header('Proxy-authorization','Basic '+base64.b64encode(credentials.encode()).decode('ascii'))
        original=request.type
        request.set_proxy(host,parsed.scheme)
        if original!=parsed.scheme and original!='https':
            return self.parent.open(request,timeout=request.timeout)


class _Stream(io.RawIOBase):
    """Keep urllib's bounded reads without buffering an entire proxied download."""
    def __init__(self,response,client):
        self.response,self.client=response,client
        self.parts=iter(response.iter_raw(chunk_size=65536));self.pending=b''
    def readable(self): return True
    def readinto(self,buffer):
        if not self.pending:
            try:self.pending=next(self.parts)
            except StopIteration:return 0
        length=min(len(buffer),len(self.pending))
        buffer[:length]=self.pending[:length];self.pending=self.pending[length:]
        return length
    def close(self):
        try:self.response.close()
        finally:
            self.client.close();super().close()


class HTTPSProxy(urllib.request.HTTPSHandler):
    """stdlib sends CONNECT in clear text even for an https:// proxy.

    httpx (already a core dependency) supplies verified TLS to the proxy and
    nested TLS to the destination. urllib still owns redirect/error policy.
    """
    def __init__(self,route):
        super().__init__();self.route=route
    def https_open(self,request):
        proxy=self.route.proxy_for(request.full_url)
        if urllib.parse.urlsplit(proxy).scheme!='https':return super().https_open(request)
        import httpx
        context=ssl.create_default_context()
        client=httpx.Client(proxy=httpx.Proxy(proxy,ssl_context=context),verify=context,
                            trust_env=False,follow_redirects=False,timeout=request.timeout)
        try:
            headers={k:v for k,v in request.header_items() if k.lower() not in ('proxy-authorization','host')}
            sent=client.build_request(request.get_method(),request.full_url,content=request.data,headers=headers)
            response=client.send(sent,stream=True)
            parsed=email.message.Message()
            for key,value in response.headers.multi_items():parsed.add_header(key,value)
            wrapped=urllib.response.addinfourl(io.BufferedReader(_Stream(response,client)),parsed,
                                               request.full_url,response.status_code)
            wrapped.msg=response.reason_phrase
            return wrapped
        except Exception as exc:
            client.close()
            raise urllib.error.URLError('HTTPS proxy: '+type(exc).__name__) from None


def settings(config):
    value=(config.get('network') or {}).get('proxy') or {}
    if not isinstance(value,dict): raise ValueError('Настройки сети должны быть объектом.')
    mode=value.get('mode','system')
    if mode not in ('system','direct','proxy'): raise ValueError('Выбери маршрут подключения приложения.')
    if mode!='proxy': return mode,''
    protocol=value.get('protocol','http')
    host=str(value.get('host') or '').strip()
    if protocol not in ('http','https') or not host or any(c in host for c in '/@?#\\ \t\r\n'):
        raise ValueError('Нужны HTTP/HTTPS-прокси, адрес сервера без пути и порт.')
    try:
        port=int(value.get('port') or 0)
        if not 1<=port<=65535: raise ValueError()
        if ':' in host: host='['+str(ipaddress.IPv6Address(host.strip('[]')))+']'
    except ValueError: raise ValueError('Проверь адрес прокси и порт 1–65535.') from None
    user=str(value.get('username') or '');password=str(value.get('password') or '')
    if password and not user: raise ValueError('Для пароля прокси укажи имя пользователя.')
    auth=urllib.parse.quote(user,safe='')+':'+urllib.parse.quote(password,safe='')+'@' if user else ''
    return mode,f'{protocol}://{auth}{host}:{port}'


class Route:
    def __init__(self,config):
        self.mode,self.url=settings(config)

    def proxies(self):
        if self.mode=='system': return urllib.request.getproxies()
        return {'http':self.url,'https':self.url} if self.url else {}

    def proxy_for(self,url):
        parsed=urllib.parse.urlsplit(url)
        if parsed.hostname in ('localhost','127.0.0.1','::1'): return ''
        if self.mode=='system' and urllib.request.proxy_bypass(parsed.hostname or ''): return ''
        proxy=self.proxies().get(parsed.scheme) or ''
        if proxy and urllib.parse.urlsplit(proxy).scheme not in ('http','https'):
            raise ValueError('Для этого подключения нужен системный HTTP/HTTPS-прокси.')
        return proxy

    def opener(self,*handlers):
        proxy=SelectedProxy(self.proxies()) if self.mode=='proxy' else urllib.request.ProxyHandler(self.proxies())
        return urllib.request.build_opener(proxy,HTTPSProxy(self),*(handlers or (NoRedirect(),)))

    def open(self,request,timeout=30):
        return self.opener().open(request,timeout=timeout)
