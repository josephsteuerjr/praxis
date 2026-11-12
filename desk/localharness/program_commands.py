"""Presentation-independent programme commands for desktop and MiniApp."""
UNHANDLED = object()


def invoke(root, program, command, args):
    if command in ('window_preferences', 'appearance_read', 'appearance_patch', 'appearance_reset'):
        from . import window_preferences as preferences
        if command == 'appearance_read': return preferences.read(root)
        if command == 'appearance_reset': return preferences.reset(root, args.get('seen'))
        if command == 'appearance_patch': return preferences.change(root, args.get('patch'), args.get('seen'), appearance_only=True)
        return preferences.change(root, {args.get('key'): args.get('value')}, args.get('seen'))
    if command == 'components_status':
        from . import standalone_relay, component_downloads
        from .server_bridge import component
        return [standalone_relay.status(program),component()] + component_downloads.status(program)
    if command in ('backup_now', 'backup_list'):
        from . import essential_backup
        return (essential_backup.snapshot(root, essential_backup.destination(root))
                if command == 'backup_now' else essential_backup.listing(root))
    return UNHANDLED
