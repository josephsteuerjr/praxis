"""Scheduled trigger survives the runner→disk→GUI seam without text guessing."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from localharness.run_trigger import record_wake
from deskd import readers


class Trigger(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name)
        env = patch.dict(os.environ, {'HELENE_TREE':str(self.tree)}); env.start(); self.addCleanup(env.stop)
        self.rid='run-20261008T120000000000Z-abcdef12'
        self.path=self.tree/'memory/runs/2026-10'/self.rid
        self.path.mkdir(parents=True)
        self.manifest={'context':{'kind':'chat_turn','origin_chat_id':'window','goal':'Сработал будильник'},'status':'completed'}
        self.write()

    def write(self):
        (self.path/'manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')

    def test_bound_wake_appears_on_desktop_and_phone_and_keeps_kernel_kind(self):
        record_wake(self.tree,self.rid,'window','alarm-actual-task-1000')
        record_wake(self.tree,self.rid,'window','alarm-actual-task-1000')
        rows=readers.list_runs(kind='wake',with_titles=False)
        self.assertEqual([r['id'] for r in rows],[self.rid])
        self.assertEqual(rows[0]['kernel_kind'],'chat_turn')
        self.assertEqual(readers.list_runs(kind='chat_turn',with_titles=False),[])
        self.assertEqual(json.loads((self.path/'manifest.json').read_text()),self.manifest)

    def test_authored_alarm_words_do_not_create_a_wake(self):
        self.assertEqual(readers.list_runs(kind='wake',with_titles=False),[])
        self.assertEqual(readers.list_runs(with_titles=False)[0]['kind'],'chat_turn')

    def test_wrong_chat_and_rebinding_refused(self):
        with self.assertRaises(ValueError):record_wake(self.tree,self.rid,'another-chat','alarm-real')
        record_wake(self.tree,self.rid,'window','alarm-real')
        with self.assertRaises(ValueError):record_wake(self.tree,self.rid,'window','alarm-other')

    def test_mismatched_and_malformed_receipts_are_ignored(self):
        record_wake(self.tree,self.rid,'window','alarm-real')
        target=self.path/'helene-trigger.json'
        row=json.loads(target.read_text())
        for field,value in [('run_id','another-run'),('chat_id','another-chat'),('kind','message'),('schema','unknown'),('source_id','user-alarm')]:
            with self.subTest(field=field):
                target.write_text(json.dumps({**row,field:value}))
                self.assertEqual(readers.list_runs(kind='wake',with_titles=False),[])
        target.write_text('bad JSON')
        self.assertEqual(readers.list_runs(kind='wake',with_titles=False),[])

    def test_invalid_run_cannot_escape_tree_or_invent_a_run(self):
        for rid in ['../../outside', self.rid.replace('abcdef12','12345678')]:
            with self.assertRaises((ValueError,OSError)):record_wake(self.tree,rid,'window','alarm-real')
        self.assertFalse((self.path/'helene-trigger.json').exists())


if __name__=='__main__':unittest.main()
