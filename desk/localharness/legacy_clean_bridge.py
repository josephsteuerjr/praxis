"""Move an old POSIX sender's nested clean candidate through one owned copy.

The admitted original helper keeps its runtime and receipt until a normal
external updater admits custody. That updater waits for this helper to exit,
then performs the ordinary clean switch, including retirement from NEW.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from .package_notice import read, write

FORMAT = 'helene-legacy-clean-bridge.v1'


def required(old, new, prepared, *, posix=None):
    from . import clean_update
    old, new = Path(old).resolve(), Path(new).resolve()
    return ((os.name != 'nt' if posix is None else posix)
        and new.is_relative_to(old) and new != old
        and prepared.get('format') == 'helene-prepared-install.v1'
        and 'folder_switch' not in prepared
        and prepared.get('old') == str(old) and prepared.get('new') == str(new)
        and prepared.get('note', {}).get('source_policy') == 'clean'
        and clean_update.selected(old, new))


def location(old, source):
    from .program_state import staging
    identity = hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:16]
    return staging(old, 'legacy-clean-' + identity, folder_switch=True)


def journal(old, source):
    return location(old, source) / 'legacy-clean-bridge.json'


def _committed(target):
    note = read(Path(target) / 'python-migration.json')
    return bool(note.get('startup_proof')) or note.get('state') in ('retiring_old', 'completed')


def can_reopen(old, source):
    """A live or uncertain external custodian may still be starting NEW."""
    from .session_host import process_state
    from .clean_update import directory_identity
    record = journal(old, source)
    value = read(record)
    if _committed(location(old, source) / 'Helene'):
        return False
    try:
        if record.exists() and (value.get('format') != FORMAT or value.get('old') != str(old)
                or value.get('source') != str(source) or value.get('old_identity') != directory_identity(old)
                or value.get('source_identity') != directory_identity(source)):
            return False
    except (OSError, ValueError):
        return False
    if value.get('state') == 'launch_pending':
        return False
    return not value.get('helper') or process_state(value['helper']) in ('absent', 'changed')


def _bound(old, source, prepared):
    from . import clean_update
    binding = {'format': FORMAT, 'old': str(old), 'source': str(source), 'new': str(location(old, source) / 'Helene'),
        'operation': prepared['note']['id'], 'old_identity': clean_update.directory_identity(old),
        'source_identity': clean_update.directory_identity(source)}
    record = journal(old, source)
    value = read(record)
    if record.exists() and any(value.get(k) != v for k, v in binding.items()):
        raise ValueError('Legacy clean bridge identity differs')
    if value.get('state') == 'launch_pending' and not value.get('helper'):
        raise RuntimeError('Legacy clean updater launch is still uncertain')
    return binding, value


def materialize(old, source, prepared, stopped):
    """Copy needed files only; the journal owns any removable partial target."""
    from . import clean_update, parallel_files
    from .python_source_updater import prepared_install_path
    from .update_source import choice_path
    old, source = clean_update.plain(old), clean_update.plain(source)
    stage = location(old, source)
    target = stage / 'Helene'
    clean_update.roots(old, target)
    record = journal(old, source)
    binding, value = _bound(old, source, prepared)
    if stopped():
        raise InterruptedError('Owner stopped before legacy clean copy')
    if value.get('helper'):
        from .session_host import process_state
        if process_state(value['helper']) not in ('absent', 'changed'):
            raise RuntimeError('Legacy clean bridge still has an external updater')
        if not _committed(target):
            from . import migration_lifecycle
            migration_lifecycle.quiescent(target)
    if target.exists():
        if clean_update.directory_identity(target) != value.get('target_identity'):
            raise ValueError('Legacy clean copy target is not journal-owned')
        if value.get('state') == 'copying':
            shutil.rmtree(target)
    if not target.exists():
        if value.get('state') not in (None, 'copying'):
            raise ValueError('Legacy clean candidate disappeared')
        target.mkdir(parents=True)
        value = dict(binding, state='copying', target_identity=clean_update.directory_identity(target))
        write(record, value)
        parallel_files.copytree(source, target, dirs_exist_ok=True)
        value['state'] = 'copied'
        write(record, value)
    if stopped():
        raise InterruptedError('Owner stopped before legacy clean preparation')
    # The selected policy follows its copied candidate. No programme content
    # or historical preparation inventory is read or compared here.
    write(choice_path(target), {'format': 'helene-source-choice.v1', 'old': str(old),
        'new': str(target), 'mode': 'clean', 'by': 'updater', 'legacy_bridge': str(record)})
    current = read(prepared_install_path(target))
    if not current:
        current = clean_update.prepare(old, target)
    clean_update.identity(old, target, current)
    value['state'] = 'prepared'
    write(record, value)
    return stage, target, value


def handoff(args, prepared, report, stopped):
    """The old sender has admitted us and exited before this function runs."""
    from python_migrate import installation_lock
    from . import clean_update, migration_lifecycle
    from .python_updates import await_admission
    from .python_source_updater import _job_report
    from .update_process import interpreter_for, background_flags, permanent_environment
    from .session_host import process_state
    old, source = args.old.resolve(), args.new.resolve()
    stage = location(old, source)
    clean_update.roots(old, stage / 'Helene')
    # This external lease serializes partial-copy recovery and helper launch.
    with installation_lock(stage):
        _, value = _bound(old, source, prepared)
        if value.get('helper') and process_state(value['helper']) not in ('absent', 'changed'):
            receipt = read(stage / value['admission_name'])
            if (process_state(value['helper']) != 'alive' or receipt.get('pid') != value['helper']['pid']
                    or receipt.get('birth') != value['helper']['birth'] or receipt.get('target') != str(old)):
                raise RuntimeError('Existing external updater admission is still uncertain')
            from .python_source_updater import prepared_install_path
            _job_report(args, 'handed_off', helper=receipt, staging=str(stage),
                progress=read(prepared_install_path(stage / 'Helene')).get('progress', ''))
            return 0
        if stopped():
            raise InterruptedError('Owner stopped before legacy clean copy')
        migration_lifecycle.stop(old)
        migration_lifecycle.quiescent(old)
        report('copying_program', legacy_clean_bridge=True)
        stage, target, value = materialize(old, source, prepared, stopped)
        if stopped():
            raise InterruptedError('Owner stopped before external clean handoff')
        job = getattr(args, 'job', None)
        from .python_source_updater import prepared_install_path
        from .program_state import progress as progress_path
        from .update_progress import Progress
        current_prepared = read(prepared_install_path(target))
        operation = current_prepared['note']['id']
        progress = Progress(progress_path(target, operation), operation, target)
        progress.phase('prepared', preparation_completed=True, legacy_clean_bridge=True)
        current_prepared['progress'] = str(progress.path)
        write(prepared_install_path(target), current_prepared)
        if job:
            from . import desktop_update_request as jobs
            current = read(job)
            if current.get('id') != args.job_id:
                raise ValueError('Legacy clean bridge update job differs')
            jobs.bind_incoming(current, target)
        name = 'updater-admitted-' + uuid.uuid4().hex + '.json'
        helper = stage / 'python_source_updater.py'
        shutil.copy2(target / 'app/localharness/python_source_updater.py', helper)
        command = [str(interpreter_for(target)), str(helper), '--old', str(old), '--new', str(target),
            '--staging', str(stage), '--pid', str(os.getpid()), '--source-mode', 'clean', '--admission-name', name]
        if args.browser:
            command.append('--browser')
        if job:
            command.extend(['--job', str(job), '--job-id', args.job_id])
        value.update(state='launch_pending', admission_name=name)
        value.pop('helper', None)
        write(journal(old, source), value)
        try:
            with (stage / 'update.log').open('ab') as output:
                child = subprocess.Popen(command, cwd=stage, env=permanent_environment(), stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=background_flags(), start_new_session=os.name != 'nt')
        except OSError:
            value['state'] = 'prepared'
            write(journal(old, source), value)
            raise
        import psutil
        try:
            birth = psutil.Process(child.pid).create_time()
        except psutil.NoSuchProcess:
            if child.poll() is not None:
                value.update(state='prepared', failed_launch_exit=child.returncode)
                write(journal(old, source), value)
            raise RuntimeError('External clean helper exited before admission')
        value.update(state='helper_spawned', helper={'pid': child.pid, 'birth': birth}, admission_name=name)
        write(journal(old, source), value)
        admitted = await_admission(child, stage, old, receipt_name=name)
        value.update(state='handed_off', helper=admitted)
        write(journal(old, source), value)
        _job_report(args, 'handed_off', helper=admitted, staging=str(stage), progress=str(progress.path))
        # The normal external updater waits for our PID. It performs all data
        # moves and retirement only after this helper releases OLD and exits.
        report('handoff', external_helper=admitted['pid'], legacy_clean_bridge=True)
        return 0
