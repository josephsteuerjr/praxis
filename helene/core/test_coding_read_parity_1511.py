"""Exact archived reads are pure; unavailable live state remains visible."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import agent
import forge
import frame_language

RAW='Alex Алекс 日本語 {literal} $&\nnext line'


class ExactArchivedReads(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='helene-exact-archive-')
        self.addCleanup(temporary.cleanup);self.base=Path(temporary.name)
        self.root=self.base/'project';self.root.mkdir()
        for name,value in (('BASE',self.base),('STATE_DIR',self.base/'memory/.forge'),
                           ('TASKS_DIR',self.base/'memory/.forge/tasks')):
            guard=patch.object(forge,name,value);guard.start();self.addCleanup(guard.stop)
        self.task='code-read';self.unit='agent-read'
        forge._save_task({'id':self.task,'root':str(self.root),'status':'active','scope':'self'})
        self.directory=forge._unit_dir(self.task,'agents',self.unit);self.directory.mkdir(parents=True)

    def test_unicode_result_pages_are_exact_and_never_reconcile_or_write_custody(self):
        text=json.dumps({'result':RAW*90},ensure_ascii=False)
        path=self.directory/'result.json';path.write_bytes(text.encode('utf-8'))
        digest=hashlib.sha256(path.read_bytes()).hexdigest();before=path.read_bytes()
        self.root.rmdir()
        with (
            patch.object(forge,'_unit_state',side_effect=AssertionError('read must not reconcile')),
            patch.object(forge,'_mutation_lock',side_effect=AssertionError('read must not mutate')),
        ):
            for language in frame_language.LANGUAGES:
                with frame_language.use(language):
                    parts=[];offset=0
                    while True:
                        page=json.loads(agent.tool_coding_agent(self.task,'payload',agent_id=self.unit,
                            kind='result',payload_sha256=digest,offset=offset,limit=113))
                        self.assertEqual(page['sha256'],digest);self.assertEqual(page['size_bytes'],len(before))
                        parts.append(page['text'])
                        if page['next_offset'] is None:break
                        self.assertGreater(page['next_offset'],offset);offset=page['next_offset']
                    self.assertEqual(''.join(parts),text)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(sorted(file.name for file in self.directory.iterdir()),['result.json'])

    def test_events_page_without_a_project_root_or_unit_state_recovery(self):
        rows=[{'seq':index,'kind':'literal','payload':RAW} for index in range(1,6)]
        path=self.directory/'events.jsonl'
        path.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        self.root.rmdir();before=path.read_bytes()
        with patch.object(forge,'_unit_state',side_effect=AssertionError('pure event read')):
            first=json.loads(agent.tool_coding_agent(self.task,'events',agent_id=self.unit,limit=2))
            second=json.loads(agent.tool_coding_agent(self.task,'events',agent_id=self.unit,
                limit=10,after_seq=first['next_after_seq']))
        self.assertEqual(first['events']+second['events'],rows)
        self.assertFalse(second['has_more']);self.assertEqual(path.read_bytes(),before)

    def test_parent_scope_and_changed_record_cannot_return_payload(self):
        path=self.directory/'result.json';path.write_text(RAW,encoding='utf-8')
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        with (
            patch.object(forge,'_worker_parent',return_value=('agent-parent','request')),
            patch.object(forge,'_in_subtree',return_value=False),
        ):
            refused=agent.tool_coding_agent(self.task,'payload',agent_id=self.unit,kind='result',payload_sha256=digest)
        self.assertNotIn(RAW,refused);self.assertIn('соседней веткой',refused)
        path.write_text(RAW+' changed',encoding='utf-8')
        value=json.loads(agent.tool_coding_agent(self.task,'payload',agent_id=self.unit,kind='result',payload_sha256=digest))
        self.assertFalse(value['ok']);self.assertNotIn('text',value)


class LiveOrientationLanguage(unittest.TestCase):
    def test_unproven_and_coding_only_rows_survive_all16_language_axes_and_advisory_limit(self):
        for ui in frame_language.LANGUAGES:
            for language in frame_language.LANGUAGES:
                settings={'lang':ui,'agent':{'frame_language':language}}
                manager=SimpleNamespace(live_run_summaries=lambda:[{'run_id':'run-observed',
                    'status':'unknown','kind':'wake','observation_error':RAW}])
                with (
                    patch.object(frame_language,'settings',return_value=settings),
                    patch.object(agent,'_runs',return_value=manager),
                    patch.object(agent,'_coding_tasks_line',return_value='\n'+RAW),
                    patch.object(agent,'resume_wait_seconds',side_effect=AssertionError('do not re-lock an unavailable run')),
                ):
                    result=agent.tool_list_active_runs(limit=0)
                    self.assertIn(RAW,result);self.assertIn('run-observed [unknown]',result)
                    self.assertIn(frame_language.text('runs.active.observation',error=RAW),result)
                manager.live_run_summaries=lambda:[]
                with (
                    patch.object(frame_language,'settings',return_value=settings),
                    patch.object(agent,'_runs',return_value=manager),
                    patch.object(agent,'_coding_tasks_line',return_value='\n'+RAW),
                ):
                    self.assertEqual(agent.tool_list_active_runs(),frame_language.text('runs.active.empty')+'\n'+RAW)


if __name__=='__main__':unittest.main()
