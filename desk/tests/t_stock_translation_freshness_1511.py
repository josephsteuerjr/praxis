"""A changed canonical stock text cannot silently retain obsolete translations."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

DESK=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('stock_language_audit',DESK/'installer/stock_languages.py')
stock=importlib.util.module_from_spec(spec);spec.loader.exec_module(stock)


class StockFreshness(unittest.TestCase):
    def test_all_present_translations_are_bound_and_missing_paths_are_visible(self):
        result=stock.audit(DESK/'resources')
        self.assertGreater(result['reviewed_files'],0)
        self.assertEqual(result['canonical_files'],result['reviewed_files']+len(result['missing']['en']))
        self.assertEqual(result['missing']['en'],result['missing']['fr'])
        self.assertEqual(result['missing']['fr'],result['missing']['zh'])

    def test_source_translation_and_orphan_changes_refuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);shutil.copytree(DESK/'resources',root/'resources')
            resources=root/'resources'
            for relative in ('SOUL.md','SOUL.fr.md'):
                path=resources/relative;before=path.read_bytes()
                path.write_bytes(before+b'\nUnreviewed change\n')
                with self.assertRaisesRegex(ValueError,'changed'):stock.audit(resources)
                path.write_bytes(before)
            orphan=resources/'soul.zh/skills/UNREVIEWED.md';orphan.write_text('Unreviewed',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Orphan'):stock.audit(resources)


if __name__=='__main__':unittest.main()
