"""Resume the selected clean folder switch before an ordinary window attaches."""
from contextlib import ExitStack, contextmanager
import os
from pathlib import Path
import re
import time

from . import clean_update, session_host
from .package_notice import read, write


class Deferred(InterruptedError):
    """Existing owner intent/work keeps this prepared installation unstarted."""


def pending(program, operation=''):
    program = clean_update.plain(program)
    note = read(clean_update.plain(program/'python-migration.json'))
    if note.get('clean_switch') != clean_update.FORMAT or note.get('state') != 'prepared':
        return None
    if operation and note.get('id') != operation:
        raise ValueError('Clean startup operation differs from the selected migration')
    if not isinstance(note.get('id'), str) or not re.fullmatch('[0-9a-f]{32}', note['id']):
        raise ValueError('Clean startup operation identity is invalid')
    from .python_source_updater import prepared_install_path
    value = read(clean_update.plain(prepared_install_path(program)))
    old, program = clean_update.roots(note['old_root'], program)
    clean_update.identity(old, program, value)
    def preparation(item):
        return {key: field for key, field in item.items()
                if key not in ('app_retirement', 'startup_handoff')}
    if preparation(value['note']) != preparation(note):
        raise ValueError('Clean startup note differs from its prepared installation')
    if not clean_update.selected(old, program):
        raise ValueError('Clean folder startup needs the selected clean source policy')
    configured = session_host.control_path(program).parent.parent.parent
    if str(configured) not in note['data_paths']:
        raise ValueError('Clean startup selected data differs from its preparation')
    return value


def _check_stop(program, old=None, automatic=False):
    from . import owner_stop, session_exit
    if (owner_stop.stopped() or session_exit.requested(program)
            or (automatic and old is not None and session_exit.requested(old))):
        raise Deferred('Owner Stop/Exit retains the prepared update for a later launch')


def _settings_bindings(old, program, value):
    """Settings may evolve; the selected profiles/retention paths may not."""
    from .app_migration import discover
    from . import agents
    for raw in value['note']['old_data_paths']:
        data = clean_update.plain(raw)
        retained = any(data.is_relative_to(item['source']) for item in value['moves'])
        if data.is_relative_to(old) and data.exists() and not retained:
            raise Deferred('This prepared selection leaves previous agent data inside the old programme. '
                           'Reopen installation setup and retain that data before switching.')
    previous = discover(old)['agents']
    incoming = {row.config.relative_to(program).as_posix(): row for row in agents.roster(program)}
    if ({row['relative'] for row in previous} != set(incoming)
            or len(previous) != len(value['note']['data_paths'])):
        raise ValueError('Selected agent configurations changed since clean preparation')
    for index, row in enumerate(previous):
        source = clean_update.plain(row['tree'])
        if str(source) not in value['note']['old_data_paths']:
            raise ValueError('Previous selected profile changed since clean preparation')
        selected = Path(value['note']['data_paths'][index])
        if incoming[row['relative']].tree.resolve() != selected:
            raise ValueError('Incoming selected profile differs from the prepared data selection')
        if row['relative'] == 'helene.json':
            if source != Path(value['note']['old_data_paths'][0]):
                raise ValueError('Previous primary profile changed since clean preparation')
            continue  # Its explicit attached-data selection is already admitted.
        if (row['value'].get('mode') == 'remote' and not source.exists()
                and str(selected) in value.get('empty_data', [])):
            continue
        retained = next((item for item in value['moves'] if source.is_relative_to(item['source'])), None)
        expected = Path(retained['target'])/source.relative_to(retained['source']) if retained else source
        if selected != expected:
            raise ValueError('Incoming selected profile differs from the admitted retention path')


