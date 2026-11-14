"""The optional MCP server uses the existing per-agent Computer permission switches."""
READ = {'ControlStatus', 'DisplayInventory', 'Snapshot', 'Screenshot', 'Wait', 'WaitFor', 'Scrape'}
APPS = {'App', 'Click', 'Type', 'Scroll', 'Move', 'Shortcut', 'MultiSelect', 'MultiEdit', 'Notification'}


def rights(name, args):
    """Return scopes and whether this operation needs the sovereign file-write gate."""
    if name in READ:
        if name in ('Snapshot', 'Screenshot') and args.get('save_path'):
            return {'computer.read', 'computer.files'}, True
        return {'computer.read'}, False
    if name in APPS:
        return {'computer.apps'}, False
    if name == 'PowerShell':
        return {'computer.process'}, False
    if name == 'Process':
        return ({'computer.read'}, False) if args.get('mode') in (None, 'list', 'info') else ({'computer.process'}, False)
    if name == 'Clipboard':
        return ({'computer.read'}, False) if args.get('mode') in (None, 'get') else ({'computer.apps'}, False)
    if name == 'FileSystem':
        return {'computer.files'}, args.get('mode') not in ('read', 'list', 'search', 'info')
    if name == 'Registry':
        return ({'computer.read'}, False) if args.get('mode') in ('get', 'list') else ({'computer.process'}, True)
    raise ValueError('Unknown Windows MCP tool')


def refusal(config, name, args):
    from .body import enabled, scopes
    required, _ = rights(name, args)
    if not enabled(config):
        return 'Windows MCP: включи «Управление компьютером» в Настройках.'
    missing = required - set(scopes(config))
    return ('Windows MCP: владелец не выдал права ' + ', '.join(sorted(missing))) if missing else ''


def install(agent, tree, config, config_path, program):
    import windows_mcp_tools
    from . import windows_mcp_client
    from .body import _LiveScopes
    live = _LiveScopes(config_path, config)
    def allowed(name, args):
        live.refresh()
        required, sovereign = rights(name, args)
        if not live.enabled or not required <= set(live.scopes):
            return 'Windows MCP: не выданы права Управления компьютером (' + ', '.join(sorted(required)) + ').'
        if sovereign and not agent._is_sovereign_actor():
            return 'Windows MCP: запись файлов/реестра доступна владельцу или самому агенту.'
        return ''
    windows_mcp_tools.configure(lambda name, args: windows_mcp_client.call(program, tree, name, args), allowed)
