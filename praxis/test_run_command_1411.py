"""run_command: one program with exact argv, real child processes, home and Forge routes."""
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import agent
import catalog_families
import command_execution
import forge
import forge_worker
import tool_report

ECHO_ARGV = 'import json,sys; sys.stdout.write(json.dumps(sys.argv[1:]))'
ECHO_STDIN = 'import sys; data=sys.stdin.buffer.read(); sys.stdout.write(data.hex() or "EMPTY")'
TRICKY = ['', ' two  spaces ', "quote ' and \" both", 'a;b', '$(touch PWNED)', '`touch PWNED2`',
          '*', '~', 'back\\slash\\\\', 'line1\nline2', 'кириллица ✓ 𝄞', '--flag=x y', '&&', '|']


def facts(out):
    rows = re.findall(r'^\[run_command (\{.*\})\]$', out, re.M)
    if len(rows) != 1:
        raise AssertionError('expected one facts line:\n' + out)
    return json.loads(rows[0])


class HomeFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(agent, 'BASE', self.root))
        self.stack.enter_context(patch.object(agent, '_autocommit_self_edit'))
        self.stack.enter_context(patch.object(agent, '_pre_shell_dirt', return_value={}))
        self.stack.enter_context(patch.object(agent.selfgit, 'safety_point'))

    def call(self, **kw):
        kw.setdefault('cwd', str(self.root))
        report = tool_report.new(); token = tool_report.attach(report)
        try:
            out = agent._call_tool_with_ceiling('run_command', agent.TOOL_IMPL['run_command'], kw)
            return out, report
        finally:
            tool_report.detach(token)


