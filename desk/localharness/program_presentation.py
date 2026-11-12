"""The full programme shown in Telegram's MiniApp: the same commands, the phone's own window.

12.11 (owner): «полностью программа в миниаппе», but a phone tap must not open a
folder, a system dialog or an administrator prompt on the computer's screen, and
the computer's channel key never travels to the phone. Everything else is the
same controller call as in the native window.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Actions on the computer's own desktop: windows, dialogs, tray, OS panes.
COMPUTER_ONLY = {
    'reveal_path': 'Показать файл в папке можно на компьютере.',
    'open_privacy_pane': 'Системные разрешения открываются на компьютере.',
    'install_service': 'Службу ставят на компьютере: система спросит подтверждение там.',
    'remove_service': 'Службу снимают на компьютере: система спросит подтверждение там.',
    'migration_wizard': 'Мастер переноса открывается на компьютере.',
    'tray_open_main': 'Значок у часов — на компьютере.', 'tray_hide': 'Значок у часов — на компьютере.',
    'tray_fit': 'Значок у часов — на компьютере.', 'tray_exit': 'Значок у часов — на компьютере.',
    'tray_context': 'Значок у часов — на компьютере.',
}
#: Host transport credentials: the remote channel key stays on the computer.
HOST_SECRETS = ('key',)
KEPT = '__helene_kept_on_computer__'


def refusal(command: str, arguments: dict) -> str:
    """A human reason when the command belongs to the computer's desktop, else ''."""
    if command in COMPUTER_ONLY:
        return 'На компьютере: ' + COMPUTER_ONLY[command]
    if command == 'open_path':
        path = str(arguments.get('path') or '')
        if not path.lower().startswith(('https://', 'http://')):
            return 'На компьютере: файл открывается там — ' + path
    return ''


def redact(loaded):
    """config_load for the phone: settings stay editable, host keys stay home."""
    if not isinstance(loaded, dict) or not isinstance(loaded.get('config'), dict):
        return loaded
    config = dict(loaded['config'])
    for name in HOST_SECRETS:
        if config.get(name):
            config[name] = KEPT
    return {**loaded, 'config': config}


def restore(document, path):
    """config_save from the phone: a kept marker means "unchanged", never the literal."""
    if not isinstance(document, str):
        return document
    value = json.loads(document)
    if not isinstance(value, dict):
        return document
    kept = [name for name in HOST_SECRETS if value.get(name) == KEPT]
    if not kept:
        return document
    from .config_transaction import read_snapshot
    try:
        current, _ = read_snapshot(Path(path))
    except FileNotFoundError:
        current = {}
    for name in kept:
        if current.get(name):
            value[name] = current[name]
        else:
            value.pop(name, None)
    return json.dumps(value, ensure_ascii=False)


def restart_target(observers: dict):
    """One programme window on the computer performs a full restart, never several."""
    return sorted(observers)[0] if observers else None
