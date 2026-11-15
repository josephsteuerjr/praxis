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


def call(program, tree, name, arguments):
    try:
        return invoke(program, 'windows_mcp_call', dict(name=name, arguments=arguments), tree=tree, timeout=190)
    except (OSError, ValueError, RuntimeError) as exc:
        return words.failure(program,'unconfirmed',in_doubt=True,
            cause=exc.spec if isinstance(exc,words.Error) else None,
            detail='' if isinstance(exc,words.Error) else str(exc))
