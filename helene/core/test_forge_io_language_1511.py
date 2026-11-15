"""Actual Forge admission, durable tasks and byte-bound reads across independent locales."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import agent
import forge
import frame_language as fl

RAW='Literal Русский 中文 {v1} <script>inert</script>'

class ForgeIOLanguage(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.base=Path(temp.name)
        self.root=self.base/'project';self.root.mkdir();self.state=self.base/'state';self.state.mkdir();self.tasks=self.state/'tasks';self.tasks.mkdir()
        self.enterContext(patch.object(forge,'BASE',self.base));self.enterContext(patch.object(forge,'REPO',self.base/'self'))
        self.enterContext(patch.object(forge,'STATE_DIR',self.state));self.enterContext(patch.object(forge,'TASKS_DIR',self.tasks));self.enterContext(patch.object(forge,'_UPSTREAMS',self.state/'upstreams.json'))
        self.enterContext(patch.object(forge.forge_learning,'recall',return_value=[]))
        self.enterContext(patch.object(agent,'_active_chat',return_value='chat-literal'))

    def settings(self,ui,language):return patch.object(fl,'settings',return_value={'lang':ui,'agent':{'frame_language':language}})
    def snapshot(self):return {str(p.relative_to(self.base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.base.rglob('*') if p.is_file()}
    def open(self):return forge.start(RAW,target=str(self.root),isolation='direct',success_criteria=[RAW])

    def test_actual_local_start_has_an_id_even_when_contract_precedes_caption16axes(self):
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language),patch.object(agent,'tool_journal') as journal:
                    out=agent.tool_coding_session('start',goal=RAW,target=str(self.root),isolation='direct',success_criteria=[RAW])
                    self.assertIsInstance(out,str);self.assertIsInstance(out,forge.TaskStartReply)
                    task=forge.get(out.task_id);self.assertEqual(task['goal'],RAW);self.assertEqual(task['origin_chat'],'chat-literal');self.assertEqual(task['success_criteria'],[RAW])
                    self.assertIn(fl.text('forge.overview.9',v1=out.task_id,v3=RAW),out)
                    self.assertTrue(out.startswith(fl.text('forge.support.1')))
                    journal.assert_called_once_with(fl.text('forge.start.local',goal=RAW),salience=2)

    def test_actual_remote_start_is_one_probe_with_raw_text_and_task_id16axes(self):
        for scope,client,target in [('host',forge.serverd_client,'/host/literal'),('windows',forge.body_client,'C:\\Literal')]:
            for ui in fl.LANGUAGES:
                for language in fl.LANGUAGES:
                    with self.settings(ui,language),patch.object(client,'available',return_value=True),patch.object(client,'workspace_inspect_result',return_value={'ok':True,'text':RAW,'head':'a'*40}) as probe,patch.object(agent,'tool_journal') as journal:
                        out=agent.tool_coding_session('start',goal=RAW,target=target,scope=scope)
                        probe.assert_called_once_with(target,'orientation');self.assertIsInstance(out,forge.TaskStartReply)
                        task=forge.get(out.task_id);self.assertEqual(task['root'],target);self.assertEqual(task['scope'],scope);self.assertEqual(task['base_commit'],'a'*40)
                        self.assertIn(RAW,out);journal.assert_called_once_with(fl.text('forge.start.'+scope,goal=RAW),salience=2)
                        self.assertEqual(out.endswith(fl.text('run.result.legacy')),scope=='windows')

    def test_unconfirmed_and_forged_display_responses_never_admit_or_journal(self):
        before=self.snapshot()
        for scope,method in [('self','start'),('host','start_host'),('windows','start_windows')]:
            for language in fl.LANGUAGES:
                with fl.use(language),patch.object(forge,method,return_value='coding-задача code-fake: '+RAW),patch.object(agent,'tool_journal') as journal:
                    out=agent.tool_coding_session('start',scope=scope,goal=RAW,target='/literal')
                    self.assertFalse(hasattr(out,'task_id'));journal.assert_not_called()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language),patch.object(agent,'tool_journal') as journal:
                    out=agent.tool_coding_session('start',goal='',target=str(self.root))
                    self.assertEqual(out,fl.text('forge.io.5'));self.assertFalse(hasattr(out,'task_id'));journal.assert_not_called()
        self.assertEqual(self.snapshot(),before)

    def test_probe_cannot_change_language_halfway_through_start_or_journal(self):
        choice={'language':'en'}
        def settings():return {'lang':'zh','agent':{'frame_language':choice['language']}}
        def probe(*args):choice['language']='fr';return {'ok':True,'text':RAW}
        with patch.object(fl,'settings',side_effect=settings),patch.object(forge.serverd_client,'available',return_value=True),patch.object(forge.serverd_client,'workspace_inspect_result',side_effect=probe) as rpc,patch.object(agent,'tool_journal') as journal:
            out=agent.tool_coding_session('start',goal=RAW,target='/literal',scope='host');rpc.assert_called_once()
        with fl.use('en'):
            self.assertTrue(out.startswith(fl.text('forge.io.18',v1=out.task_id,v3=RAW,v5='')));journal.assert_called_once_with(fl.text('forge.start.host',goal=RAW),salience=2)

    def test_actual_exact_read_reconstructs_raw_unicode_and_keeps_machine_cursor16axes(self):
        raw=('前缀 {v1} Русский '+('\t\\\"'*4000)+'\n'+RAW+'\n').encode('utf8');path=self.root/'literal.txt';path.write_bytes(raw);out=self.open();task_id=out.task_id;digest=hashlib.sha256(raw).hexdigest();before=self.snapshot()
        for ui in fl.LANGUAGES:
            for language in fl.LANGUAGES:
                with self.settings(ui,language):
                    cursor={'start':1,'start_char':0,'expected_sha256':digest};parts=[]
                    while True:
                        page=forge.inspect(task_id,'read',path='literal.txt',**cursor);self.assertLessEqual(len(page),8000);row=json.loads(page)
                        self.assertEqual(row['sha256'],digest);self.assertEqual(row['path'],'literal.txt');parts.append(row['text'])
                        if row['complete']:break
                        cursor['start'],cursor['start_char']=row['next_start'],row['next_char']
                    self.assertEqual(''.join(parts).encode('utf8'),raw)
                    refused=forge.inspect(task_id,'read',path='literal.txt',expected_sha256='0'*64)
                    self.assertEqual(refused.outcome,'refused');self.assertEqual(refused,fl.text('forge.io.26',v1='0'*64,v3=digest));self.assertEqual(self.snapshot(),before)

    def test_actual_missing_binary_and_invalid_utf8_reads_do_not_rewrite_files(self):
        task_id=self.open().task_id;binary=self.root/'binary';binary.write_bytes(b'\x00raw\xff');invalid=self.root/'invalid';invalid.write_bytes(b'\xff\xfe');before=self.snapshot()
        for language in fl.LANGUAGES:
            with fl.use(language):
                missing=forge.inspect(task_id,'read',path='absent');self.assertEqual(missing.outcome,'refused');self.assertEqual(missing,fl.text('forge.io.44',v1='absent'))
                value=forge.inspect(task_id,'read',path='binary');self.assertEqual(value,fl.text('forge.io.45',v0='binary',v2=5,v4=hashlib.sha256(binary.read_bytes()).hexdigest()[:16]))
                value=forge.inspect(task_id,'read',path='invalid',expected_sha256=hashlib.sha256(invalid.read_bytes()).hexdigest());self.assertEqual(value.outcome,'refused');self.assertIn(hashlib.sha256(invalid.read_bytes()).hexdigest(),value)
                self.assertEqual(self.snapshot(),before)

    def test_remote_exact_cursor_is_refused_before_rpc_with_observation_outcome(self):
        task_id=self.open().task_id;task=forge.get(task_id);task['scope']='host';forge._save_task(task);before=self.snapshot()
        for language in fl.LANGUAGES:
            with fl.use(language),patch.object(forge.serverd_client,'workspace_inspect') as rpc:
                out=forge.inspect(task_id,'read',path='literal',expected_sha256='a'*64)
                self.assertEqual(out.outcome,'refused');self.assertEqual(out,fl.text('forge.io.40'));rpc.assert_not_called();self.assertEqual(self.snapshot(),before)

    def test_status_keeps_canonical_ids_json_and_literal_history_for_missing_root(self):
        task_id=self.open().task_id;task=forge.get(task_id);task['root']=str(self.root/'absent');forge._save_task(task);before=self.snapshot()
        for language in fl.LANGUAGES:
            with fl.use(language):
                out=forge.inspect(task_id,'status');self.assertIn(task_id,out);self.assertIn('[active]',out);self.assertIn(RAW,out);self.assertIn(fl.text('forge.io.53'),out)
                line=next(line for line in out.splitlines() if line.startswith(fl.text('forge.io.53')));record=json.loads(line[len(fl.text('forge.io.53')):]);self.assertEqual(record['goal'],RAW)
                self.assertIn(fl.text('forge.io.33'),out);self.assertEqual(self.snapshot(),before)

if __name__=='__main__':unittest.main()