def _refresh_settings(old, program, value):
    from .essential_backup import configs
    from .program_state import directory
    from .python_source_updater import prepared_install_path
    from .private_files import write_private
    from .component_store import PINS
    folder = clean_update.plain(directory(program, 'clean-settings')/value['note']['id'])
    journal = clean_update.plain(folder/'refresh.json')
    previous = read(journal)
    binding = {'id':value['note']['id'], 'old':str(old), 'new':str(program)}
    if previous and any(previous.get(key) != item for key, item in binding.items()):
        raise ValueError('Settings refresh belongs to another clean handoff')
    if previous.get('state') == 'committed':
        return clean_update.identity(old, program, read(clean_update.plain(prepared_install_path(program))))
    _settings_bindings(old, program, value)
    paths = [*configs(program), *(program/name for name in
             ('agent-default.json', 'window-preferences.json', 'owner-stop.json', PINS))]
    allowed = {path.relative_to(program).as_posix() for path in paths}
    if not previous:
        previous = dict(binding, state='retaining', files=[path.relative_to(program).as_posix()
                        for path in paths if clean_update.plain(path).is_file()])
        write(journal, previous)
    for relative in previous['files']:
        if relative not in allowed:
            raise ValueError('Settings preimage escaped the selected configurations')
        source = clean_update.plain(program/relative)
        target = clean_update.plain(folder/'incoming'/relative)
        if not target.exists():
            write_private(target, source.read_bytes())
    # Same operation, exact data destinations and the existing path-rebinding
    # transform. Legacy preparations need no new baseline to retain preimages.
    primary = Path(value['note']['data_paths'][0])
    retained = next((item for item in value['moves'] if primary.is_relative_to(item['target'])), None)
    attached = Path(retained['source'])/primary.relative_to(retained['target']) if retained else primary
    value = clean_update.prepare(old, program, refresh=True, attached_data=attached)
    write(journal, dict(previous, state='committed'))
    return value


def _helper_alive(old, program, value):
    import psutil
    helper = value['note'].get('startup_helper', {})
    if any(helper.get(key) != item for key, item in
           (('old', str(old)), ('new', str(program)), ('operation', value['note']['id']))):
        return False
    if session_host.process_state(helper) != 'alive':
        return False
    try:
        process = psutil.Process(helper['pid'])
        if process.create_time() != helper['birth']:
            return False
        entry = clean_update.plain(helper['entry'])
        runtime = clean_update.plain(helper['runtime'])
        if runtime.is_relative_to(old) or entry.is_relative_to(old):
            return False
        arguments = process.cmdline()
        cwd = Path(process.cwd())
        return (Path(process.exe()).resolve() == runtime and entry.is_file()
                and any((Path(arg) if Path(arg).is_absolute() else cwd/arg).resolve() == entry
                        for arg in arguments[1:] if not arg.startswith('-')))
    except (psutil.Error, OSError, KeyError, ValueError, TypeError):
        return False


def _relocation_snapshot(old, program, value):
    """Rejoin an admitted producer without rediscovering already moved data."""
    from .first_start import FORMAT as FIRST_FORMAT, receipt
    from .component_store import _pins, PINS, export_references, _reference
    from . import agents
    from .app_migration import code_path
    witness = read(clean_update.plain(receipt(program)))
    if witness.get('state') == 'handoff_restored':
        return False
    if witness.get('format') != FIRST_FORMAT or witness.get('data') != value['note']['data_paths'][0]:
        return False
    bound = witness.get('clean_handoff')
    if bound and bound != clean_update.relocation_binding(value):
        raise ValueError('Relocation receipt belongs to another clean operation')
    if bound:
        if witness.get('state') not in ('handoff', 'completed'):
            return False
    elif witness.get('state') != 'completed' or not (value['moves'] or value.get('legacy_components') or value.get('empty_data')):
        return False  # A legacy external-data receipt is not a handoff proof.
    complete = True
    for row in [*value['moves'], *value.get('legacy_components', [])]:
        source, target = clean_update.plain(row['source']), clean_update.plain(row['target'])
        if source.exists() == target.exists():
            raise ValueError('Retained data is present in both locations or missing from both')
        complete = complete and target.exists()
    for data in value['note']['old_data_paths']:
        path = clean_update.plain(data)
        if path.is_relative_to(old) and path.exists() and not any(path.is_relative_to(row['source']) for row in value['moves']):
            return False  # The normal admission reports the unsafe legacy plan.
    for data in value.get('empty_data', []):
        path = clean_update.plain(data)
        if path.exists() and not path.is_dir():
            raise ValueError('Admitted remote cache is not a directory')
        complete = complete and path.is_dir()
    if not complete and (not bound or witness.get('state') == 'completed'):
        return False  # Restored paths cannot consume a completed witness.
    rows = agents.roster(program)
    if len(rows) != len(value['note']['data_paths']):
        raise ValueError('Relocated selected configurations changed')
    for row, expected in zip(rows, value['note']['data_paths']):
        if row.tree.resolve() != Path(expected):
            raise ValueError('Relocated selected data binding changed')
        config = agents.read_config(row.config)
        if config.get('mode') != 'remote' and code_path(row.config, config) != program/'tree':
            raise ValueError('Relocated selected executable source changed')
    expected_pins = witness.get('clean_pins', {})
    owners = {str(program), *value['note']['data_paths']}
    if bound and (not isinstance(expected_pins, dict) or set(expected_pins) != owners
                  or _pins(program) != expected_pins[str(program)]):
        raise ValueError('Relocated programme pins changed after handoff intent')
    pins_complete = True
    for data in value['note']['data_paths']:
        target = clean_update.plain(data)
        source = clean_update.data_source(value, target)
        current = target if target.exists() else source
        pins = _pins(current)
        update = next((item for item in value.get('data_pin_updates', []) if clean_update.plain(item['path']) == target/PINS), None)
        if update:
            allowed = [update['before'], update['after']] if complete and current == target else [update['before']]
            if pins not in allowed or (bound and expected_pins[str(target)] != update['after']):
                raise ValueError('Retained data pins changed outside the admitted operation')
            pins_complete = pins_complete and pins == update['after']
        elif bound and pins != expected_pins[str(target)]:
            raise ValueError('Relocated selected-data pins changed after handoff intent')
    if not bound and not pins_complete:
        return False
    if complete and pins_complete:
        for owner in [program, *map(Path, value['note']['data_paths'])]:
            for ref in export_references(owner)['pins'].values():
                if _reference(ref).is_relative_to(old):
                    raise ValueError('Retained component still belongs to the former programme')
    return True


