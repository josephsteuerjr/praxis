"""Late update cleanup remains visible without requiring old-folder access."""
from pathlib import Path
import json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness.cleanup_notice import status

class Notice(unittest.TestCase):
    def test_late_receipt_and_cleared_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'python-migration.json'
            self.assertEqual(status(root)['messages'],[])
            p.write_text(json.dumps({'state':'retirement_incomplete','cleanup_errors':['Protected old folder','Protected old folder']}))
            value=status(root);self.assertEqual(value['messages'],['Protected old folder'])
            self.assertTrue(value['id']);self.assertEqual(value['receipt'],str(p))
            p.write_text('{"state":"completed","cleanup_errors":[]}')
            self.assertEqual(status(root)['messages'],[])

    def test_incomplete_receipt_without_details_and_malformed_input(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);p=root/'python-migration.json'
            p.write_text('{"state":"retirement_incomplete"}')
            self.assertTrue(status(root)['messages'])
            for raw in ('[]','broken','x'*(1024*1024+1)):
                p.write_text(raw);self.assertEqual(status(root)['messages'],[])

if __name__=='__main__':unittest.main()
