"""Exercise the production resolver without loading providers or credentials."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock


class Selection(unittest.TestCase):
    def test_relay_does_not_silently_replace_sol_with_astra(self):
        path=Path(__file__).resolve().parents[2]/'helene/core/llm.py'
        fn=next(node for node in ast.parse(path.read_text(encoding='utf8')).body if isinstance(node,ast.FunctionDef) and node.name=='_resolve_model')
        discover=Mock(return_value=['gpt-6-astra'])
        scope={'_config':lambda:{'frameworks':{'voice':{'provider':'relay'}}},'_available_models':discover}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),scope)
        self.assertEqual(scope['_resolve_model']('voice','gpt-6.1-sol'),'gpt-6.1-sol')
        discover.assert_not_called()


if __name__=='__main__':unittest.main()