class Home(HomeFixture, unittest.TestCase):
    def test_every_argument_reaches_the_program_exactly_and_is_never_shell(self):
        argv = [sys.executable, '-c', ECHO_ARGV, *TRICKY]
        out, report = self.call(argv=argv)
        self.assertEqual(json.loads(out.split('\n[run_command ', 1)[0]), TRICKY)
        self.assertFalse((self.root / 'PWNED').exists()); self.assertFalse((self.root / 'PWNED2').exists())
        f = facts(out)
        self.assertEqual(f['argv'], argv); self.assertEqual(f['executor'], 'home')
        self.assertEqual(f['exit_code'], 0); self.assertTrue(f['started'])
        self.assertEqual(f['stdin'], 'closed')
        self.assertTrue(out.endswith('[exit 0]'), out)
        self.assertEqual(report['outcome'], 'ok')

    def test_bare_program_name_is_resolved_and_reported(self):
        out, _ = self.call(argv=['env'])
        self.assertTrue(os.path.isabs(facts(out)['program']))
        self.assertTrue(out.endswith('[exit 0]'), out)

    def test_stdin_is_exact_text_then_eof_and_absent_means_closed(self):
        text = 'line 1\r\nкириллица\n\0? no: ' + '  end'
        out, _ = self.call(argv=[sys.executable, '-c', ECHO_STDIN], stdin_text=text)
        self.assertEqual(bytes.fromhex(out.split('\n', 1)[0]), text.encode('utf-8'))
        self.assertIn(f"text {len(text.encode('utf-8'))} bytes then EOF", facts(out)['stdin'])
        empty, _ = self.call(argv=[sys.executable, '-c', ECHO_STDIN], stdin_text='')
        self.assertTrue(empty.startswith('EMPTY'), empty)
        closed, _ = self.call(argv=[sys.executable, '-c', ECHO_STDIN])
        self.assertTrue(closed.startswith('EMPTY'), closed)
        self.assertEqual(facts(closed)['stdin'], 'closed')

    def test_bad_argv_is_refused_before_any_launch(self):
        with patch('hands.execute_argv') as launcher:
            for bad in ([], 'git status', [1], ['ok', None], ['a\0b'], ['', 'x'], ['  ']):
                with self.subTest(argv=bad):
                    out, report = self.call(argv=bad)
                    self.assertTrue(out.startswith('refused: run_command:'), out)
                    self.assertEqual(report['outcome'], 'refused')
            out, report = self.call(argv=['true'], stdin_text=b'bytes')
            self.assertEqual(report['outcome'], 'refused')
        launcher.assert_not_called()

    def test_missing_program_and_missing_cwd_are_refused_before_spawn(self):
        with patch('hands.execute_argv') as launcher, patch.object(agent.selfgit, 'safety_point') as point:
            out, report = self.call(argv=['definitely-not-a-program-1411', 'x'])
            self.assertIn('program not found', out); self.assertEqual(report['outcome'], 'refused')
            self.assertFalse(facts(out)['started'])
            out, report = self.call(argv=['./missing-script'])
            self.assertIn('program not found', out)
            out, report = self.call(argv=['true'], cwd=str(self.root / 'nowhere'))
            self.assertIn('no such directory', out); self.assertEqual(report['outcome'], 'refused')
        launcher.assert_not_called(); point.assert_not_called()

    def test_nonzero_exit_and_signal_are_observed_not_success(self):
        out, report = self.call(argv=[sys.executable, '-c', 'import sys; print("partial"); sys.exit(3)'])
        self.assertEqual(facts(out)['exit_code'], 3); self.assertTrue(out.endswith('[exit 3]'))
        self.assertEqual(report['outcome'], 'failed')
        if os.name != 'nt':
            out, _ = self.call(argv=[sys.executable, '-c', 'import os,signal; os.kill(os.getpid(), signal.SIGTERM)'])
            self.assertEqual(facts(out)['signal'], 15)

    def test_large_and_binary_output_is_retained(self):
        out, _ = self.call(argv=[sys.executable, '-c',
                                 'import sys; sys.stdout.write("x"*300000); sys.stdout.buffer.write(b"\\xff\\x00tail")'])
        self.assertTrue(out.startswith('x' * 300000))
        self.assertIn('�\x00tail', out)

    def test_stderr_is_combined_and_named(self):
        out, _ = self.call(argv=[sys.executable, '-c', 'import sys; print("O"); sys.stdout.flush(); print("E", file=sys.stderr)'])
        self.assertIn('O\nE', out)
        self.assertIn('combined', facts(out)['output'])

    def test_timeout_reports_no_exit_and_stops_descendants(self):
        marker = self.root / 'late'
        child = 'import time,pathlib;time.sleep(3);pathlib.Path(%r).write_text("late")' % str(marker)
        parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",%r]);time.sleep(30)' % child
        started = time.monotonic()
        out, report = self.call(argv=[sys.executable, '-c', parent], timeout=1)
        self.assertLess(time.monotonic() - started, 10)
        f = facts(out)
        self.assertTrue(f['timed_out']); self.assertNotIn('exit_code', f); self.assertIn('tree_stop', f)
        self.assertTrue(out.endswith('[exit unobserved]'), out)
        # Without a custodian the group exit is not proven: that is in_doubt, never success.
        expected = 'failed' if f['tree_stop']['confirmed'] else 'in_doubt'
        self.assertEqual(report['outcome'], expected)
        time.sleep(3.2)
        self.assertFalse(marker.exists())

    def test_background_descendant_survives_a_normal_foreground_exit(self):
        marker = self.root / 'background'
        child = 'import time,pathlib;time.sleep(1.5);pathlib.Path(%r).write_text("ok")' % str(marker)
        started = time.monotonic()
        out, _ = self.call(argv=[sys.executable, '-c',
                                 'import subprocess,sys;subprocess.Popen([sys.executable,"-c",%r])' % child], timeout=10)
        self.assertLess(time.monotonic() - started, 1.4)
        self.assertTrue(out.endswith('[exit 0]'), out)
        deadline = time.monotonic() + 4
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        self.assertEqual(marker.read_text(), 'ok')

    def test_hardbot_rail_sees_the_displayed_argv(self):
        with patch.object(agent.stewardship, 'check', return_value='rail said no') as rail, \
             patch('hands.execute_argv') as launcher:
            out, _ = self.call(argv=['rm', '-rf', '/opt/hardbot2/data'])
        self.assertEqual(out, 'rail said no')
        self.assertIn('/opt/hardbot2/data', rail.call_args.kwargs['command'])
        launcher.assert_not_called()


