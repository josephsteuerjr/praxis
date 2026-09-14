import contextlib
import io
import json
import tempfile
import unittest

from keat_capture import CaptureLedger
from keat_control import main


class ControlTests(unittest.TestCase):
    def test_explicit_revoke_committed_and_idempotent_no_payload(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = CaptureLedger(root, 'test')
            ledger.issue(occurrence_id='e', key='k', kind='message',
                         payload='PRIVATE-SENTINEL', capture=dict(
                             issuer='i', policy_revision='p', grant='g',
                             audience=['owner'], transfer='none', presence_hidden=False))
            args = ['--directory', root, '--namespace', 'test', '--grant', 'g', '--revoke']
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(main(args), 0)
                self.assertEqual(main(args), 0)
            self.assertNotIn('PRIVATE-SENTINEL', out.getvalue())
            self.assertEqual([json.loads(line)['status'] for line in out.getvalue().splitlines()],
                             ['committed', 'committed'])
            with contextlib.redirect_stdout(io.StringIO()) as failure:
                self.assertEqual(main(args[:-3] + ['--grant', 'missing', '--revoke']), 1)
            self.assertEqual(json.loads(failure.getvalue())['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
