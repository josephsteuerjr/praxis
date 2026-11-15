"""Search uncertainty and execution evidence survive independent UI/frame languages."""
import subprocess
import unittest
from unittest.mock import Mock, patch

import frame_language as fl
import workshop
from test_hands import Home

RAW = 'literal Русский 中文 {v1} $&'


class WorkshopExecutionLanguage(Home, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.project = workshop.PROJECTS / 'proj'
        self.project.mkdir()

    def axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                yield ui, language

    def settings(self, ui, language):
        return patch.object(fl, 'settings', return_value={'lang': ui, 'agent': {'frame_language': language}})

    def test_actual_literal_and_regex_search_keep_source_hits_and_file_bytes16axes(self):
        path = self.tmp / 'workspace/a.txt'
        content = ('needle ' + RAW + '\n').encode('utf8')
        path.write_bytes(content)
        for ui, language in self.axes():
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                for pattern in ('needle', 'need.e'):
                    with patch.object(workshop.tool_report, 'note') as note:
                        value = workshop.fs_search(pattern, glob='**/*.txt', root='workspace')
                    self.assertIn('a.txt:1: needle ' + RAW, value)
                    note.assert_called_once_with(outcome='ok', target='workspace')
                    self.assertEqual(path.read_bytes(), content)

    def test_silent_and_incomplete_search_never_claim_absence16axes(self):
        for ui, language in self.axes():
            with self.subTest(ui=ui, frame=language), self.settings(ui, language):
                with patch.object(workshop.hands, 'search', return_value=None):
                    value, outcome = workshop._fs_search('needle', '**/*.txt', 'workspace')
                self.assertEqual(outcome, 'failed')
                self.assertEqual(value, fl.text('workshop.execute.61', v1='workspace'))
                with patch.object(workshop, '_search_roots', return_value=['workspace', 'soul']), \
                     patch.object(workshop.hands, 'search', side_effect=[{'ok': True, 'hits': ['a:1: ' + RAW], 'files_seen': 1}, None]) as search:
                    value = workshop._floor_search_home('needle', '**/*.txt')
                self.assertIn('workspace/a:1: ' + RAW, value)
                self.assertIn(fl.text('workshop.execute.67', v1='soul'), value)
                self.assertEqual(search.call_count, 2)
                self.assertNotIn(fl.text('workshop.execute.69', v1=1, v3='**/*.txt', v5=''), value)

    def test_search_pins_language_across_a_worker_return(self):
        settings = {'lang': 'zh', 'agent': {'frame_language': 'en'}}
        def walk(*args, **kwargs):
            settings['agent']['frame_language'] = 'fr'
            return {'ok': True, 'rows': [('a', 1, RAW)], 'files_seen': 1, 'scanned': 2, 'stop': 'time'}
        with patch.object(fl, 'settings', side_effect=lambda: settings), patch.object(workshop.hands, 'walk', side_effect=walk):
            value, outcome = workshop._fs_search('need.e', '**/*.txt', 'workspace')
        self.assertEqual(outcome, 'ok')
        self.assertIn(RAW, value)
        with fl.use('en'):
            self.assertIn(fl.text('workshop.execute.79', v1=f'{workshop.SEARCH_SECONDS:.0f}'), value)
        with fl.use('fr'):
            self.assertNotIn(fl.text('workshop.execute.79', v1=f'{workshop.SEARCH_SECONDS:.0f}'), value)

    def test_run_retains_exact_command_raw_output_and_machine_receipt16axes(self):
        for ui, language in self.axes():
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                 patch.object(workshop.hands, 'execute', return_value={'out': RAW, 'code': 3}) as execute, \
                 patch.object(workshop.tool_report, 'execution') as receipt, patch.object(workshop.tool_report, 'note') as note:
                value = workshop.run(RAW, 'proj', timeout=17)
                self.assertEqual(value, RAW + '\n[exit 3]')
                execute.assert_called_once_with(RAW, cwd=str(self.project), timeout=17, cap=workshop.RUN_OUT_CAP, base=self.tmp)
                receipt.assert_called_once_with({'kind': 'process', 'argv': ['bash', '-lc', RAW], 'cwd': str(self.project), 'status': 'returned', 'exit_code': 3})
                self.assertEqual(note.call_args.kwargs['outcome'], 'failed')

    def test_pytest_fallback_keeps_exception_predicate_argv_and_exit16axes(self):
        for ui, language in self.axes():
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), \
                 patch.object(workshop.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 1, '', 'No module named pytest'), subprocess.CompletedProcess([], 5, RAW, '')]) as run:
                self.assertEqual(workshop.run_tests('proj'), RAW + '\n[exit 5]')
                self.assertEqual(run.call_args_list[0].args[0][1:], ['-m', 'pytest', '-q'])
                self.assertEqual(run.call_args_list[1].args[0][1:], ['-m', 'unittest', 'discover', '-q'])
                self.assertTrue(all(c.kwargs['cwd'] == str(self.project) for c in run.call_args_list))
            with self.settings(ui, language), patch.object(workshop.subprocess, 'run', side_effect=subprocess.TimeoutExpired('pytest', 1)) as run:
                self.assertEqual(workshop.run_tests('proj'), fl.text('workshop.execute.95', v1=str(workshop.RUN_TIMEOUT)))
                run.assert_called_once()

    def test_pip_preflight_refuses_effects_and_success_preserves_package_argv16axes(self):
        for ui, language in self.axes():
            with self.subTest(ui=ui, frame=language), self.settings(ui, language), patch.object(workshop.subprocess, 'run') as run:
                self.assertEqual(workshop.pip_install('proj', ''), fl.text('workshop.execute.97'))
                self.assertEqual(workshop.pip_install('proj', '--index-url requests'), fl.text('workshop.execute.98', v1='--index-url'))
                with patch.object(workshop, '_du_mb', return_value=workshop.QUOTA_MB + 1):
                    self.assertEqual(workshop.pip_install('proj', 'requests'), fl.text('workshop.execute.99', v1=f'{workshop.QUOTA_MB+1:.0f}', v3=str(workshop.QUOTA_MB)))
                run.assert_not_called()
            python = self.project / '.venv/bin/python'
            with self.settings(ui, language), patch.object(workshop, '_venv_python', return_value=python), \
                 patch.object(workshop, '_du_mb', return_value=1), patch.object(workshop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, RAW, '')) as run:
                value = workshop.pip_install('proj', 'Requests==1.2 rich')
                self.assertEqual(run.call_args.args[0], [str(python), '-m', 'pip', 'install', '-q', 'Requests==1.2', 'rich'])
                self.assertEqual(value, fl.text('workshop.execute.107', v1='Requests==1.2 rich', v3=fl.text('workshop.execute.105', v1='1', v3=str(workshop.QUOTA_MB))))