class ForgeRoute(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'a.txt').write_text('one\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@local',
                        'commit', '-qm', 'fixture'], check=True)
        self.task = re.search(r'code-[0-9a-f]+', forge.start('run_command fixture', target=str(self.root),
                                                            isolation='direct')).group()

    def records(self):
        return sorted((forge._task_dir(self.task) / 'processes').glob('*/request.json'))

    def test_parent_and_worker_run_the_same_exact_argv_in_task_custody(self):
        argv = [sys.executable, '-c', ECHO_ARGV, *TRICKY]
        report = tool_report.new(); token = tool_report.attach(report)
        try:
            parent = agent.tool_run_command(argv, task_id=self.task, timeout=30)
        finally:
            tool_report.detach(token)
        worker = forge_worker._dispatch({'task_id': self.task, 'role': 'worker'}, 'run_command',
                                        {'argv': argv, 'timeout': 30})
        for out in (parent, worker):
            self.assertEqual(json.loads(out.split('\n[run_command ', 1)[0]), TRICKY)
            f = facts(out)
            self.assertEqual(f['argv'], argv); self.assertEqual(f['executor'], 'forge task ' + self.task)
            self.assertEqual(f['exit_code'], 0)
            self.assertIn('[command: ok;', out); self.assertIn('log ', out)
        self.assertEqual(report['outcome'], 'ok')
        self.assertEqual(report['executions'][-1]['argv'], argv)
        self.assertTrue(os.path.isabs(report['executions'][-1]['program']))
        rows = [json.loads(p.read_text()) for p in self.records()]
        self.assertEqual(len(rows), 2)
        for row, path in zip(rows, self.records()):
            self.assertEqual(row['argv'], argv)
            self.assertEqual(row['command_custody'], 'foreground-group-v1')
            self.assertEqual(json.loads(path.with_name('result.json').read_text())['status'], 'done')
        self.assertFalse((self.root / 'PWNED').exists())

    def test_task_stdin_cwd_and_missing_program(self):
        (self.root / 'sub').mkdir()
        out = forge.run_command(self.task, [sys.executable, '-c', 'import os,sys; print(os.getcwd()); print(sys.stdin.read())'],
                                cwd='sub', stdin_text='данные\n')
        self.assertIn(str((self.root / 'sub').resolve()), out); self.assertIn('данные', out)
        before = self.records()
        report = tool_report.new(); token = tool_report.attach(report)
        try:
            out = forge.run_command(self.task, ['no-such-program-1411'])
        finally:
            tool_report.detach(token)
        self.assertIn('refused before launch', out); self.assertEqual(report['outcome'], 'refused')
        out = forge.run_command(self.task, ['true'], cwd='../outside')
        self.assertIn('refused before launch', out)
        self.assertEqual(self.records(), before)

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux command custody')
    def test_task_timeout_is_unobserved_exit_with_tree_stop_receipt(self):
        out = forge.run_command(self.task, [sys.executable, '-c', 'import time; time.sleep(30)'], timeout=1)
        f = facts(out)
        self.assertTrue(f['timed_out']); self.assertNotIn('exit_code', f)
        self.assertTrue(f['tree_stop']['confirmed'], f)
        self.assertTrue(out.endswith('[exit unobserved]'), out)

    def test_remote_task_is_unsupported_and_nothing_is_sent(self):
        class Remote:
            def __getattr__(self, name):
                raise AssertionError('remote backend touched: ' + name)
        report = tool_report.new(); token = tool_report.attach(report)
        try:
            with patch.object(forge, '_task_root', return_value=({'scope': 'host'}, Path('/host'), '')), \
                 patch.object(forge, '_remote_client', return_value=Remote()):
                out = agent.tool_run_command(['git', 'status'], task_id='hcode-fixture')
        finally:
            tool_report.detach(token)
        self.assertIn('unsupported for this host task', out)
        self.assertEqual(report['outcome'], 'refused')

    def test_read_only_roles_cannot_launch(self):
        out = forge_worker._dispatch({'task_id': self.task, 'role': 'scout'}, 'run_command', {'argv': ['true']})
        self.assertIn('refused (read-oriented role)', out)
        self.assertEqual(self.records(), [])


