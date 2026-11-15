"""The optional MCP server uses the existing per-agent Computer permission switches."""
READ = {'ControlStatus', 'DisplayInventory', 'Snapshot', 'Screenshot', 'Wait', 'WaitFor', 'Scrape'}
APPS = {'App', 'Click', 'Type', 'Scroll', 'Move', 'Shortcut', 'MultiSelect', 'MultiEdit', 'Notification'}


def rights(name, args):
    """Return scopes and whether this operation needs the sovereign file-write gate."""
    if name=='ControlStatus' and args.get('release'):
        return {'computer.apps'},False
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
    from .windows_mcp_words import configured
    required, _ = rights(name, args)
    if not enabled(config):
        return configured(config,'computer_disabled')
    missing = required - set(scopes(config))
    return configured(config,'missing_scopes',scopes=', '.join(sorted(missing))) if missing else ''


def install(agent, tree, config, config_path, program, *, body_module=None):
    import windows_mcp_tools
    from . import windows_mcp_client
    if body_module is None:
        from . import body as body_module
    live = body_module._LiveScopes(config_path, config)
    def allowed(name, args):
        live.refresh()
        required, sovereign = rights(name, args)
        if not live.enabled:
            return {'code':'computer_disabled','values':{}}
        if not required <= set(live.scopes):
            return {'code':'missing_scopes','values':{'scopes':', '.join(sorted(required))}}
        if sovereign and not agent._is_sovereign_actor():
            return {'code':'sovereign','values':{}}
        return ''
    windows_mcp_tools.configure(lambda name, args: windows_mcp_client.call(program, tree, name, args,body_module=body_module), allowed)
