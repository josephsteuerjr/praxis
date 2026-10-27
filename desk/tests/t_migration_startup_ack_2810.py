"""A rendered migration may retry readiness, without accepting an unproved owner."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import python_app


class StartupAck(unittest.TestCase):
    def fixture(self, root):
        (root/'profile').mkdir();(root/'code').mkdir()
        (root/'helene.json').write_text(json.dumps({'tree':'profile','code':'code','mode':'local'}))
        (root/'python-migration.json').write_text(json.dumps({'id':'fixture','state':'prepared',
            'new_root':str(root),'data_paths':[str(root/'profile')],
            'source_paths':[{'selected':str(root/'code')}]}))
        app=python_app.Application(root,root);app.migration_id='fixture'
        self.addCleanup(app.server.server_close)
        return app

    def test_temporary_failure_is_pending_then_real_proof_confirms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=self.fixture(root)
            proof={'tree':str(root/'profile'),'channel_pid':123}
            with patch('localharness.owner_stop.stopped',return_value=False), \
                 patch.object(python_app,'migration_proof',side_effect=[TimeoutError('Runner heartbeat is starting'),proof]):
                with self.assertRaises(TimeoutError):app.confirm_ui()
                self.assertFalse(app.confirmed_ui)
                self.assertEqual(json.loads((root/'python-migration.json').read_text())['state'],'prepared')
                self.assertIn('heartbeat',json.loads((root/'migration-ready.json').read_text())['error'])
                self.assertTrue(app.confirm_ui()['confirmed'])
                self.assertEqual(json.loads((root/'python-migration.json').read_text())['state'],'new_app_started')
                self.assertEqual(json.loads((root/'migration-ready.json').read_text())['state'],'confirmed')

    def test_failed_transaction_cannot_be_confirmed_by_a_late_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);app=self.fixture(root);p=root/'python-migration.json'
            note=json.loads(p.read_text());note['state']='failed_before_retirement';p.write_text(json.dumps(note))
            with patch.object(python_app,'migration_proof') as readiness:
                result=app.confirm_ui();self.assertFalse(result['confirmed']);self.assertTrue(result['terminal'])
                readiness.assert_not_called()


if __name__=='__main__':unittest.main()