class ProcessArgv(ForgeRoute):
    """Long-running start speaks the same exact-argv language through one process owner."""

    def poll_done(self, proc_id):
        polled = ''
        for _ in range(200):
            polled = forge.process(self.task, 'poll', process_id=proc_id)
            if '"status": "done"' in polled or '"status": "failed"' in polled:
                break
            time.sleep(.05)
        return polled

    def test_start_delivers_exact_argv_through_the_supervisor(self):
        argv = [sys.executable, '-c', ECHO_ARGV, *TRICKY]
        for start in (lambda: forge.process(self.task, 'start', argv=argv, name='argv'),
                      lambda: forge_worker._dispatch({'task_id': self.task, 'role': 'worker'}, 'process',
                                                     {'action': 'start', 'argv': argv})):
            out = start()
            proc_id = re.search(r'(proc-[0-9a-f]+)', out).group(1)
            polled = self.poll_done(proc_id)
            self.assertIn('"status": "done"', polled)
            self.assertEqual(json.loads(polled.split('--- log ---\n', 1)[1].strip()), TRICKY)
            row = json.loads((forge._task_dir(self.task) / 'processes' / proc_id / 'request.json').read_text())
            self.assertEqual(row['argv'], argv); self.assertTrue(os.path.isabs(row['program']))
        self.assertFalse((self.root / 'PWNED').exists())

    def test_conflicts_and_unusable_argv_are_refused_before_start(self):
        cases = [dict(action='start', argv=['true'], command='echo x'),
                 dict(action='start', argv=[]), dict(action='start', argv=['a\0b']),
                 dict(action='start', argv=['no-such-program-1411']),
                 dict(action='start', argv=['true'], cwd='../outside'),
                 dict(action='poll', argv=['true'], process_id='proc-x')]
        for args in cases:
            with self.subTest(args=args):
                report = tool_report.new(); token = tool_report.attach(report)
                try:
                    out = forge.process(self.task, **args)
                finally:
                    tool_report.detach(token)
                self.assertTrue('refused' in out.lower() or 'вне' in out or 'outside' in out, out)
        self.assertEqual(list((forge._task_dir(self.task) / 'processes').glob('*')), [])

    def test_remote_task_process_argv_is_unsupported(self):
        class Remote:
            def __getattr__(self, name):
                raise AssertionError('remote backend touched: ' + name)
        with patch.object(forge, '_control_root', return_value=({'scope': 'host'}, Path('/host'), '')), \
             patch.object(forge, '_remote_client', return_value=Remote()):
            out = forge._process_unlocked('hcode-fixture', 'start', argv=['git', 'status'])
        self.assertIn('unsupported for this host task', out)

    def test_shell_command_start_is_unchanged(self):
        out = forge.process(self.task, 'start', command=f'{sys.executable} -c "print(1+1)" | cat')
        polled = self.poll_done(re.search(r'(proc-[0-9a-f]+)', out).group(1))
        self.assertIn('"status": "done"', polled); self.assertIn('2', polled.split('--- log ---', 1)[1])

    test_parent_and_worker_run_the_same_exact_argv_in_task_custody = None
    test_task_stdin_cwd_and_missing_program = None
    test_task_timeout_is_unobserved_exit_with_tree_stop_receipt = None
    test_remote_task_is_unsupported_and_nothing_is_sent = None
    test_read_only_roles_cannot_launch = None


