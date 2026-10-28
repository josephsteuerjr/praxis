"""Trusted bundle subprocess adapter: discover and record actual unittest cases.

Executed by incoming host, never imported as candidate code. Exact case IDs,
including import errors, skips and unexpected successes, stay in report.json.
"""
from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import importlib.metadata
import json
import os
from pathlib import Path
import runpy
import sys
import traceback
import unittest
import tempfile


def cases(suite):
    for case in suite:
        if isinstance(case, unittest.TestSuite):
            yield from cases(case)
        else:
            yield case


class Observed(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.started_ids = []
        self.outcomes = []

    def startTest(self, test):
        self.started_ids.append(test.id())
        super().startTest(test)

    def addSuccess(self, test):
        self.outcomes.append({'id': test.id(), 'status': 'passed'})
        super().addSuccess(test)

    def addError(self, test, err):
        self.outcomes.append({'id': test.id(), 'status': 'error'})
        super().addError(test, err)

    def addFailure(self, test, err):
        self.outcomes.append({'id': test.id(), 'status': 'failed'})
        super().addFailure(test, err)

    def addSkip(self, test, reason):
        # unittest does not start a class/module skipped by setUpClass/setUpModule.
        self.outcomes.append({'id': test.id(), 'status': 'skipped', 'reason': reason})
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.outcomes.append({'id': test.id(), 'status': 'expected-failure'})
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.outcomes.append({'id': test.id(), 'status': 'unexpected-success'})
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.outcomes.append({'id': test.id(), 'status': 'failed-subtest', 'subtest': str(subtest)})
        super().addSubTest(test, subtest, err)


def _write_fence(root, report):
    """Python audit floor for accidental writes to native Known Folders.

    Not an OS security sandbox; native subprocess lanes need an isolated native
    executor. A denied write is a real test error, never a permitted skip.
    """
    root = Path(root).resolve()
    report = Path(report).resolve()
    def allowed(path):
        if isinstance(path, int) or path is None:
            return
        p = Path(os.fsdecode(path)).resolve()
        if p not in {Path(os.devnull).resolve(), report} and not p.is_relative_to(root):
            raise PermissionError('Verification write outside isolated work: ' + str(p))
    def audit(event, args):
        if event == 'open':
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)):
                allowed(args[0])
        elif event in {'os.remove', 'os.rmdir', 'os.mkdir', 'os.chmod', 'os.chown', 'os.utime', 'os.truncate'}:
            allowed(args[0])
        elif event in {'os.rename', 'os.link', 'os.symlink'}:
            allowed(args[0]); allowed(args[1])
    sys.addaudithook(audit)


def _pytest(source,value,sandbox=None):
    """Observe pytest's real fixture/collection protocol, not a synthetic case."""
    import pytest
    observed={}
    class Plugin:
        def pytest_collection_finish(self,session):
            value['collected']=[item.nodeid for item in session.items]
        def pytest_runtest_logstart(self,nodeid,location):
            value['executed'].append(nodeid)
        def pytest_runtest_logreport(self,report):
            identity=report.nodeid
            if report.failed:
                observed[identity]={'id':identity,'status':'failed' if report.when=='call' else 'error'}
            elif report.skipped:
                reason=report.longrepr[2] if isinstance(report.longrepr,tuple) else str(report.longrepr)
                observed[identity]={'id':identity,'status':'expected-failure' if hasattr(report,'wasxfail') else 'skipped',
                                    'reason':str(reason).removeprefix('Skipped: ')}
            elif report.when=='call' and identity not in observed:
                observed[identity]={'id':identity,'status':'unexpected-success' if hasattr(report,'wasxfail') else 'passed'}
    os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
    command=[str(Path(source).resolve()),'-q','-p','no:cacheprovider']
    if sandbox:command+=['--basetemp',str(Path(sandbox)/'pytest-temp')]
    code=pytest.main(command,plugins=[Plugin()])
    value.update(format='helene-pytest-observation.v1',outcomes=[observed[name] for name in value['collected'] if name in observed],
        complete=True,successful=int(code)==0,tests_run=len(value['executed']))
    value['coverage_complete']=(Counter(value['collected'])==Counter(value['executed'])
        and Counter(value['collected'])==Counter(row['id'] for row in value['outcomes']))
    return 0 if value['successful'] and value['coverage_complete'] and value['collected'] else 1


