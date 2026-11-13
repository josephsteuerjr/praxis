"""Legacy cwd-only local process recovery must preserve the original subject."""
import json
import unittest
import test_forge_recovery_0311 as fixtures
import forge
import forge_recovery


class LegacyProcessRecovery(unittest.TestCase):
    put = fixtures.RebootRecovery.put
    recover = fixtures.RebootRecovery.recover

    def setUp(self):
        fixtures.RebootRecovery.setUp(self)
        self.kind='processes';self.uid='proc-old'
        self.directory=forge._unit_dir(self.tid,self.kind,self.uid);self.directory.mkdir(parents=True)
        self.request={k:v for k,v in self.request.items() if k!='root'}
        self.request.update(id=self.uid,cwd=str(self.root))
        self.put('request.json',self.request)
        self.event={'at':self.request['created'],'kind':'process_started','process_id':self.uid}
        self.events.write_text(json.dumps(self.event)+'\n')

    # These tests exercise process-specific legacy format without inheriting agent fixtures.
    def test_cwd_only_process_receipt_releases_without_rewriting(self):
        before=(self.directory/'request.json').read_bytes()
        self.assertIn(self.uid,forge._shared_writer_reason(self.tid,''))
        self.recover()
        self.assertEqual(before,(self.directory/'request.json').read_bytes())
        self.assertEqual(forge._unit_state(self.directory)['status'],'lost')
        self.assertTrue(forge._unit_state(self.directory)['custody_recovered'])
        # The original agent fixture still owns its root; only this process was recovered.
        self.assertNotIn(self.uid,forge._shared_writer_reason(self.tid,''))

    def test_unbound_or_remote_process_refused(self):
        for change in ({'cwd':None},{'cwd':str(self.root/'child')},{'remote':True},{'root':'different'}):
            self.put('request.json',{**self.request,**change})
            with self.assertRaises(ValueError): self.recover()
            self.assertFalse((self.directory/forge_recovery.FILE).exists())

    def test_post_reboot_process_event_refused(self):
        row={**self.event,'at':'2999-01-01T00:00:00+00:00'}
        with self.events.open('a') as f:f.write(json.dumps(row)+'\n')
        with self.assertRaisesRegex(ValueError,'after the reboot'):self.recover()


