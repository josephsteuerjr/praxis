"""Real memory handlers: exact evidence, paging and authority under all frame languages."""
from contextlib import ExitStack
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock,patch
import agent
import authored_notes
import frame_language as lang
import memory_provenance as provenance
from test_memory_compact_paging_2310 import META,unpack

RAW='Windows Егор 中文 {literal} $&'

class MemoryResults(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(agent,'BASE',self.root))
        self.stack.enter_context(patch.object(agent.run_context,'current_run',return_value=None))
        self.stack.enter_context(patch.object(agent,'_active_chat',return_value='42'))
        self.stack.enter_context(patch.object(agent,'_active_memory_source_ref',return_value=RAW))
    def own(self,text,language):
        if language!='ru':self.assertNotRegex(text.replace(RAW,''),'[А-Яа-яЁё]')
    def test_recall_trust_and_audience_warnings_at_actual16_axes_keep_evidence_literal(self):
        hits=[{'path':'memory/journal/sample.md','text':RAW,'source_type':provenance.UNTRUSTED_EPISODIC_KIND},
          {'path':'soul/self/history/1.md','text':RAW,'source_type':provenance.ARCHIVED_SELF_KIND},
          {'path':'memory/life/claims/c.md','text':RAW,'source_type':provenance.CLAIM_CONTESTED_KIND},
          {'path':'memory/people/sample.md','text':RAW},
          {'path':'memory/self/rooms/other/turns-x.jsonl','text':RAW}]
        original=copy.deepcopy(hits)
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                token=agent._TURN_CHANNEL.set(SimpleNamespace(owner_audience=False))
                try:
                    with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                         patch.object(agent.memory_index,'search',return_value=hits),patch.object(agent,'_active_scope',return_value='known'), \
                         patch.object(agent,'recall_cross_room',return_value=True),patch.object(agent,'_recall_room_label',return_value='room:other'):
                        value=agent.tool_recall('fixture')
                        for key in ('episodic_label','archived_label','claim_contested','claim_warning','episode_warning','archived_warning','dossier_warning','internal'):
                            self.assertIn(lang.text('memory.result.'+key),value)
                        self.assertIn('room:other',value);self.assertEqual(value.count(RAW),5);self.own(value,language)
                        self.assertEqual(hits,original)
                finally:agent._TURN_CHANNEL.reset(token)
    def test_recall_hidden_room_counts_and_empty_match_keep_existing_filter(self):
        hits=[{'path':'memory/self/rooms/other/turns-x.jsonl','text':RAW}]
        token=agent._TURN_CHANNEL.set(SimpleNamespace(owner_audience=False))
        try:
            for language in lang.LANGUAGES:
                with lang.use(language),patch.object(agent.memory_index,'search',return_value=hits),patch.object(agent,'recall_cross_room',return_value=False):
                    value=agent.tool_recall('fixture')
                    self.assertEqual(value,lang.text('memory.result.no_recall')+lang.text('memory.result.hidden',count=1))
                    self.assertNotIn(RAW,value)
        finally:agent._TURN_CHANNEL.reset(token)
    def test_pinned_language_through_recall_search_config_change(self):
        settings={'agent':{'frame_language':'fr'}}
        def search(*a,**kw):settings['agent']['frame_language']='zh';return []
        with patch.object(lang,'settings',side_effect=lambda:settings),patch.object(agent.memory_index,'search',side_effect=search):
            with lang.use('fr'):expected=lang.text('memory.result.no_recall')
            self.assertEqual(agent.tool_recall('fixture'),expected)
    def test_compact_pages_reconstruct_unicode_bytes_under_all16_axes_and_language_changes(self):
        source=(RAW+'🐺\u0301\n')*1200+'\nTAIL\t  '
        digest=hashlib.sha256(source.encode()).hexdigest()
        for ui in lang.LANGUAGES:
            for language in lang.LANGUAGES:
                pieces=[];offset=0
                with patch.object(lang,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}), \
                     patch.object(agent.memory_life,'read_compact',return_value={'ok':True,'meta':META,'text':source}):
                    while True:
                        value=agent.tool_memory_compact('read',place='42',compact_id=META['id'],start_char=offset,expected_sha256=digest if offset else '')
                        self.assertLessEqual(len(value),4000)
                        page,body=unpack(value);pieces.append(body)
                        self.assertEqual(page['sha256'],digest);self.assertEqual(page['start_char'],offset)
                        self.assertEqual(page['end_char'],offset+len(body))
                        if page['eof']:break
                        offset=page['next_start_char'];self.assertGreater(offset,0)
                self.assertEqual(''.join(pieces).encode(),source.encode())
    def test_compact_cursor_refusals_and_backend_payload_remain_canonical(self):
        for language in lang.LANGUAGES:
            with lang.use(language),patch.object(agent.memory_life,'read_compact') as read:
                out=json.loads(agent.tool_memory_compact('read',compact_id='x',start_char=2))
                self.assertEqual(out['reason'],'expected_sha256_required');self.own(out['instruction'],language);read.assert_not_called()
                out=json.loads(agent.tool_memory_compact('read',compact_id='x',start_char=-1))
                self.assertEqual(out['reason'],'invalid_start_char');read.assert_not_called()
            payload={'ok':False,'reason':'unknown','error':RAW,'source':RAW}
            with lang.use(language),patch.object(agent.memory_life,'prepare_authored_fold',return_value=payload):
                self.assertEqual(json.loads(agent.tool_memory_compact('prepare',place='42')),payload)
    def test_real_authored_note_write_read_close_preserves_text_and_reason(self):
        token=agent._TURN_CHANNEL.set(SimpleNamespace(owner_audience=True,origin_message_id=7))
        try:
            for language in lang.LANGUAGES:
                with lang.use(language):
                    value=agent.tool_manage_notes('write',text=RAW,scope='global')
                    row=authored_notes.AuthoredNoteLedger(self.root).list(status='open')[0]
                    self.assertIn(row['id'],value);self.own(value,language)
                    self.assertEqual(json.loads(agent.tool_manage_notes('read',note_id=row['id']))['text'],RAW)
                    self.assertIn(RAW,agent.tool_manage_notes('list'))
                    self.assertEqual(agent.tool_manage_notes('close',note_id=row['id'],reason=RAW),lang.text('memory.result.note_closed',id=row['id']))
                    stored=authored_notes.AuthoredNoteLedger(self.root).get(row['id'])
                    self.assertEqual(stored['text'],RAW);self.assertEqual(stored['closed_reason'],RAW)
        finally:agent._TURN_CHANNEL.reset(token)
    def test_no_summary_never_deletes_history_including_whitespace_or_provider_failure(self):
        for language in lang.LANGUAGES:
            for summary in ('','  \n\t',None):
                history=[{'role':'user','content':RAW+str(n)} for n in range(20)]
                before=copy.deepcopy(history)
                with lang.use(language),patch.object(agent,'_active_history',return_value=history), \
                     patch.object(agent,'_summarize_history',return_value=summary),patch.object(agent,'tool_journal') as journal:
                    value=agent.tool_consolidate_context()
                    self.assertEqual(value,lang.text('memory.result.history_no_summary'));self.assertEqual(history,before);journal.assert_not_called()
    def test_consolidation_journals_authored_summary_before_trimming_only_durable_source(self):
        for language in lang.LANGUAGES:
            for snapshot in (False,True):
                history=[{'role':'user','content':RAW+str(n)} for n in range(20)]
                before=copy.deepcopy(history)
                token=agent._TURN_HISTORY.set(history if snapshot else None)
                persistent=agent._TURN_HISTORY_PERSISTENT.set(False)
                try:
                    with lang.use(language),patch.object(agent,'_active_history',return_value=history),patch.object(agent,'tool_journal') as journal:
                        value=agent.tool_consolidate_context(RAW)
                        self.assertIn(RAW,journal.call_args.args[0]);self.own(value,language)
                        if snapshot:self.assertEqual(history,before)
                        else:self.assertEqual(history,before[min(agent.CONSOLIDATE_FOLD,14):])
                finally:agent._TURN_HISTORY.reset(token);agent._TURN_HISTORY_PERSISTENT.reset(persistent)
    def test_canonical_trust_index_prefixes_and_eligibility_do_not_depend_on_language(self):
        before=(provenance._CLAIM_OBSERVED_PREFIX,provenance._CLAIM_INFERRED_PREFIX)
        for language in lang.LANGUAGES:
            with lang.use(language):
                self.assertEqual(provenance.claim_prompt_label('unknown'),'')
                for source in provenance.CLAIM_KINDS|{provenance.CLAIM_RAW_KIND}:
                    self.assertTrue(provenance.claim_prompt_label(source));self.own(provenance.claim_prompt_label(source),language)
                self.assertEqual((provenance._CLAIM_OBSERVED_PREFIX,provenance._CLAIM_INFERRED_PREFIX),before)
