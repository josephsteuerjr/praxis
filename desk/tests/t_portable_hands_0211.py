"""Regression: independent portable program/data roots, real shell and log consumers."""
import ast
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

DESK=Path(__file__).resolve().parents[1]
CORE=DESK.parent/'helene/core'
sys.path[:0]=[str(DESK),str(DESK/'localharness'),str(CORE)]
import boot
import fence

class PortableHands(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='helene-portable-hands-')
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.program=self.base/"Программа owner's app"
        self.tree=self.base/'separate home/data'
        (self.program/'runtime').mkdir(parents=True)
        self.tree.mkdir(parents=True)
        self.saved=dict(fence.STATE)
        self.addCleanup(lambda:(fence.STATE.clear(),fence.STATE.update(self.saved)))

    def install(self,enabled=False):
        agent=types.SimpleNamespace(subprocess=subprocess,TOOL_IMPL={},BASE_TOOLS=[])
        with mock.patch.object(fence,'_shim_workshop'), mock.patch.object(fence,'_open_everything'), mock.patch.object(fence,'_open_home'):
            fence.install(agent,self.tree,{'sandbox':{'enabled':enabled}},config_path=self.program/'helene.json')
        return agent

    @unittest.skipUnless(os.name=='nt','native Windows shell')
    def test_windows_shell_routes_bare_and_absolute_names_to_program(self):
        agent=self.install()
        import process_scope
        def completed(args,*pargs,**kwargs):
            self.assertEqual(Path(args[0]),self.program/'runtime/bash.exe')
            self.assertEqual(kwargs['cwd'],self.tree/'workspace')
            return subprocess.CompletedProcess(args,0,stdout=b'PORTABLE-SHELL-OK',stderr=None)
        with mock.patch.object(process_scope,'run',side_effect=completed):
            for executable in ('bash',str(self.program/'runtime/bash.exe')):
                result=agent.subprocess.run([executable,'-lc','echo PORTABLE-SHELL-OK'],cwd=self.tree/'workspace',timeout=10)
                self.assertEqual(result.returncode,0)
                self.assertIn('PORTABLE-SHELL-OK',result.stdout)
                self.assertEqual(Path(result.args[0]),self.program/'runtime/bash.exe')

    def test_container_and_mounts_receive_actual_program_root(self):
        with mock.patch.object(fence,'Container') as container, mock.patch.object(fence,'Mounts') as mounts:
            mounts.return_value.rows.return_value=[]
            self.install(enabled=True)
            if os.name=='nt': self.assertEqual(container.call_args.args[0],self.program)
            self.assertEqual(mounts.call_args.args[1:3],(self.program,self.tree))

    def test_secondary_agent_config_does_not_move_runtime_under_agents(self):
        agent=types.SimpleNamespace(subprocess=subprocess,TOOL_IMPL={},BASE_TOOLS=[])
        config=self.program/'agents/second/helene.json'
        with mock.patch.object(fence,'_shim_workshop'),mock.patch.object(fence,'_open_everything'):
            fence.install(agent,self.tree,{'sandbox':{'enabled':False}},config_path=config,program_root=self.program)
        self.assertEqual(agent.subprocess._root,self.program)
        self.assertEqual(fence.STATE['roots'],[str(self.program)])

    def test_separate_home_read_access_keeps_program_write_and_secret_boundaries(self):
        agent=types.SimpleNamespace(subprocess=subprocess,BASE=self.tree,BASE_TOOLS=[],
            TOOL_IMPL={'fs_read':lambda path:'READ_OK','fs_write':lambda path:'WRITE_OK'})
        with mock.patch.object(fence,'Container'),mock.patch.object(fence,'Mounts') as mounts, mock.patch.object(fence,'_shim_workshop'),mock.patch.object(fence,'_open_home'):
            mounts.return_value.rows.return_value=[]
            mounts.return_value.roots.return_value=[]
            fence.install(agent,self.tree,{'sandbox':{'enabled':True}},config_path=self.program/'helene.json',program_root=self.program)
        self.assertEqual(agent.TOOL_IMPL['fs_read'](str(self.tree/'soul/note.md')),'READ_OK')
        self.assertEqual(agent.TOOL_IMPL['fs_read'](str(self.program/'tree/agent.py')),'READ_OK')
        self.assertEqual(agent.TOOL_IMPL['fs_write'](str(self.tree/'workspace/note.md')),'WRITE_OK')
        self.assertNotEqual(agent.TOOL_IMPL['fs_write'](str(self.program/'app/code.py')),'WRITE_OK')
        self.assertNotEqual(agent.TOOL_IMPL['fs_read'](str(self.base/'unmounted/file')),'READ_OK')
        self.assertNotEqual(agent.TOOL_IMPL['fs_read'](str(self.tree/'memory/llm.json')),'READ_OK')

    def test_existing_git_untracks_held_runtime_lock_preserves_authored_work(self):
        def git(*args):
            p=subprocess.run(['git','-C',str(self.tree),*args],capture_output=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'))
            return p.stdout
        git('init','-q')
        (self.tree/'.gitignore').write_text('authored-ignore/\n',encoding='utf8')
        (self.tree/'note.txt').write_text('my work',encoding='utf8')
        (self.tree/'.desktop-owner.lock').write_text('stale',encoding='utf8')
        git('add','-A')
        from localharness.phone_tunnel.platform_support import acquire_file_lock,release_file_lock
        held=acquire_file_lock(self.tree/'.desktop-owner.lock',timeout_sec=0)
        try:
            self.assertFalse(boot.seed_git(self.tree,{}))
            git('add','-A')
            files=git('ls-files').decode().splitlines()
            self.assertNotIn('.desktop-owner.lock',files)
            self.assertIn('note.txt',files)
            self.assertIn('authored-ignore/',(self.tree/'.gitignore').read_text())
            self.assertTrue((self.tree/'.desktop-owner.lock').exists())
        finally:release_file_lock(held)

    def test_runner_attaches_one_log_tail_consumed_by_both_hands(self):
        import logsink
        rootlog=logging.getLogger();before=list(rootlog.handlers)
        self.addCleanup(lambda:[(rootlog.removeHandler(h),h.close()) for h in list(rootlog.handlers) if h not in before])
        code=ast.parse((DESK/'localharness/runner.py').read_text(encoding='utf8'))
        load=next(n for n in code.body if isinstance(n,ast.FunctionDef) and n.name=='_load_tree')
        # Execute the real startup attach before agent import, without starting a model.
        attach=[]
        for n in load.body:
            if isinstance(n,ast.Import) and any(x.name=='agent' for x in n.names):break
            if isinstance(n,ast.Import) and any(x.name=='logsink' for x in n.names):attach.append(n)
            if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Attribute) and isinstance(n.value.func.value,ast.Name) and n.value.func.value.id=='logsink':attach.append(n)
        self.assertTrue(attach,'runner does not attach logsink before loading agent')
        with mock.patch.object(logsink,'LOGS_DIR',self.tree/'memory/.logs'):
            for _ in range(2):exec(compile(ast.Module(body=attach,type_ignores=[]),'runner-startup','exec'),{})
            rootlog.warning('PORTABLE-LOG-TAIL-PROBE')
            for h in rootlog.handlers:h.flush()
            text=(logsink.LOGS_DIR/'praxis.log').read_text(encoding='utf8')
            self.assertEqual(text.count('PORTABLE-LOG-TAIL-PROBE'),1)
            for filename,function,namespace,args in (
                ('agent.py','tool_read_log',{'_LOG_LINE_CLIP':280},{}),
                ('panel.py','log_tail',{'LOGS_DIR':logsink.LOGS_DIR,'LOG_NAMES':('praxis',)},{'name':'praxis'})):
                module=ast.parse((CORE/filename).read_text(encoding='utf8'))
                fn=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name==function)
                exec(compile(ast.Module(body=[fn],type_ignores=[]),filename,'exec'),namespace)
                self.assertIn('PORTABLE-LOG-TAIL-PROBE',str(namespace[function](**args)))

if __name__=='__main__':unittest.main(verbosity=2)
