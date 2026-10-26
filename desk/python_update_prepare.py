"""Prepare an existing installation's merge with the incoming trusted runtime.

Older updaters return before launching the incoming helper on a conflict. This
explicit bridge only prepares and reviews their exact source plan. The owner
then retries the usual Update button; no installation or restart occurs here.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import threading
import uuid


def prepare_existing(old, new, progress=None, cancelled=None):
    from python_migrate import installation_lock
    from localharness import code_transition, source_update_host, update_profile
    from localharness.package_notice import read, version
    from localharness.python_platform import target
    old, new = Path(old).resolve(), Path(new).resolve()
    if old == new or old in new.parents or new in old.parents:
        raise ValueError('Choose the existing installation and a separate extracted new release')
    if not (old / 'python-app.json').is_file() or not (old / 'helene.json').is_file():
        raise ValueError('The selected folder is not a configured Hélène installation')
    old_version, new_version = (version(read(root / 'python-app.json').get('version')) for root in (old, new))
    if (not old_version or not new_version or '-' in old_version or '-' in new_version
            or tuple(map(int, new_version.split('+')[0].split('.'))) <= tuple(map(int, old_version.split('+')[0].split('.')))):
        raise ValueError('Select a newer stable release for this installation')
    if target(old) != target(new):
        raise ValueError('Incoming release and existing installation are for different platforms')
    source_update_host.verified_runtime(new)
    with installation_lock(old, timeout_sec=1):
        before = update_profile.selection(old)
        plan = code_transition.preflight(old, new, notify=False)
        source_update_host.review(old, new, plan['plans'], progress, cancelled)
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError('Update preparation cancelled; previous application retained')
        if update_profile.selection(old) != before:
            raise ValueError('Selected source or settings changed; prepare a fresh update')
        code_transition.validate(new)
        result = code_transition.preflight(old, new, notify=False)
        if result['state'] != 'ready':
            raise RuntimeError(result['message'])
        if progress:
            progress.phase('review_prepared', source_review_ready=True,
                message='Вернитесь в прежнее окно и нажмите «Обновить» для этого же выпуска.')
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old', type=Path, required=True)
    parser.add_argument('--browser', action='store_true')
    args = parser.parse_args(argv)
    new = Path(__file__).resolve().parent.parent
    from localharness.update_progress import Progress
    from localharness.update_window import run
    token = uuid.uuid4().hex
    progress = Progress(args.old.resolve().parent / ('.helene-update-' + token + '.progress.json'),
                        token, args.old)
    progress.phase('code', label='Подготавливаю совместимое обновление')
    ready = threading.Event()
    def transaction():
        if not ready.wait(15):
            raise TimeoutError('Update preparation window did not become ready')
        prepare_existing(args.old, new, progress)
        return 0
    return run(transaction, progress.path, ready, browser=args.browser)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # pythonw has no terminal. Keep even failures before observer creation
        # visible without importing or starting the installed owner's code.
        if sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'Hélène — подготовка обновления', 0x10)
        raise