def run(root, report, *, source='', pattern='test*.py', lock=None, sandbox=None, dependencies=None,framework='unittest'):
    root = Path(root).resolve()
    value = {'format': 'helene-unittest-observation.v1', 'collected': [], 'executed': [], 'outcomes': [], 'complete': False}
    try:
        if sandbox:
            temporary=Path(sandbox).resolve()/'tmp'
            temporary.mkdir(parents=True,exist_ok=True)
            for key in ('TEMP','TMP','TMPDIR'):os.environ[key]=str(temporary)
            tempfile.tempdir=str(temporary)
            _write_fence(sandbox, report)
        if dependencies:
            sys.path.insert(0, str(Path(dependencies).resolve()))
        if lock:
            expected = json.loads(Path(lock).read_text(encoding='utf8'))
            actual = dict(sorted((d.metadata['Name'].lower().replace('_', '-'), d.version)
                                 for d in importlib.metadata.distributions() if d.metadata['Name']))
            if expected.get('platform') != sys.platform or expected.get('python_version') != sys.version:
                raise ValueError('Executor Python/platform does not match the published lock')
            required = expected.get('python_packages')
            if not isinstance(required, dict) or any(actual.get(name) != version for name, version in required.items()):
                raise ValueError('Executor verification dependency closure does not match the published lock')
        sys.path[:0] = [str(root), str(root / 'localharness')]
        if framework=='pytest':
            if not source:raise ValueError('Pytest lane requires its exact source')
            code=_pytest(source,value,sandbox)
            Path(report).write_text(json.dumps(value,ensure_ascii=False,sort_keys=True),encoding='utf8')
            return code
        if not source:
            # Exactly the product's hermetic bootstrap, then its full discovery.
            runpy.run_path(str(root / 'praxis_test.py'), run_name='verification_bootstrap')
            suite = unittest.defaultTestLoader.discover(str(root), pattern=pattern, top_level_dir=str(root))
        else:
            path = Path(source).resolve()
            sys.path.insert(0, str(path.parent))
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec is None or spec.loader is None:
                raise ValueError('Test module cannot be loaded')
            module = importlib.util.module_from_spec(spec)
            sys.modules[path.stem] = module
            spec.loader.exec_module(module)
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
        value['collected'] = [case.id() for case in cases(suite)]
        result = unittest.TextTestRunner(verbosity=2, resultclass=Observed).run(suite)
        outcomes = []
        not_started = []
        for row in result.outcomes:
            # unittest reports one _ErrorHolder for setUpClass/module skip.
            # Expand only its actually collected cases, preserving exact reason.
            identity = row['id']
            if row['status'] == 'skipped' and identity.startswith(('setUpClass (', 'setUpModule (')) and identity.endswith(')'):
                prefix = identity.split('(', 1)[1][:-1] + '.'
                selected = [n for n in value['collected'] if n.startswith(prefix)]
                for name in selected:
                    outcomes.append({**row, 'id': name, 'started': False})
                    not_started.append(name)
            else:
                outcomes.append(row)
        value.update(executed=result.started_ids, skipped_before_start=not_started,
                     outcomes=outcomes, complete=True, successful=result.wasSuccessful(), tests_run=result.testsRun)
        value['coverage_complete'] = Counter(value['collected']) == Counter(result.started_ids + not_started)
    except BaseException as exc:
        value.update(error=type(exc).__name__ + ': ' + str(exc), traceback=traceback.format_exc())
        traceback.print_exc()
    Path(report).write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf8')
    return 0 if (value.get('complete') and value.get('successful') and value.get('coverage_complete')
                 and value['collected']) else 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--report', required=True)
    p.add_argument('--source', default='')
    p.add_argument('--pattern', default='test*.py')
    p.add_argument('--lock')
    p.add_argument('--sandbox')
    p.add_argument('--dependencies')
    p.add_argument('--framework',choices=('unittest','pytest'),default='unittest')
    a = p.parse_args()
    return run(a.root, a.report, source=a.source, pattern=a.pattern, lock=a.lock, sandbox=a.sandbox, dependencies=a.dependencies,framework=a.framework)


if __name__ == '__main__':
    raise SystemExit(main())