class Declared(HomeFixture, unittest.TestCase):
    """env / outputs / scratch: explicit, reported, never a new authority."""

    def git_root(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root / 'tracked.txt').write_text('t\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@l',
                        'commit', '-qm', 'x'], check=True)

    def test_env_reaches_the_child_and_only_names_are_reported(self):
        secret = 'знач ение $(x) "q"'
        out, report = self.call(argv=[sys.executable, '-c', 'import os; print(os.environ["MY_VAR"])'],
                                env=[{'name': 'MY_VAR', 'value': secret}])
        self.assertEqual(out.split('\n', 1)[0], secret)
        f = facts(out)
        self.assertEqual(f['env_set'], ['MY_VAR'])
        self.assertNotIn(secret, out.split('\n', 1)[1])
        self.assertEqual(report['outcome'], 'ok')

    def test_unusable_env_is_refused_before_launch(self):
        with patch('hands.execute_argv') as launcher:
            for bad in ({'A': 'b'}, [{'name': 'PRAXIS_BASE', 'value': '/x'}], [{'name': '1A', 'value': 'x'}],
                        [{'name': 'A', 'value': 1}], [{'name': 'A'}], [{'name': 'A', 'value': 'x'}] * 2,
                        [{'name': 'A', 'value': 'a\0b'}]):
                with self.subTest(env=bad):
                    out, report = self.call(argv=['true'], env=bad)
                    self.assertTrue(out.startswith('refused: run_command:'), out)
                    self.assertEqual(report['outcome'], 'refused')
        launcher.assert_not_called()

    def test_outputs_are_reported_with_change_and_hash(self):
        (self.root / 'existing.txt').write_text('old')
        (self.root / 'same.txt').write_text('same')
        code = ('import pathlib; pathlib.Path("new.txt").write_text("fresh"); '
                'pathlib.Path("existing.txt").write_text("changed")')
        out, _ = self.call(argv=[sys.executable, '-c', code],
                           outputs=['new.txt', 'existing.txt', 'same.txt', 'never.txt'])
        rows = {Path(r['path']).name: r for r in facts(out)['outputs']}
        self.assertEqual({k: v['change'] for k, v in rows.items()},
                         {'new.txt': 'created', 'existing.txt': 'modified', 'same.txt': 'unchanged',
                          'never.txt': 'missing'})
        import hashlib
        self.assertIn(hashlib.sha256(b'fresh').hexdigest(), rows['new.txt']['state'])

    def test_scratch_is_removed_after_the_run_and_left_out_of_changes(self):
        self.git_root()
        (self.root / 'probe_check.py').write_text('print("PROBE")\n')
        out, report = self.call(argv=[sys.executable, 'probe_check.py'], scratch=['probe_check.py'])
        self.assertTrue(out.startswith('PROBE'), out)
        self.assertFalse((self.root / 'probe_check.py').exists())
        self.assertEqual(facts(out)['scratch'], [{'path': str(self.root / 'probe_check.py'), 'removed': True}])
        changes = re.search(r'^\[changes: (.*)\]$', out, re.M).group(1)
        self.assertNotIn('probe_check.py', changes)
        self.assertEqual(report['outcome'], 'ok')

    def test_scratch_that_is_tracked_or_outside_cwd_is_refused_before_launch(self):
        self.git_root()
        (self.root / 'sub').mkdir()
        with patch('hands.execute_argv') as launcher:
            for scratch, cwd in ((['tracked.txt'], self.root), (['../tracked.txt'], self.root / 'sub'),
                                 (['.'], self.root)):
                with self.subTest(scratch=scratch):
                    out, report = self.call(argv=['true'], scratch=scratch, cwd=str(cwd))
                    self.assertIn('refused before launch', out)
                    self.assertEqual(report['outcome'], 'refused')
        launcher.assert_not_called()
        self.assertTrue((self.root / 'tracked.txt').exists())

    def test_scratch_is_kept_when_the_stop_is_not_proven(self):
        (self.root / 'keep.txt').write_text('k')
        out, report = self.call(argv=[sys.executable, '-c', 'import time; time.sleep(30)'],
                                scratch=['keep.txt'], timeout=1)
        row = facts(out)['scratch'][0]
        if facts(out)['tree_stop']['confirmed']:
            self.assertTrue(row['removed'])
        else:
            self.assertFalse(row['removed']); self.assertIn('not proven', row['note'])
            self.assertTrue((self.root / 'keep.txt').exists())


class DeclaredForge(ForgeRoute):
    def test_task_env_outputs_scratch_and_runtime_pythonpath(self):
        (self.root / 'tmp_probe.txt').write_text('scratch')
        code = ('import os, pathlib, praxis_hands; pathlib.Path("made.txt").write_text(os.environ["TOKEN_NAME"]); '
                'print(os.environ["PYTHONPATH"])')
        out = forge.run_command(self.task, [sys.executable, '-c', code],
                                env=[{'name': 'TOKEN_NAME', 'value': 'VALUE-1411-xyz'}, {'name': 'PYTHONPATH', 'value': '/extra'}],
                                outputs=['made.txt'], scratch=['tmp_probe.txt'], timeout=30)
        f = facts(out)
        self.assertEqual(f['exit_code'], 0, out)
        self.assertEqual((self.root / 'made.txt').read_text(), 'VALUE-1411-xyz')
        self.assertTrue(out.split('\n', 1)[0].endswith(os.pathsep + '/extra'), out)
        self.assertEqual(f['outputs'][0]['change'], 'created')
        self.assertFalse((self.root / 'tmp_probe.txt').exists())
        row = json.loads(self.records()[-1].read_text())
        self.assertEqual(row['env_names'], ['PYTHONPATH', 'TOKEN_NAME'])
        self.assertNotIn('VALUE-1411-xyz', json.dumps(row))

    def test_task_output_outside_root_is_refused(self):
        out = forge.run_command(self.task, ['true'], outputs=['../escape.txt'])
        self.assertIn('outside the task root', out)
        self.assertEqual(self.records(), [])

    test_parent_and_worker_run_the_same_exact_argv_in_task_custody = None
    test_task_stdin_cwd_and_missing_program = None
    test_task_timeout_is_unobserved_exit_with_tree_stop_receipt = None
    test_remote_task_is_unsupported_and_nothing_is_sent = None
    test_read_only_roles_cannot_launch = None