def _quiet(old, value, helper=None):
    from . import migration_lifecycle
    if helper is None:migration_lifecycle.quiescent(old)
    else:migration_lifecycle.quiescent(old, exempt=helper)
    if os.name == 'nt' and migration_lifecycle.owned_processes(old):
        raise RuntimeError('Previous programme descendants have not confirmed graceful exit')
    for data in dict.fromkeys([*value['note']['old_data_paths'], *value['note']['data_paths']]):
        for relative in ('.desktop-owner.lock', 'memory/.control/desktop-session.lock'):
            if not migration_lifecycle.owner_lease_available(clean_update.plain(Path(data)/relative)):
                raise RuntimeError('Previous selected-data writer has not released ownership')


def _exit_old(old, program, value, descriptor, timeout, automatic):
    from .python_archive_update import require_idle_data
    from . import migration_lifecycle
    for data in value['note']['old_data_paths']:
        try:
            require_idle_data(Path(data))
        except RuntimeError as exc:
            raise Deferred(str(exc)) from exc
    _check_stop(program, old, automatic)
    # Both checks and the authenticated RPC stay under the same launch fence
    # used by attach. A different observed generation is never stopped.
    session_host._rpc(descriptor, 'status', timeout=5)
    session_host.retire_observers(old)
    current = read(clean_update.plain(session_host.control_path(old)))
    for key in ('format', 'program', 'tree', 'pid', 'birth', 'generation', 'origin', 'token'):
        if current.get(key) != descriptor.get(key):
            raise ValueError('Previous owner generation changed before clean startup')
    _check_stop(program, old, automatic)
    try:
        session_host._rpc(descriptor, 'exit', {'owner_exit': False}, timeout=45)
    except OSError:
        # A lost reply is resolved by this exact birth's disappearance, never
        # by sending Exit again to a replacement process.
        pass
    deadline = time.monotonic() + timeout
    while True:
        state = session_host.process_state(descriptor)
        if state == 'unknown':
            raise RuntimeError('Previous owner exit identity is unknown')
        if state in ('absent', 'changed'):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('Previous owner has not confirmed graceful exit; retry the prepared update')
        time.sleep(.05)
    # The registered readers already exited. This also handles older Python
    # windows with an authenticated exit surface, without a terminate fallback.
    if os.name == 'nt':
        migration_lifecycle.close_python(old, force=False)
    while True:
        try:
            _quiet(old, value)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.05)


