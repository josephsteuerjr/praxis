"""A/L/B/M review navigation derived only from the complete frozen manifest."""
from collections import defaultdict
import difflib
import hashlib
import json
from pathlib import Path

SIDES = ('base', 'mine', 'release', 'resolved')


def overview(manifest, overlay):
    if not overlay.get('merge_identity'):
        return None
    sides = {side: {} for side in SIDES}
    for row in manifest:
        label, separator, name = row['path'].partition('/')
        if separator and label in sides:
            sides[label][name] = row
    names = set(sides['base'])
    if not names or any(set(rows) != names for rows in sides.values()):
        return None  # existing overlay gap still prevents admission
    groups, changed, withheld = defaultdict(list), [], []
    for name in sorted(names):
        rows = [sides[side][name] for side in SIDES]
        # Existence and executable mode matter as well as the content digest.
        a, l, b, m = [(r.get('exists', True), r.get('sha256'), r.get('mode')) for r in rows]
        if a == l == b == m: group = 'unchanged'
        elif m == b and a == l: group = 'upstream-only'
        elif m == l and a == b: group = 'local-only'
        elif m == l == b: group = 'converged'
        elif m == b: group = 'selected-upstream'
        elif m == l: group = 'retained-local'
        else: group = 'combined'
        groups[group].append(name)
        if l != m: changed.append(name)
        if any(r.get('withheld') for r in rows): withheld.append(name)
    return {'format': 'helene-merge-review.v1', 'groups': dict(groups),
            'counts': {k: len(v) for k, v in groups.items()},
            'changed_from_local': changed, 'withheld': withheld,
            'conflicts': overlay.get('conflicts', []),
            'repairs': overlay.get('repairs', [])}


def index_text(value):
    lines = ['Frozen source update: base=A, mine=L, release=B, resolved=M.\n',
             'This index compares this update\'s four sides, never an unrelated earlier review.\n',
             'All source bytes, absences and modes remain in the bound manifest.\n',
             'Use inspect diff(path, left, right) for one complete comparison; read/search for coupling.\n',
             'Start with changed_from_local and conflicts. Compare base->mine, base->release and mine->resolved.\n',
             'local-only paths are retained preexisting edits/deletions, not deletions introduced by this update.\n',
             'Trace their coupling where updated code depends on them; unchanged inputs are available as context.\n',
             'Any withheld material remains a gap; this index does not approve code.\n\n']
    lines.append('Changed from installed local source:\n' + ''.join('  '+p+'\n' for p in value['changed_from_local']))
    for group, paths in value['groups'].items():
        lines.append('\n'+group+' ('+str(len(paths))+'):\n'+''.join('  '+p+'\n' for p in paths))
    return ''.join(lines)


def coverage_units(value, snapshot):
    """Bound each critic to a tractable assignment; retain the full shared context."""
    names = sorted(set(value['changed_from_local']) | set(value['conflicts']) |
                   {r['path'] for r in value.get('repairs', []) if isinstance(r, dict) and r.get('path')})
    units, pending, weight = [], [], 0
    def append(paths):
        identity = hashlib.sha256(json.dumps(paths, separators=(',', ':')).encode()).hexdigest()
        units.append({'id': identity, 'paths': paths})
    for name in names:
        sides = {}
        for side in SIDES:
            path = Path(snapshot) / side / name
            sides[side] = path.read_text(encoding='utf-8-sig').splitlines(True) if path.is_file() else []
        size = sum(len(''.join(difflib.unified_diff(sides[a], sides[b])))
                   for a, b in [('base', 'mine'), ('base', 'release'), ('mine', 'resolved')])
        if pending and (len(pending) >= 4 or weight + size > 24000):
            append(pending); pending, weight = [], 0
        pending.append(name); weight += size
    if pending: append(pending)
    if not units: append([])  # an unchanged contested intent still needs review
    return units


def validate_coverage(subject, critics):
    units = subject.get('merge_review', {}).get('coverage_units')
    if units is None: return  # historical ordinary review
    expected = {unit['id']: unit['paths'] for unit in units}
    actual = {}
    for critic in critics:
        assignment = critic.get('coverage') or {}
        identity = assignment.get('id')
        if identity not in expected or assignment.get('paths') != expected[identity]:
            raise ValueError('Critic coverage does not match the frozen assignment')
        actual[identity] = assignment['paths']
    if actual != expected:
        raise ValueError('Mandatory review coverage is incomplete')
