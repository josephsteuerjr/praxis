"""Language axes cannot change durable process requests or verification evidence."""
import json
import shlex
import sys
import unittest
from unittest.mock import patch

import forge
import frame_language as fl
import tool_report
from test_forge_run_language_1611 import ForgeRunLanguage, RAW


class ForgeProcessLanguage(unittest.TestCase):
    setUp = ForgeRunLanguage.setUp
    git_run = ForgeRunLanguage.git_run
    settings = ForgeRunLanguage.settings

    def requests(self, kind):
        return set((forge._task_dir(self.task)/kind).glob('*/request.json'))

    def test_process_supervisor_caption_preserves_exact_argv_and_durable_identity16axes(self):
        argv = [sys.executable, '-c', 'print(' + repr(RAW) + ')']
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                before = self.requests('processes')
                with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                     patch.object(forge, '_spawn_runner', return_value=777777) as spawn, \
                     patch.object(forge, '_proc_started_at', return_value='born'):
                    answer = forge.process(self.task, 'start', argv=argv, name=RAW, timeout=27)
                    after = self.requests('processes')
                    self.assertEqual(len(after-before), 1)
                    request_path = (after-before).pop()
                    request = forge._read_json(request_path)
                    self.assertEqual(request['argv'], argv)
                    self.assertEqual(request['program'], sys.executable)
                    self.assertEqual(request['command'], shlex.join(argv))
                    self.assertEqual(request['name'], RAW)
                    self.assertEqual(request['cwd'], str(self.root))
                    self.assertEqual(request['command_custody'], 'group-v1')
                    self.assertEqual(request['supervisor_pid'], 777777)
                    self.assertEqual(request['supervisor_started_at'], 'born')
                    self.assertEqual(request['parent_unit_id'], '')
                    self.assertEqual(request['timeout'], 27)
                    self.assertFalse((request_path.parent/'result.json').exists())
                    self.assertEqual(answer, fl.text('forge.execution.71',
                        v1=request['id'], v3='777777', v5=str(self.root)))
                    spawn.assert_called_once_with(forge.REPO/'forge_process.py', request_path,
                                                  request_path.parent/'supervisor.log')
                # Settle this simulated runner before the next admission; no child was launched.
                forge._atomic_json(request_path.parent/'result.json', {'status':'done', 'code':0})

    def test_argv_preflight_refuses_without_creating_requests_or_starting_children16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                for action, command, key in [('poll', '', '111'), ('start', RAW, '112')]:
                    with self.subTest(ui=ui, frame=language, action=action), self.settings(ui, language), \
                         patch.object(forge, '_spawn_runner') as spawn:
                        before = self.requests('processes')
                        slot = tool_report.new(); token = tool_report.attach(slot)
                        try:
                            answer = forge.process(self.task, action, argv=[sys.executable], command=command)
                        finally:
                            tool_report.detach(token)
                        self.assertEqual(slot['outcome'], 'refused')
                        self.assertEqual(answer, fl.text('forge.execution.113',
                            v1=fl.text('forge.execution.'+key)))
                        self.assertEqual(self.requests('processes'), before)
                        spawn.assert_not_called()

    def test_real_clean_tree_dedup_keeps_skip_proof_and_localizes_only_the_explanation16axes(self):
        tree = forge._task_tree_sha(self.root)
        self.assertRegex(tree, r'^[0-9a-f]{40}$')
        command = 'literal-command ' + RAW
        old = forge._task_dir(self.task)/'verifications'/'verify-aaaaaaaa'
        forge._atomic_json(old/'request.json', {'id':old.name, 'task_id':self.task, 'tree_sha':tree})
        forge._atomic_json(old/'result.json', {'status':'passed', 'checks':[
            {'id':'old-pass', 'command':command, 'status':'passed'}]})
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                before = self.requests('verifications')
                with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                     patch.object(forge, '_spawn_runner', return_value=777777) as spawn, \
                     patch.object(forge, '_proc_started_at', return_value='born'), \
                     patch.object(forge, '_event', wraps=forge._event) as event:
                    answer = forge.verify(self.task, 'start', commands=command+'\nsecond-command', max_parallel=2)
                    paths = self.requests('verifications')-before
                    self.assertEqual(len(paths), 1)
                    request_path = paths.pop(); request = forge._read_json(request_path)
                    self.assertEqual(request['tree_sha'], tree)
                    first, second = request['checks']
                    self.assertEqual(first['command'], command)
                    self.assertTrue(first['skip'])
                    self.assertEqual(first['skip_status'], 'skipped_already_green')
                    self.assertEqual(first['skip_reference'], old.name+'/old-pass')
                    self.assertNotIn('skip', second)
                    self.assertEqual(request['max_parallel'], 2)
                    self.assertFalse((request_path.parent/'result.json').exists())
                    self.assertEqual(answer, fl.text('forge.execution.97',
                        v1=request['id'], v3='2', v5='2') + fl.text('forge.execution.98',
                        v1='1', v3=tree[:10]) + fl.text('forge.execution.99'))
                    dedup = [call for call in event.call_args_list if call.args[1]=='verification_dedup']
                    self.assertEqual(len(dedup), 1)
                    self.assertEqual(dedup[0].kwargs['summary'], fl.text('forge.execution.118',
                        v0='custom-1', v2=old.name+'/old-pass', v4=tree[:10]))
                    spawn.assert_called_once_with(forge.REPO/'forge_verify.py', request_path,
                                                  request_path.parent/'supervisor.log')
                forge._atomic_json(request_path.parent/'result.json', {'status':'passed_with_skips'})
        # Same HEAD is insufficient once authored input changes.
        self.path.write_text(RAW, encoding='utf8')
        self.assertEqual(forge._task_tree_sha(self.root), '')
        checks = [{'id':'same-command', 'command':command}]
        output, notes = forge._dedup_against_green(self.task, forge._task_tree_sha(self.root), checks)
        self.assertEqual(output, checks); self.assertFalse(notes)
        self.assertNotIn('skip', output[0])

    def test_verification_stop_keeps_natural_result_and_reports_unconfirmed_stop_without_signals(self):
        for index, language in enumerate(fl.LANGUAGES):
            directory = forge._task_dir(self.task)/'verifications'/('verify-'+f'{index+100:08x}')
            forge._atomic_json(directory/'request.json', {'id':directory.name, 'task_id':self.task,
                'supervisor_pid':777777, 'supervisor_started_at':'born', 'status':'running'})
            with self.settings('zh', language), patch.object(forge, '_pid_alive', return_value=False), \
                 patch.object(forge, '_kill_tree', side_effect=AssertionError('no PID authority')), \
                 patch.object(forge.time, 'monotonic', side_effect=[0, 9]):
                answer = forge.verify(self.task, 'stop', verification_id=directory.name)
                self.assertEqual(answer, fl.text('forge.execution.108', v0=directory.name,
                    v2=fl.text('forge.execution.107')))
            self.assertFalse((directory/'result.json').exists())
            self.assertTrue((directory/'stop-request.json').is_file())
            self.assertEqual(forge._unit_state(directory)['status'], 'unknown')
            result = (json.dumps({'status':'passed', 'checks':[], 'raw':RAW})+'\n').encode('utf8')
            (directory/'result.json').write_bytes(result)
            before = (directory/'stop-request.json').read_bytes()
            with self.settings('en', language), patch.object(forge, '_kill_tree') as kill:
                answer = forge.verify(self.task, 'stop', verification_id=directory.name)
                self.assertEqual(answer, fl.text('forge.execution.102', v0=directory.name))
                kill.assert_not_called()
            self.assertEqual((directory/'result.json').read_bytes(), result)
            self.assertEqual((directory/'stop-request.json').read_bytes(), before)

    def test_language_is_pinned_while_a_supervisor_is_registered(self):
        settings = {'lang':'zh', 'agent':{'frame_language':'en'}}
        def spawn(*args):
            settings['agent']['frame_language'] = 'fr'
            return 777777
        before = self.requests('processes')
        with patch.object(fl, 'settings', side_effect=lambda:settings), \
             patch.object(forge, '_spawn_runner', side_effect=spawn), \
             patch.object(forge, '_proc_started_at', return_value='born'):
            answer = forge.process(self.task, 'start', command=RAW)
            request = forge._read_json((self.requests('processes')-before).pop())
            with fl.use('en'):
                self.assertEqual(answer, fl.text('forge.execution.71',
                    v1=request['id'], v3='777777', v5=str(self.root)))
            self.assertEqual(forge.process(self.task, 'list'),
                             request['id']+' [lost] '+RAW)