class StrictSchema(unittest.TestCase):
    def test_env_pairs_survive_openai_strict_translation(self):
        import llm
        strict = llm._openai_strict_schema(catalog_families.RUN_COMMAND_TOOL['input_schema'])
        env = strict['properties']['env']['anyOf'][0]
        self.assertEqual(env['items']['required'], ['name', 'value'])
        self.assertEqual(set(env['items']['properties']), {'name', 'value'})
        self.assertIs(env['items']['additionalProperties'], False)


@unittest.skipUnless(sys.platform.startswith('linux'), 'Linux custody integration')
class Reset(unittest.TestCase):
    """The program belongs to the current durable run and dies with its reset."""

    def setUp(self):
        import step_control
        from run_context import RunContext
        from run_manager import RunManager
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        env = {k: v for k, v in os.environ.items() if k != 'PRAXIS_TEST'}
        env['PRAXIS_BASE'] = str(self.base)
        self.stack.enter_context(patch.dict(os.environ, env, clear=True))
        for name, value in (('BASE', self.base), ('_autocommit_self_edit', None), ('_pre_shell_dirt', None)):
            if value is None:
                self.stack.enter_context(patch.object(agent, name, return_value={}))
            else:
                self.stack.enter_context(patch.object(agent, name, value))
        self.stack.enter_context(patch.object(agent.selfgit, 'safety_point'))
        self.manager = RunManager(self.base)
        self.registry = step_control.start(self.base)
        self.addCleanup(self.registry.close)
        self.ctx = RunContext.create(kind='chat_turn', goal='fixture', principal_id='owner', scope='owner')
        self.manager.create(self.ctx, 'fixture'); self.manager.transition(self.ctx.run_id, 'running')

    def cancel(self):
        import step_control
        directory = self.base / 'memory/.control/run-cancel'
        directory.mkdir(parents=True, exist_ok=True)
        step_control._write(directory / (self.ctx.run_id + '.json'),
                            {'id': 'fixture', 'run_id': self.ctx.run_id, 'action': 'cancel'})

    def run_tool(self, argv, result):
        import run_context
        with run_context.bind_run(self.ctx):
            result['out'] = agent.tool_run_command(argv, cwd=str(self.base), timeout=60)

    def test_reset_kills_the_selected_tree_and_keeps_a_sibling(self):
        pid_file = self.base / 'child.pid'
        script = ("import os,time,pathlib; pid=os.fork(); "
                  "(os.setsid(),pathlib.Path(%r).write_text(str(os.getpid()))) if pid==0 else None; time.sleep(90)"
                  % str(pid_file))
        sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(90)'])
        self.addCleanup(lambda: (sibling.kill(), sibling.wait()))
        result = {}
        worker = threading.Thread(target=self.run_tool, args=([sys.executable, '-c', script], result))
        worker.start()
        deadline = time.monotonic() + 10
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(pid_file.exists())
        self.cancel()
        worker.join(10)
        self.assertFalse(worker.is_alive())
        self.assertNotIn('[exit 0]', result['out'])
        self.assertFalse(Path('/proc/' + pid_file.read_text()).exists())
        self.assertIsNone(sibling.poll())

    def test_reset_before_spawn_never_runs_the_program_or_claims_success(self):
        marker = self.base / 'ran'
        self.cancel()
        result = {}
        self.run_tool([sys.executable, '-c', 'import pathlib; pathlib.Path(%r).write_text("x")' % str(marker)], result)
        time.sleep(.3)
        self.assertFalse(marker.exists())
        self.assertNotIn('[exit 0]', result['out'])


