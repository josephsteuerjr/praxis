"""Actual roots, project files and saved task/upstream facts remain independent of captions."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import forge
import frame_language as fl

RAW='Literal Русский 中文 {v1} <script>inert</script>'

class ForgeOverviewLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.state=self.base/'state';self.state.mkdir();self.tasks=self.state/'tasks';self.tasks.mkdir()
        self.root=self.base/'project';self.root.mkdir();(self.root/'README.md').write_text(RAW,encoding='utf8');(self.root/'example.py').write_text('print("literal")\n',encoding='utf8')
        self.enterContext(patch.object(forge,'STATE_DIR',self.state));self.enterContext(patch.object(forge,'TASKS_DIR',self.tasks));self.enterContext(patch.object(forge,'_UPSTREAMS',self.state/'upstreams.json'))
        self.task={'id':'code-local','root':str(self.root),'goal':RAW,'scope':'local','status':'active','isolation':'source','success_criteria':[RAW],'verify_commands':['python -c "'+RAW+'"']}

    def snapshot(self):return {str(p.relative_to(self.base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.base.rglob('*') if p.is_file()}
    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})

    def test_actual_orientation_preserves_files_contract_values_and_coordinator_protocol16axes(self):
        seen={};before=self.snapshot()
        with patch.object(forge.forge_learning,'recall',return_value=[]):
            for ui in fl.LANGUAGES:
                for language in fl.LANGUAGES:
                    with self.settings(ui,language):
                        value=forge._orientation_text(self.task)
                        self.assertIn(RAW,value);self.assertIn('README.md',value);self.assertIn('example.py',value);self.assertIn('python -c',value)
                        self.assertIn(fl.text('forge.support.1'),value)
                        self.assertIn(fl.text('forge.overview.10',v1=str(self.root)),value)
                        self.assertIn(fl.text('forge.support.2',v1='').strip(),value)
                        body,record=value.rsplit('\nCOORDINATOR STATE\n',1);self.assertIsInstance(json.loads(record),dict)
                        self.assertEqual(seen.setdefault(language,value),value);self.assertEqual(self.snapshot(),before)
        self.assertEqual(len(set(seen.values())),4)

    def test_missing_child_in_existing_worktree_is_an_observation_not_a_history_or_integrity_claim(self):
        task=dict(self.task,root=str(self.root/'missing'),worktree_root=str(self.root));before=self.snapshot()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    hint=fl.text('forge.overview.4')
                    self.assertEqual(forge._missing_root_reason(task,Path(task['root'])),fl.text('forge.overview.5',v1=task['root'],v3=self.root.resolve(),v5='missing',v7=hint))
                    self.assertEqual(forge._root_state_line(task),fl.text('forge.overview.24',v1=task['root']))
                    self.assertEqual(forge._root_state_line(self.task),fl.text('forge.overview.26',v1=self.root))
                    self.assertEqual(self.snapshot(),before)
        self.assertFalse((self.root/'missing').exists());self.assertEqual(task['status'],'active')

    def test_remote_orientation_rpc_is_once_and_observed_output_is_never_rewritten(self):
        for scope,client in [('host',forge.serverd_client),('windows',forge.body_client)]:
            for language in fl.LANGUAGES:
                with fl.use(language),patch.object(client,'workspace_inspect',return_value=RAW) as rpc:
                    answer=forge._orientation_text(dict(self.task,scope=scope,base_commit='a'*40))
                    rpc.assert_called_once_with(str(self.root),'orientation',base='a'*40);self.assertEqual(answer,RAW)

    def test_actual_upstream_store_keeps_hashes_source_timestamps_and_untold_selection(self):
        row={'key':'fixture/repository','url':'https://example.invalid/'+RAW,'tasks':['code-local'],
             'known_head':'a'*40,'head':'b'*40,'known_source':RAW,'checked_at':'2026-01-01T00:00:00','last_move_at':'2026-01-02T00:00:00'}
        path=forge._UPSTREAMS;path.write_text(json.dumps({row['key']:row}),encoding='utf8');before=path.read_bytes()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    line=forge.upstream_line();self.assertIn(row['key'],line);self.assertIn('aaaaaaaa→bbbbbbbb',line)
                    note=forge._upstream_note(self.task);self.assertIn(row['url'],note);self.assertIn('aaaaaaaa',note);self.assertIn('bbbbbbbb',note)
                    full=forge._upstream_row_line(row);self.assertIn(row['url'],full);self.assertIn(row['checked_at'],full)
                    self.assertEqual(path.read_bytes(),before)
        row['known_head']=row['head'];path.write_text(json.dumps({row['key']:row}),encoding='utf8')
        self.assertEqual(forge.upstream_line(),'')

    def test_state_counts_distinguish_lost_unknown_and_running_without_starting_anything(self):
        directory=self.tasks/self.task['id'];directory.mkdir();(directory/'task.json').write_text(json.dumps(self.task),encoding='utf8')
        lost={'id':'code-lost','status':'lost'};other=self.tasks/lost['id'];other.mkdir();(other/'task.json').write_text(json.dumps(lost),encoding='utf8')
        before=self.snapshot()
        def units(task_id,kind):return [{'status':'running'},{'status':'unknown'}] if kind=='agents' else [{'status':'running'}]
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language),patch.object(forge,'_units',side_effect=units):
                    expected=fl.text('forge.overview.37',v1=1,v3=1,v5=0,v7=1)+fl.text('forge.overview.38',v1=1)+fl.text('forge.overview.39',v1=1,v3='code-lost')
                    self.assertEqual(forge.state_line(),expected);self.assertEqual(self.snapshot(),before)

    def test_pin_survives_mid_read_settings_change_and_caps_do_not_translate_data(self):
        choice={'language':'en'}
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def read():choice['language']='fr';return {'x':{'key':RAW,'head':'b'*40,'known_head':'a'*40}}
        with patch.object(fl,'settings',side_effect=settings),patch.object(forge,'_load_upstreams',side_effect=read) as loader:
            value=forge.upstream_line();loader.assert_called_once()
        with fl.use('en'):self.assertEqual(value,fl.text('forge.overview.27',v1=1)+RAW+' aaaaaaaa→bbbbbbbb')
        source=RAW*200
        for language in fl.LANGUAGES:
            with fl.use(language):
                self.assertEqual(forge._cap(RAW,16000),RAW)
                limited=forge._cap(source,1000);head=int(1000*.62);tail=int(1000*.33)
                self.assertEqual(limited,source[:head]+fl.text('forge.support.0',v1=len(source)-head-tail)+source[-tail:])

if __name__=='__main__':unittest.main()
