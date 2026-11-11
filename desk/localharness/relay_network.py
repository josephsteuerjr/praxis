"""Saved relay connection -> private child environment. No global proxy mutation."""
import urllib.parse


def standard_proxy_environment(url,bypass):
    # Standalone praxis-relay uses reqwest's standard environment. Keep both
    # cases explicit on POSIX, and restrict these overrides to the relay child.
    result={name:url for name in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy')}
    result.update(NO_PROXY=bypass,no_proxy=bypass)
    return result


def environment(relay):
    if not isinstance(relay,dict):raise ValueError('Relay settings must be an object')
    value=relay.get('proxy')
    if value is None:value={}
    if not isinstance(value,dict):raise ValueError('Relay proxy settings must be an object')
    mode=value.get('mode','system')
    if mode not in ('system','direct','proxy'):raise ValueError('Unknown relay network mode')
    result={'RELAY_NETWORK_MODE':mode,'RELAY_PROXY_URL':''}
    if mode=='system':return result
    if mode=='direct':
        result.update(standard_proxy_environment('','*'))
        return result
    protocol=value.get('protocol','http')
    if protocol not in ('http','https','socks5','socks5h'):raise ValueError('Proxy supports HTTP, HTTPS or SOCKS5')
    host=str(value.get('host') or '').strip()
    if not host or any(c in host for c in '/@?#\\ \t\r\n'):raise ValueError('Enter a proxy hostname or IP address without a URL path')
    try:port=int(value.get('port') or 0)
    except (TypeError,ValueError):raise ValueError('Proxy port must be a number') from None
    if not 1<=port<=65535:raise ValueError('Proxy port must be 1..65535')
    user=str(value.get('username') or '');password=str(value.get('password') or '')
    if password and not user:raise ValueError('Proxy password requires a username')
    authentication=urllib.parse.quote(user,safe='')+':'+urllib.parse.quote(password,safe='')+'@' if user else ''
    if ':' in host and not host.startswith('['):host='['+host+']'
    result['RELAY_PROXY_URL']=f'{protocol}://{authentication}{host}:{port}'
    try:
        parsed=urllib.parse.urlsplit(result['RELAY_PROXY_URL'])
        if not parsed.hostname or parsed.port!=port:raise ValueError()
    except ValueError:
        raise ValueError('Enter a valid proxy hostname or IPv6 address') from None
    result.update(standard_proxy_environment(result['RELAY_PROXY_URL'],'localhost,127.0.0.1,::1'))
    return result
