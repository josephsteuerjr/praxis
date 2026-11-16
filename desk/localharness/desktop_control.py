"""The process owner serves controls even while a runner is still booting."""
import datetime as dt
from pathlib import Path
import sys

from . import runner_startup, ui_language


class Control:
    def __init__(self, stop_children, child_state):
        from deskd import control
        self.protocol = control
        self.started = dt.datetime.now(dt.timezone.utc).isoformat()
        self.stop_children = stop_children
        self.child_state = child_state
        self.export_errors = {}
        self.handled = {}

    def publish(self, tree, method, *args):
        key = (str(tree), method)
        try:
            getattr(self.protocol, method)(tree, *args)
        except OSError as exc:
            problem = type(exc).__name__ + ': ' + str(exc)
            if self.export_errors.get(key) != problem:
                print('[python-supervisor] control export unavailable: ' + problem,
                      file=sys.stderr, flush=True)
            self.export_errors[key] = problem
        else:
            self.export_errors.pop(key, None)

    def tick(self, installed, active, policy, stopped):
        refreshed = False
        for agent, (tree, specs) in list(installed.items()):
            if stopped():
                break
            children = []
            for key, spec in specs.items():
                child = active.get(key)
                state = policy.children[key]
                row = {'id': key, 'name': spec.role, 'role': spec.role,
                       'pid': child.pid if child else None, 'alive': child is not None,
                       'falls': len(state.falls), 'halted': spec.unavailable or
                       ('exit' if state.halted else ''), 'since_utc': ''}
                if spec.role == 'runner':
                    row.update(runner_startup.observe(tree, row['pid']))
                children.append(row)
            self.publish(tree, 'beat', 'python-owner', self.started, children)
            request = self.protocol.take_request(tree)
            if not request:
                continue
            identity = request.get('id')
            # A Windows sharing denial can leave a consumed request pathname.
            # Never execute that same request again in this owner generation.
            if identity and self.handled.get(agent) == identity:
                continue
            self.handled[agent] = identity
            target = request.get('target')
            roles = {'runner': {'runner'}, 'relay': {'relay'},
                     'all': {'runner', 'channel', 'relay', 'xtts'}}.get(target)
            config = Path(next(iter(specs.values())).env.get('HELENE_CONFIG', tree / 'helene.json')) if specs else tree / 'helene.json'
            language = ui_language.for_root(config.parent)
            if request.get('action') != 'restart' or not roles:
                self.publish(tree, 'receipt', request, False,
                    ui_language.text('supervisor.restart.invalid', language))
                continue
            selected = {key: child for key, child in active.items()
                        if key in specs and specs[key].role in roles}
            try:
                self.stop_children(selected)
            except (OSError, RuntimeError) as exc:
                self.publish(tree, 'receipt', request, False,
                    ui_language.text('supervisor.restart.failed', language, {'detail': str(exc)}))
                raise  # uncertain ownership must never admit another generation
            for key, spec in specs.items():
                if spec.role in roles:
                    active.pop(key, None)
                    policy.children[key] = self.child_state(spec.role)
            self.publish(tree, 'receipt', request, True,
                ui_language.text('supervisor.restart.accepted', language))
            refreshed = True
        return refreshed
