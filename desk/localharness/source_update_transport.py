"""Keep the selected managed model relay alive during isolated update review.

Legacy owners close before preparation. Their child relay closes with them;
review needs its own bounded custody, never an ordinary agent/controller.
"""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
import time

from . import desktop_supervisor as supervisor, python_relay
from .package_notice import write


def uses_managed_relay(config, effective):
    relay = config.get('relay') or {}
    if relay.get('enabled') is not True:
        return False
    role = (effective.get('roles') or {}).get('voice') or {}
    framework = (effective.get('frameworks') or {}).get(role.get('framework')) or {}
    address = urlsplit(str(framework.get('base_url') or ''))
    return (address.scheme == 'http' and address.hostname in ('127.0.0.1', 'localhost')
            and not address.username and not address.password
            and (address.port or 80) == (relay.get('port') or 5011))


class RelayLease:
    def __init__(self, agent, spec, directory, stopped, *, timeout=20):
        self.agent = SimpleNamespace(tree=Path(agent['tree']), config=Path(agent['config']))
        self.spec, self.stopped, self.timeout = spec, stopped, timeout
        self.record = Path(directory) / 'relay-transport.json'
        self.child = None
        self.owned = False
        self.borrowed = False
        self.pid = None

    def _save(self, state):
        write(self.record, {'format': 'helene-review-relay.v1', 'state': state,
                           'owned': self.owned, 'pid': self.pid, 'port': self.spec.port})

    def _ready(self):
        try:
            if not supervisor._port_busy(self.spec.port):
                return False
            # The private control listener has its own ephemeral port. Its
            # authenticated status reports the configured model API port.
            status = python_relay.control(self.agent, 'status', timeout_sec=.5)
            return status.get('port') == self.spec.port and status.get('running') is not False
        except (OSError, ValueError):
            return False

    def _admit(self):
        if self.stopped():
            raise RuntimeError('Owner Stop/Pause forbids starting update relay')

    def __enter__(self):
        self._admit()
        # A capable desktop may still be open during preparation. Borrow only
        # the authenticated control endpoint of this exact selected tree.
        deadline = time.monotonic() + min(3, self.timeout)
        while supervisor._port_busy(self.spec.port):
            self._admit()
            if self._ready():
                self.borrowed = True
                self._save('borrowed')
                return self
            if time.monotonic() >= deadline:
                raise RuntimeError('Configured model relay port is occupied but its private control identity is unavailable')
            time.sleep(.05)
        try:
            self.child = supervisor.JobChild.spawn(list(self.spec.argv), cwd=self.spec.cwd,
                env=self.spec.env, log=self.record.with_suffix('.log'), stopped=self.stopped)
            self.owned, self.pid = True, self.child.pid
            self._save('starting')
            deadline = time.monotonic() + self.timeout
            while True:
                self._admit()
                if self.child.poll() is not None:
                    raise RuntimeError('Update model relay exited before readiness; previous application retained')
                if self._ready():
                    self._save('ready')
                    return self
                if time.monotonic() >= deadline:
                    raise TimeoutError('Update model relay did not confirm its private control endpoint')
                time.sleep(.05)
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise

    def check(self):
        self._admit()
        if self.child is not None and self.child.poll() is not None:
            raise RuntimeError('Update model relay exited during review; previous application retained')

    def close(self):
        if self.child is None:
            if not self.owned:
                self._save('borrowed-retained' if self.borrowed else 'not-started')
            return
        try:
            self.child.stop()  # measured custody; never stop a borrowed relay
        except Exception:
            self._save('cleanup-unconfirmed')
            raise
        self._save('stopped')
        self.child = None

    def __exit__(self, _kind, error, _trace):
        try:
            self.close()
        except Exception as cleanup:
            if error is not None:
                raise RuntimeError(f'{type(error).__name__}: {error}; relay cleanup remains unconfirmed: {cleanup}') from error
            raise


@contextmanager
def review_transport(agent, program, directory, effective, stopped):
    cfg = agent['value']
    if not uses_managed_relay(cfg, effective):
        yield None
        return
    base = Path(agent['config']).parent
    selected = cfg.get('python')
    python = Path(supervisor.sys.executable) if selected in (None, '', 'python') else supervisor._resolve(base, str(selected))
    spec = supervisor.relay_spec(Path(agent['config']), cfg, Path(agent['tree']), python,
                                 install_base=Path(program))
    if spec is None:
        raise RuntimeError('Configured managed model relay has no launch specification')
    with RelayLease(agent, spec, directory, stopped) as lease:
        yield lease
