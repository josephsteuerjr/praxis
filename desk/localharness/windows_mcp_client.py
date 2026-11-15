"""Core/UI access to the existing authenticated programme session, never a new owner."""
from pathlib import Path
from . import windows_mcp_words as words


def invoke(program, command, arguments=None, *, tree=None, timeout=10):
    from . import session_host, agents
    from .package_notice import read
    root = Path(program).resolve()
    descriptor = read(session_host.control_path(root))
    if (descriptor.get('program') != str(root) or session_host.process_state(descriptor) != 'alive'):
        raise words.Error(root,'app_unavailable')
    selected = None
    if tree is not None:
        selected = next((row.id for row in agents.roster(root) if row.tree.resolve() == Path(tree).resolve()), None)
        if selected is None:
            raise words.Error(root,'unknown_caller')
    return session_host._rpc(descriptor, 'invoke', dict(command=command, arguments=arguments or {}, agent=selected), timeout=timeout)


def component_status(program):
    from .windows_mcp_component import status
    row = status(program)
    if row['installed']:
        try:
            return invoke(program, 'windows_mcp_status', timeout=3)
        except (OSError, ValueError, RuntimeError) as exc:
            row['runtime'] = dict(state='stopped', running=False, tools=0, error=str(exc))
    return row


def remote_request(program,body_module,operation,name=None,arguments=None):
    """One attempt to the explicit selected desktop; never local fallback/replay."""
    target=body_module._BODY
    if not target.metadata(timeout=2,force=True) or not body_module.STATE.get('connected'):
        return words.failure(program,'remote_unavailable')
    device=body_module._settings()[2]
    import re
    if not isinstance(device,str) or not re.fullmatch('[a-f0-9]{32}',device):
        return words.failure(program,'remote_unavailable')
    payload=None
    if operation=='call':
        from .windows_mcp_remote import invocation
        name,arguments=invocation({'name':name,'arguments':arguments})
        payload={'name':name,'arguments':arguments}
    elif operation!='status':
        raise ValueError('Unknown paired Windows MCP operation')
    result=body_module._raw_request('POST' if operation=='call' else 'GET',
        '/v1/controller/'+device+'/windows-mcp/'+operation,payload,timeout=125 if operation=='call' else 5)
    if not isinstance(result,dict):return words.failure(program,'unconfirmed',in_doubt=True)
    if result.get('code')=='owner_refused' and result.get('dispatched') is False:
        return words.failure(program,'remote_refused',detail=str(result.get('error') or ''))
    if result.get('host')!='windows' or result.get('device_id')!=device or not isinstance(result.get('result'),dict):
        return words.failure(program,'unconfirmed',in_doubt=True,detail=str(result.get('error') or ''))
    return result['result']


def call(program, tree, name, arguments, *, body_module=None):
    try:
        if body_module is not None and isinstance(body_module._BODY,body_module.RemoteBody):
            return remote_request(program,body_module,'call',name,arguments)
        return invoke(program, 'windows_mcp_call', dict(name=name, arguments=arguments), tree=tree, timeout=190)
    except (OSError, ValueError, RuntimeError) as exc:
        return words.failure(program,'unconfirmed',in_doubt=True,
            cause=exc.spec if isinstance(exc,words.Error) else None,
            detail='' if isinstance(exc,words.Error) else str(exc))
