"""Typed source hashes must not hide real owner fields or unrelated JSON."""
import ast
import json
from pathlib import Path
import re
import unittest


class InventoryPrivacy(unittest.TestCase):
    def test_only_known_digest_fields_are_exempt(self):
        source = Path(__file__).resolve().parents[1]/'installer/personal_scan.py'
        tree = ast.parse(source.read_text(encoding='utf8'))
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'without_inventory_digests')
        namespace = {'json':json,'re':re}; exec(compile(ast.Module(body=[node],type_ignores=[]),'scanner','exec'),namespace)
        filter = namespace['without_inventory_digests']
        ident = '12345' + '67890'; digest = 'abcdef' + ident + 'abcdef' * 4
        self.assertEqual(len(digest),40)
        typed = json.dumps({'tree_inventory_scope':'published_payload','source_public_parent':digest,'owner_id':ident})
        result = filter('HELENE-SOURCE.json',typed)
        self.assertNotIn(digest,result); self.assertIn(ident,result)
        self.assertEqual(filter('contacts.json',typed),typed)
        untyped = json.dumps({'secret':digest,'owner_id':ident})
        self.assertEqual(filter('HELENE-SOURCE.json',untyped),untyped)


if __name__ == '__main__': unittest.main()
