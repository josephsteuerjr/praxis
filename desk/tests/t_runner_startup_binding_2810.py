"""Execute the speech setup call with main's actual control_watch bindings.

No model, profile mutation, services or background workers are started.
"""
import ast
from pathlib import Path
import types
import unittest
from unittest import mock
import sys


class RunnerStartupBinding(unittest.TestCase):
    def test_speech_setup_can_access_control_boundary_before_maintenance_starts(self):
        path=Path(__file__).resolve().parents[1]/'localharness/runner.py'
        module=ast.parse(path.read_text(encoding='utf-8'))
        main=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        # Keep the real call and all same-scope imports of this name in their
        # original order: a late import must not break the earlier setup call.
        selected=[]
        for node in main.body:
            if isinstance(node,ast.Import) and any(a.name=='control_watch' for a in node.names):selected.append(node)
            if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call):
                fn=node.value.func
                if isinstance(fn,ast.Attribute) and isinstance(fn.value,ast.Name) and fn.value.id=='speech_bridge' and fn.attr=='install':selected.append(node)
        self.assertTrue(any(isinstance(n,ast.Expr) for n in selected))
        main.body=selected
        boundary=types.ModuleType('control_watch');installed=[]
        namespace={'control_watch':boundary,'agent':object(),'tree':object(),'config_path':object(),
                   'owner_stop':object(),'speech_bridge':types.SimpleNamespace(install=lambda *args,**kw:installed.append(kw['boundary']))}
        with mock.patch.dict(sys.modules,{'control_watch':boundary}):
            exec(compile(ast.fix_missing_locations(ast.Module(body=[main],type_ignores=[])),str(path),'exec'),namespace)
            namespace['main']()
        self.assertEqual(installed,[boundary])


if __name__=='__main__':unittest.main()
