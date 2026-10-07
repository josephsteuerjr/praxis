"""Single-process identity probes: no child creation and no signals on Windows."""
import os
import unittest
from unittest import mock
import process_liveness as p


class ProcessBirthIdentity(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows kernel FILETIME probe')
    def test_own_kernel_identity(self):
        born = p.process_started_at(os.getpid())
        self.assertTrue(born.startswith('win:'))
        self.assertGreater(int(born[4:]), 0)
        self.assertEqual(p.identify(os.getpid(), born).verdict, p.SAME)
        self.assertEqual(p.identify(os.getpid(), 'win:1').verdict, p.OTHER)
        self.assertEqual(p.identify(os.getpid(), '').verdict, p.UNPROVEN)

    def test_unavailable_birth_cannot_authorize_signal(self):
        with mock.patch.object(p, '_number_is_taken', return_value=True), \
             mock.patch.object(p, 'process_started_at', return_value=''):
            result = p.identify(123, 'old-birth')
            self.assertEqual(result.verdict, p.UNPROVEN)
            self.assertTrue(result.alive)
            self.assertFalse(result.safe_to_signal)


if __name__ == '__main__':
    unittest.main()
