"""The missing native -> Python -> merged/reviewed-code connection.

Actual merge, selected-agent CAS edits, independent Service records and the real
migration lifecycle are used. Only provider/critic execution and Windows stop
are replaced with bounded fixtures; no live user agent or model is called.
"""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import windows_installation as installation,code_transition,source_update as pipeline
from localharness import agent_source_update as agent_resolution
from localharness.package_notice import read,write
import t_source_update_pipeline_220 as fixture
from t_source_merge_211 import BASE,BOTH,put


class NativeBridge(unittest.TestCase):
    def setup_fixture(self,conflict=True):
        f=fixture.PipelineTests('runTest');f.setUp();self.addCleanup(f.doCleanups)
        if not conflict:put(f.code,'module.py',BASE+'\ncustom_extension = True\n')
        else:put(f.code,'module.py',fixture.LOCAL+'\nlocal_extension = 11\n')
        # A native-style predecessor older than 2.2.1, with a retained stock tree.
        (f.old/'python-app.json').unlink();write(f.old/'build.json',{'version':'1.7.0'})
        put(f.old,'helene.exe','native fixture')
        write(f.new/'python-app.json',{'version':'2.2.10','delivery':'portable'})
        for name in ('app/python_app.py','app/deskapp.py',('runtime/python.exe' if os.name=='nt' else 'runtime/bin/python3')):put(f.new,name,'fixture')
        code_transition.seal(f.new)
        put(f.tree,'memory/history.jsonl','retained-private-history\n')
        return f

    def exercise(self,conflict):
        f=self.setup_fixture(conflict);events=[]
        original=code_transition.preflight
        def preflight(*args,**kwargs):
            result=original(*args,**kwargs)
            for row in result['plans']:
                f.directory=Path(row['directory'])
                self.assertFalse((f.directory/'agent-resolution.json').exists(),
                                 'A native predecessor must not need the new selected-agent API')
            return result
        def incoming_review(old,new,plans,progress,cancelled):
            self.assertEqual(old,f.old);self.assertEqual(new,f.new)
            events.append('incoming-panel')
            for row in plans:
                if row['state']=='ready':continue
                f.directory=Path(row['directory'])
                binding={'kind':'incoming-test','program':str(new),'plan_id':f.directory.name}
                pipeline.ensure(f.tree,old,f.code,f.directory,'2.2.10',coordinator=binding)
                pipeline.tick(f.tree,f.forge,f.service,coordinator=binding)
                if f.operation()['state']=='merging':
                    merged=BASE.replace('return 1','return 99')+'\nlocal_extension = 11\n'
                    f.merge_result(merged)
                    pipeline.tick(f.tree,f.forge,f.service,coordinator=binding)
                else:
                    self.assertFalse(any('source_update_worker.py' in str(row[0]) for row in f.spawns))
                self.assertEqual(f.operation()['state'],'reviewing')
                f.review_result()
                pipeline.tick(f.tree,f.forge,f.service,coordinator=binding)
                self.assertEqual(pipeline.validate_ready(f.directory)['state'],'ready_to_apply')
        before=(f.old/'helene.json').read_bytes()
        with patch.object(code_transition,'preflight',side_effect=preflight), \
             patch('localharness.source_update_host.review',side_effect=incoming_review), \
             patch('localharness.migration_lifecycle.service',return_value=None), \
             patch('localharness.migration_lifecycle.stop',side_effect=lambda _:events.append('stop-old')), \
             patch('localharness.migration_lifecycle.quiescent'):
            note=installation.run(f.new,f.new,old=f.old,data=f.tree,source_mode='preserve',launch=False)
        cfg=read(f.new/'helene.json');selected=Path(cfg['code'])
        result=(selected/'module.py').read_text()
        self.assertIn('return 99',result)
        self.assertIn('local_extension = 11' if conflict else 'custom_extension = True',result)
        self.assertEqual(events,['incoming-panel','stop-old'])
        self.assertEqual(Path(cfg['tree']),f.tree);self.assertEqual(note['copied_data'],[])
        self.assertEqual(note['state'],'prepared');self.assertEqual(note['source_policy'],'preserve')
        self.assertEqual((f.old/'helene.json').read_bytes(),before)
        self.assertEqual((f.tree/'memory/history.jsonl').read_text(),'retained-private-history\n')
        self.assertTrue(Path(note['archives'][0]['path']).is_file())

    def test_native_before_221_combines_conflicts_then_new_panel_before_shutdown(self):self.exercise(True)

    def test_automatic_merge_still_requires_new_panel_for_migration(self):self.exercise(False)

    def test_failed_panel_does_not_stop_or_retire_native_program(self):
        f=self.setup_fixture(False)
        with patch('localharness.source_update_host.review',side_effect=RuntimeError('panel has findings')), \
             patch('localharness.migration_lifecycle.stop') as stop,patch('localharness.migration_lifecycle.remove_program') as remove:
            with self.assertRaisesRegex(RuntimeError,'panel has findings'):
                installation.run(f.new,f.new,old=f.old,data=f.tree,source_mode='preserve',launch=False)
            stop.assert_not_called();remove.assert_not_called()
        self.assertTrue((f.old/'helene.exe').is_file())


if __name__=='__main__':unittest.main()