@contextmanager
def handoff(program, operation='', *, timeout=30, automatic=False):
    """Hold data launch custody through profile binding and host admission.

    An updater may already hold NEW's installation lease while waiting for
    this very window. Its completed relocation can be consumed without taking
    that lease again. Ordinary folder launches acquire it and own retirement.
    """
    from .config_transaction import transaction
    from .phone_tunnel.platform_support import PlatformSupportError
    from python_migrate import installation_lock
    value = pending(program, operation)
    if value is None:
        yield False
        return
    old, program = clean_update.roots(value['old'], value['new'])
    old_target = clean_update.plain(Path(value['note']['old_data_paths'][0])/'memory/.control/desktop-session.json')
    targets = {clean_update.plain(Path(data)/'memory/.control/desktop-session.json')
               for data in [*value['note']['old_data_paths'], *value['note']['data_paths']]}
    targets.add(session_host.control_path(program))
    with ExitStack() as held:
        deadline = time.monotonic() + 180
        for target in sorted(targets, key=lambda path: os.path.normcase(
                str(path.with_name('desktop-session-launch').resolve()))):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Prepared installation is still awaiting selected-data launch custody')
            held.enter_context(transaction(target.with_name('desktop-session-launch'), timeout_sec=remaining))
        target = clean_update.plain(session_host.control_path(program))
        if target not in targets:
            raise ValueError('Selected data changed while awaiting clean startup custody')
        latest = read(clean_update.plain(program/'python-migration.json'))
        for key in ('id', 'old_root', 'new_root', 'old_data_paths', 'data_paths'):
            if latest.get(key) != value['note'].get(key):
                raise ValueError('Selected migration data changed while awaiting clean startup custody')
        incoming = read(target)
        incoming_state = session_host.process_state(incoming) if incoming else 'absent'
        if incoming_state == 'unknown':
            raise RuntimeError('Selected incoming-data owner identity is unknown')
        if incoming_state == 'alive' and target != old_target and incoming.get('program') != str(program):
            raise ValueError('Incoming data is owned by an unrelated programme')
        if incoming_state == 'alive' and incoming.get('program') == str(program):
            if incoming.get('format') != session_host.FORMAT or incoming.get('tree') != str(target.parent.parent.parent):
                raise ValueError('Incoming session data identity differs')
            yield True
            return
        value = pending(program, operation)
        if value is None:
            raise ValueError('Prepared clean startup changed while waiting')
        fresh_targets = {clean_update.plain(Path(data)/'memory/.control/desktop-session.json')
                         for data in [*value['note']['old_data_paths'], *value['note']['data_paths']]}
        if not fresh_targets.issubset(targets):
            raise ValueError('Selected migration needs an unheld data launch fence')
        descriptor = read(old_target)
        state = session_host.process_state(descriptor) if descriptor else 'absent'
        if state == 'unknown':
            raise RuntimeError('Previous session identity is unknown; prepared update retained')
        if state == 'alive':
            if (descriptor.get('format') != session_host.FORMAT
                    or descriptor.get('tree') != str(old_target.parent.parent.parent)):
                raise ValueError('Previous session does not own the selected migration data')
            if descriptor.get('program') == str(program) and target == old_target:
                yield True  # A concurrent launch already admitted this exact NEW.
                return
            if descriptor.get('program') != str(old):
                raise ValueError('Selected data is owned by an unrelated programme')
        try:
            held.enter_context(installation_lock(program))
        except PlatformSupportError:
            # Existing helper admission retains its lease through UI readiness.
            # Never move its data, stop an owner or adopt its retirement here.
            if not _helper_alive(old, program, value):
                raise RuntimeError('Another installer holds this programme; its startup custody is unconfirmed')
            if state == 'alive':
                raise RuntimeError('Active installer has not released the previous owner')
            _quiet(old, value, value['note']['startup_helper'])
            for row in [*value.get('moves', []), *value.get('legacy_components', [])]:
                if Path(row['source']).exists() or not Path(row['target']).exists():
                    raise RuntimeError('Active installer has not completed its data handoff')
            yield True
            return
        _check_stop(program, old, automatic)
        if state == 'alive':
            _settings_bindings(old, program, value)
            if session_host.control_path(old) != old_target:
                raise ValueError('Previous selected data differs from the prepared migration')
            _exit_old(old, program, value, descriptor, timeout, automatic)
            staged = False  # The current OLD owner remains settings authority.
        else:
            _quiet(old, value)
            staged = _relocation_snapshot(old, program, value)
            if not staged:
                _settings_bindings(old, program, value)
        _check_stop(program, old, automatic)
        if not staged:
            value = _refresh_settings(old, program, value)
        _check_stop(program, old, automatic)
        clean_update.relocate(old, program, value)
        note = dict(value['note'], app_retirement=True,
                    startup_handoff={key: descriptor.get(key) for key in ('pid', 'birth', 'generation')})
        write(program/'python-migration.json', note)
        yield True
