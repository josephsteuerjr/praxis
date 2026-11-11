import contextlib
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import edit_batch as batch
import hands
import workshop
import forge
import agent
import forge_worker
import tool_report


def sha(raw): return hashlib.sha256(raw).hexdigest()


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.a=self.root/'a'; self.b=self.root/'b'
        self.a.write_bytes(b'alpha beta\r\n'); self.b.write_bytes(b'gamma delta\n')

    def item(self, path, old, new):
        return {'path':path.name,'expected_sha256':sha(path.read_bytes()),'edits':[{'old':old,'new':new}]}

    def resolve(self, p): return self.root/p, ''
    def apply(self, files, mode='snapshot'): return batch.apply(files,self.resolve,mode)

    def test_snapshot_independence_and_crlf(self):
        f=self.item(self.a,'alpha','beta'); f['edits'].append({'old':'beta','new':'end'})
        r=self.apply([f]); self.assertEqual(r['status'],'confirmed')
        self.assertEqual(self.a.read_bytes(),b'beta end\r\n')
        self.assertEqual(r['files'][0]['after_sha256'],sha(self.a.read_bytes()))

    def test_sequential_explicit(self):
        f=self.item(self.a,'alpha','omega'); f['edits'].append({'old':'omega','new':'done'})
        self.assertEqual(self.apply([f])['status'],'refused')
        self.assertEqual(self.apply([f],'sequential')['status'],'confirmed')
        self.assertEqual(self.a.read_bytes(),b'done beta\r\n')

    def test_overlap_refused(self):
        f=self.item(self.a,'alpha beta','x'); f['edits'].append({'old':'beta','new':'y'})
        self.assertEqual(self.apply([f])['status'],'refused'); self.assertEqual(self.a.read_bytes(),b'alpha beta\r\n')

    def test_overlapping_occurrences_not_unique(self):
        data,reason=batch.replacements(b'aaa',[{'old':'aa','new':'b'}])
        self.assertIsNone(data)

    def test_single_replacement_has_the_same_uniqueness_contract(self):
        data,reason=hands.replace_once(b'aaa','aa','b')
        self.assertIsNone(data)
        self.assertIn('2',reason)

    def test_adjacent_deletion_and_unicode(self):
        data,reason=batch.replacements('абв'.encode(),[{'old':'а','new':''},{'old':'б','new':'猫'}])
        self.assertEqual(data,'猫в'.encode())

    def test_invalid_utf8_refused(self):
        self.assertIsNone(batch.replacements(b'\xff',[{'old':'x','new':'y'}])[0])

    def test_all_file_preflight(self):
        f=self.item(self.b,'gamma','x'); f['expected_sha256']='0'*64
        r=self.apply([self.item(self.a,'alpha','y'),f])
        self.assertEqual(r['status'],'refused'); self.assertEqual(len(r['files']),2)
        self.assertEqual(self.a.read_bytes(),b'alpha beta\r\n')

    def test_duplicate_path_alias_refused(self):
        f=self.item(self.a,'beta','y'); f['path']='./a'
        self.assertEqual(self.apply([self.item(self.a,'alpha','x'),f])['status'],'refused')

    def test_missing_hash_and_malformed_inputs(self):
        for files in (None,[],[None],[{}],[{'path':'a'}]):
            self.assertEqual(self.apply(files)['status'],'refused')

    def test_empty_edits_invalid_mode_and_surrogate(self):
        for edits,mode in (([],'snapshot'),([{'old':'alpha','new':'x'}],'magic'),([{'old':'alpha','new':'\ud800'}],'snapshot')):
            f=self.item(self.a,'alpha','x'); f['edits']=edits
            self.assertEqual(self.apply([f],mode)['status'],'refused')

    def test_refusal_second_publish_is_partial_and_stops(self):
        c=self.root/'c'; c.write_bytes(b'last')
        original=hands.publish
        def publish(p,*a,**kw):
            if p==self.b: return hands.outcome('refused','injected before write')
            return original(p,*a,**kw)
        with patch.object(hands,'publish',side_effect=publish):
            r=self.apply([self.item(self.a,'alpha','x'),self.item(self.b,'gamma','y'),self.item(c,'last','z')])
        self.assertEqual(r['status'],'partial')
        self.assertEqual([x['status'] for x in r['files']],['confirmed','refused','refused'])
        self.assertEqual(self.a.read_bytes(),b'x beta\r\n'); self.assertEqual(c.read_bytes(),b'last')

    def test_readback_failure_unknown_keeps_first_receipt(self):
        original=hands._digest_of
        def digest(p):
            if p==self.b: raise OSError('readback')
            return original(p)
        with patch.object(hands,'_digest_of',side_effect=digest):
            r=self.apply([self.item(self.a,'alpha','x'),self.item(self.b,'gamma','y')])
        self.assertEqual(r['status'],'in_doubt')
        self.assertEqual([x['status'] for x in r['files']],['confirmed','in_doubt'])
        self.assertEqual(self.b.read_bytes(),b'y delta\n')

    def test_unexpected_publisher_failure_retains_earlier_results(self):
        original=hands.publish
        def publish(p,*a,**kw):
            if p==self.b: raise ValueError('publisher failed after possible effect')
            return original(p,*a,**kw)
        with patch.object(hands,'publish',side_effect=publish):
            r=self.apply([self.item(self.a,'alpha','x'),self.item(self.b,'gamma','y')])
        self.assertEqual(r['status'],'in_doubt')
        self.assertEqual([x['status'] for x in r['files']],['confirmed','in_doubt'])

    def test_forge_batch_takes_shared_mutation_lock(self):
        from unittest.mock import Mock
        @contextlib.contextmanager
        def task_lock(*args): yield lambda *args: None
        with patch.object(forge,'_task_root',return_value=({'id':'test'},self.root,'')), patch.object(forge,'_remote_client',return_value=None), patch.object(forge,'_mutation_lock',task_lock), patch.object(forge,'_admission_reason',return_value=''), patch.object(forge,'get',return_value={}), patch.object(forge,'_event'), patch.object(workshop,'mutation_lock') as locked:
            locked.return_value.__enter__.return_value.lost_to_takeover=False
            reply=forge.edit('test','batch',files=[self.item(self.a,'alpha','x')])
            locked.assert_called_once()
        self.assertEqual(forge._edit_outcome(reply),'confirmed')

    def test_foreign_write_after_preflight_preserved(self):
        original=hands.publish
        def publish(p,*a,**kw):
            if p==self.b: p.write_bytes(b'foreign')
            return original(p,*a,**kw)
        with patch.object(hands,'publish',side_effect=publish):
            r=self.apply([self.item(self.a,'alpha','x'),self.item(self.b,'gamma','y')])
        self.assertEqual(r['status'],'partial'); self.assertEqual(self.b.read_bytes(),b'foreign')

    @unittest.skipUnless(os.name == 'posix', 'POSIX permission bits')
    def test_permission_mode_preserved(self):
        self.a.chmod(0o640)
        self.assertEqual(self.apply([self.item(self.a,'alpha','x')])['status'],'confirmed')
        self.assertEqual(self.a.stat().st_mode & 0o777,0o640)

    def test_workshop_real_lock_and_version(self):
        with patch.object(workshop,'BASE',self.root), patch.object(workshop,'_resolve_write',side_effect=lambda p,proposal: self.resolve(p)), patch('forge_roots.foreign_writer_reason',return_value=''):
            r=workshop.fs_edit_result(files=[self.item(self.a,'alpha','x')])
        self.assertEqual(r['status'],'confirmed')

    def test_workshop_checks_every_root(self):
        seen=[]
        def guard(*args,root):
            seen.append(root); return 'occupied' if len(seen)==2 else ''
        with patch.object(workshop,'_resolve_write',side_effect=lambda p,proposal: self.resolve(p)), patch('forge_roots.foreign_writer_reason',side_effect=guard):
            r=workshop.fs_edit_result(files=[self.item(self.a,'alpha','x'),self.item(self.b,'gamma','y')])
        self.assertEqual(r['status'],'refused'); self.assertEqual(len(seen),2)
        self.assertEqual(self.a.read_bytes(),b'alpha beta\r\n')

    def test_unlock_error_preserves_per_file_evidence(self):
        @contextlib.contextmanager
        def broken(*a,**kw):
            yield
            raise OSError('unlock')
        with patch.object(workshop,'BASE',self.root), patch.object(workshop,'_resolve_write',side_effect=lambda p,proposal:self.resolve(p)), patch('forge_roots.transaction',broken), patch('forge_roots.foreign_writer_reason',return_value=''):
            r=workshop.fs_edit_result(files=[self.item(self.a,'alpha','x')])
        self.assertEqual(r['status'],'in_doubt'); self.assertEqual(r['files'][0]['after_sha256'],sha(self.a.read_bytes()))

    def test_forge_batch_and_remote_refusal(self):
        with patch.object(forge,'_task_root',return_value=({'id':'test'},self.root,'')), patch.object(forge,'_remote_client',return_value=None),patch.object(forge,'_event'):
            reply=forge._edit_unlocked('test','batch',files=[self.item(self.a,'alpha','x')])
        self.assertEqual(forge._edit_outcome(reply),'confirmed')
        from unittest.mock import Mock
        remote=Mock()
        with patch.object(forge,'_task_root',return_value=({'id':'test'},self.root,'')),patch.object(forge,'_remote_client',return_value=remote):
            reply=forge._edit_unlocked('test','batch',files=[self.item(self.a,'x','y')])
        self.assertEqual(forge._edit_outcome(reply),'refused'); remote.workspace_edit.assert_not_called()

    def test_python_hands_exceptions_have_receipt(self):
        from pyhands import praxis_hands as h
        r=batch.result([dict(path='a',**hands.outcome('confirmed','yes')),dict(path='b',**hands.outcome('refused','no'))])
        with patch.object(workshop,'fs_edit_result',return_value=r):
            with self.assertRaises(h.Partial) as caught: h.edit(files=[])
        self.assertEqual(caught.exception.result,r)

    def test_schemas_and_k_explain_batch(self):
        ctx=agent.ChannelContext(chat_id='42',room_id='42',is_dm=True,owner=True,known=True)
        tools={t['name']:t for t in agent.catalog_tools_for(ctx) if t.get('name')}
        for t in (tools['edit'],forge_worker.EDIT_TOOL):
            self.assertIn('files',t['input_schema']['properties'])
            for word in ('snapshot','sequential','partial','rollback'): self.assertIn(word,t['description'])
        self.assertIn('partial',tool_report.OUTCOMES)
        for word in ('snapshot','sequential','partial','h.edit(files='):
            self.assertIn(word,agent._hands_contract_text())


if __name__=='__main__': unittest.main()
