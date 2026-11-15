"""Fixed paired-desktop MCP operations; no URLs, tokens or tool aliases in payloads."""
import json
from .windows_mcp_access import READ,APPS,rights

NAMES=frozenset(READ|APPS|{'PowerShell','Process','Clipboard','FileSystem','Registry'})


def catalog(rows):
    if (not isinstance(rows,list) or len(rows)!=len(NAMES)
            or any(not isinstance(row,dict) or not isinstance(row.get('name'),str)
                   or not isinstance(row.get('inputSchema'),dict) for row in rows)
            or {row['name'] for row in rows}!=NAMES):
        raise ValueError('Windows MCP catalogue differs from the pinned 21 tools')
    return {row['name']:row for row in rows}


def invocation(payload):
    if not isinstance(payload,dict) or set(payload)!={'name','arguments'}:
        raise ValueError('Invalid paired Windows MCP invocation')
    name,arguments=payload['name'],payload['arguments']
    if not isinstance(name,str) or name not in NAMES or not isinstance(arguments,dict):
        raise ValueError('Unknown Windows MCP tool or invalid argument object')
    # Strict JSON, including finite numeric values; do not reinterpret literals.
    json.dumps(arguments,allow_nan=False)
    if name=='Scrape':arguments={**arguments,'use_sampling':False}
    return name,arguments


def required(payload):
    name,arguments=invocation(payload)
    return rights(name,arguments)[0]


def authorize_profile(owner,args,*,call=False):
    from . import owner_stop
    from .remote_computer_client import grant
    if owner.exiting or owner.stop.is_set() or owner_stop.stopped():
        raise PermissionError('Computer stopped by owner')
    expected={'device_id','base'}|({'name','arguments'} if call else set())
    if not isinstance(args,dict) or set(args)!=expected:
        raise ValueError('Invalid paired Windows MCP command')
    current=grant(owner.current().config)
    if args['device_id']!=current['id'] or args['base']!=current['base']:
        raise PermissionError('Desktop pairing changed')
    if call:
        name,arguments=invocation({key:args[key] for key in ('name','arguments')})
        missing=rights(name,arguments)[0]-set(current['scopes'])
        if missing:raise PermissionError('Local owner has revoked '+', '.join(sorted(missing)))
        return name,arguments
    return current
