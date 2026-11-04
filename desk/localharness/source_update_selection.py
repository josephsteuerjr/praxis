"""Incoming-host test selection: contract floor, changed suites and importers.

The full recipe remains the release inventory. This policy selects execution,
never review authority, and binds all three merge deltas, not just local edits.
Unknown non-Python/dynamic effects request full; explicit widening only adds.
"""
from pathlib import Path, PurePosixPath
import ast
import hashlib
from functools import lru_cache

FLOOR = frozenset({
    'live/test_capabilities.py', 'live/test_agent_reaper.py',
    'desk/tests/t_authored_baseline_3110.py',
    'desk/tests/t_source_update_pipeline_220.py',
    'desk/tests/t_source_update_battery_2810.py',
    'desk/tests/t_source_battery_controls_0211.py',
    'desk/tests/t_portable_switch_3010.py',
})
CUTOVER = frozenset({'agent.py', 'bootguard.py', 'command_process.py',
    'python_app.py', 'python_migrate.py', 'python_migration_window.py',
    'localharness/python_source_updater.py', 'localharness/python_archive_update.py',
    'localharness/source_update.py', 'localharness/source_update_host.py',
    'localharness/source_update_coordinator.py', 'localharness/update_composition.py',
    'localharness/source_merge.py', 'localharness/portable_update.py'})
_POLICY = Path(__file__).read_bytes()


def changed(before, after):
    # Inventories differ in optional size/framework annotations. Only program
    # bytes and executable modes determine a source delta.
    def identity(row):
        return (row.get('sha256'), row.get('mode')) if row else None
    return sorted(n for n in set(before) | set(after)
                  if identity(before.get(n)) != identity(after.get(n)))


def module(name):
    p = PurePosixPath(name)
    if p.parts[0] == 'live':
        return '.'.join(p.with_suffix('').parts[1:])
    if name.startswith('desk/localharness/'):
        return '.'.join(p.with_suffix('').parts[1:])
    return '.'.join(p.with_suffix('').parts)


@lru_cache(maxsize=2048)
def imports(text, name):
    package = module(name).split('.')[:-1]
    result = set()
    tree = ast.parse(text, filename=name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[:len(package) - node.level + 1] if node.level else []
            stem = '.'.join([*prefix, *([node.module] if node.module else [])])
            result.add(stem)
            result.update(stem + '.' + a.name for a in node.names)
    return result


def select(lanes, files, read_source, deltas, request=None):
    """Pure selection over frozen identities; exceptions never mean empty scope.

    read_source resolves exact candidate bytes at its canonical composition root.
    Caller supplies separately authenticated B/I/L/M delta lists. Paths here use
    the full recipe's live/ and desk/ coordinates, including removed paths.
    """
    request = request or {}
    if (set(request) - {'full_tests', 'extra_tests', 'reason'}
            or type(request.get('full_tests', False)) is not bool
            or not isinstance(request.get('extra_tests', []), list)
            or not isinstance(request.get('reason', ''), str)):
        raise ValueError('Invalid verification selection request')
    extras = request.get('extra_tests', [])
    if (extras or request.get('full_tests')) and not request.get('reason', '').strip():
        raise ValueError('Verification widening needs a reason')
    tests = {name for lane in lanes for name in lane['sources']
             if lane['kind'] in {'unittest', 'unittest-file', 'pytest-file', 'script', 'helper'}}
    if any(not isinstance(n, str) or n not in tests for n in extras):
        raise ValueError('Widening names a missing or non-test module')
    changed_names = sorted(set(n for names in deltas.values() for n in names))
    reasons = {name: ['contract floor'] for name in FLOOR if name in files}
    full = []
    if not FLOOR <= set(files):
        full.append('published contract floor unavailable; execute full recipe')
    if request.get('full_tests'):
        full.append('author requested full: ' + request['reason'].strip())
    for name in changed_names:
        path = PurePosixPath(name)
        short = name.removeprefix('live/').removeprefix('desk/')
        apparatus = ('verification' in path.name or path.name.startswith('source_update_')
                     or short in CUTOVER or path.name == 'praxis_test.py'
                     or path.suffix in {'.toml', '.lock'} or path.name.startswith('requirements'))
        if apparatus or name in FLOOR or name.startswith('desk/tests/') and name not in tests:
            full.append('verification/cutover/dependency input changed: ' + name)
        elif path.suffix != '.py':
            # No unsourced dependency guess for prompts, UI, native or resources.
            full.append('non-Python impact requires full: ' + name)
        if name in tests:
            reasons.setdefault(name, []).append('changed suite')
    targets = {module(n) for n in changed_names if n.endswith('.py') and n not in tests}
    if not full and targets:
        for name in sorted(tests):
            if name not in files or not name.endswith('.py'):
                continue
            try:
                dependencies = imports(read_source(name), name)
            except (SyntaxError, UnicodeError):
                full.append('test import graph unavailable: ' + name)
                break
            bound = sorted(t for t in targets if any(i == t or i.startswith(t + '.') for i in dependencies))
            if bound:
                reasons.setdefault(name, []).append('direct import: ' + ', '.join(bound))
        # An unrepresented production module has no demonstrated dependency
        # coverage. Require widening/full instead of pretending the floor covers it.
        for target in targets:
            if not any(any(reason.startswith('direct import: ') and target in reason[15:].split(', ')
                           for reason in why) for why in reasons.values()):
                full.append('no direct consumer witness for ' + target)
    for name in extras:
        reasons.setdefault(name, []).append('author widening: ' + request['reason'].strip())
    selected = []
    for lane in lanes:
        if full or lane['kind'] not in {'unittest', 'unittest-file', 'pytest-file', 'script', 'helper'}:
            selected.append(dict(lane))
        elif lane['kind'] == 'unittest':
            sources = [n for n in lane['sources'] if n in reasons]
            if sources:
                # Nested package/load_tests collection retains full core traversal.
                from .source_update_parallel import package_discovery
                if any(n.count('/') != 1 for n in lane['sources']) or package_discovery(files,read_source,lane.get('root','live')):
                    selected.append(dict(lane))
                else:
                    selected.append({**lane, 'sources': sources})
        elif any(n in reasons for n in lane['sources']):
            selected.append(dict(lane))
    record = {'version': 1, 'tier': 'full' if full else 'selected',
              'policy_sha256': hashlib.sha256(_POLICY).hexdigest(),
              'rule': 'floor + changed suites + direct importers + authored widening',
              'deltas': deltas, 'full_reasons': sorted(set(full)),
              'reasons': reasons, 'request': request, 'available_lanes': len(lanes),
              'selected_lanes': selected,
              'omitted_lanes': [l['id'] for l in lanes if l['id'] not in {s['id'] for s in selected}],
              'omitted_core_sources': sorted(set(n for l in lanes if l['kind']=='unittest' for n in l['sources'])
                    - set(n for l in selected if l['kind']=='unittest' for n in l['sources']))}
    return record