class Schemas(unittest.TestCase):
    def ctx(self, owner=True):
        return agent.ChannelContext(chat_id='42', room_id='42', is_dm=True, owner=owner, known=True)

    def test_offered_everywhere_with_one_name_and_no_aliases(self):
        for owner in (True, False):
            offered = {t.get('name'): t for t in agent.offered_tools_for(self.ctx(owner))}
            catalog = {t.get('name'): t for t in agent.catalog_tools_for(self.ctx(owner))}
            self.assertIn('run_command', catalog)
            if not agent.tool_pointers_enabled():
                self.assertEqual(offered['run_command'], catalog['run_command'])
        for alias in ('exec', 'cmd', 'run_argv', 'run_program'):
            self.assertNotIn(alias, agent.TOOL_IMPL)
        self.assertIs(agent.TOOL_IMPL['run_command'], agent.tool_run_command)
        self.assertIn('run_command', [n for _, group in agent.HAND_GROUPS for n in group])
        self.assertIn('run_command', agent.HAND_PURPOSE)

    def test_schema_shape_and_english_parameters(self):
        for tool in (catalog_families.RUN_COMMAND_TOOL, forge_worker.RUN_COMMAND_TOOL):
            props = tool['input_schema']['properties']
            self.assertEqual(tool['input_schema']['required'], ['argv'])
            self.assertEqual(props['argv'], {**props['argv'], 'type': 'array', 'items': {'type': 'string'}})
            for name, param in props.items():
                self.assertTrue(param.get('description', '').strip(), name)
            text = json.dumps(tool, ensure_ascii=False)
            self.assertFalse(any('Ѐ' <= c <= 'ӿ' for c in text))
            self.assertIn('shell', tool['description'])
        self.assertNotIn('task_id', forge_worker.RUN_COMMAND_TOOL['input_schema']['properties'])

    def test_worker_gets_it_only_with_the_mutating_role(self):
        import inspect
        source = inspect.getsource(forge_worker._run_request)
        self.assertIn('RUN_TOOL, RUN_COMMAND_TOOL, PROCESS_TOOL', source)

    def test_shell_and_coding_run_remain_strings_not_argv_aliases(self):
        self.assertEqual(agent.SHELL_TOOL['input_schema']['properties']['command']['type'], 'string')
        self.assertNotIn('argv', agent.SHELL_TOOL['input_schema']['properties'])
        self.assertEqual(forge_worker.RUN_TOOL['input_schema']['properties']['command']['type'], 'string')

    def test_contract_names_the_choice_and_stays_compact(self):
        text = agent._hands_contract_text()
        self.assertIn('run_command(argv=[...])', text)
        self.assertLess(len(text), 4000)

    def test_live_hand_bridge_reaches_programs_it_launches(self):
        source = Path(agent.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count("{'python', 'run_command', 'shell', 'coding_run'}"), 3)

    def test_h_describe_and_hand_see_the_same_schema(self):
        catalog = agent.catalog_tools_for(self.ctx())
        self.assertEqual(agent._hand_signature(next(t for t in catalog if t.get('name') == 'run_command')),
                         'run_command(argv, cwd?, timeout?, task_id?, stdin_text?, env?, …)')


class Validation(unittest.TestCase):
    def test_validate_argv_keeps_items_and_refuses_unusable_values(self):
        self.assertEqual(command_execution.validate_argv(('a', '', 'b c')), ['a', '', 'b c'])
        for bad in (None, '', 'a b', [], [b'x'], ['x', 1], ['\0'], ['']):
            with self.assertRaises(command_execution.ArgvRefusal):
                command_execution.validate_argv(bad)

    def test_resolve_program_uses_path_or_cwd_relative_file(self):
        with tempfile.TemporaryDirectory() as d:
            tool = Path(d) / 'tool.sh'; tool.write_text('#!/bin/sh\n'); tool.chmod(0o755)
            self.assertEqual(command_execution.resolve_program('./tool.sh', d), str(Path(d) / './tool.sh'))
            self.assertIsNone(command_execution.resolve_program('./absent', d))
            self.assertEqual(command_execution.resolve_program('tool.sh', '/', {'PATH': d}), str(tool))


if __name__ == '__main__':
    unittest.main()
