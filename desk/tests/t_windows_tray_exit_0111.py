"""Real WinForms teardown; the tested popup must not cancel Application.Exit."""
from pathlib import Path
import json,subprocess,sys,tempfile,unittest


@unittest.skipUnless(sys.platform=='win32','Actual WinForms application-exit contract')
class WindowsTrayExit(unittest.TestCase):
    def test_hidden_popup_allows_application_exit_and_user_dismissal_stays_reusable(self):
        here=Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix='native-tray-',dir=here) as temporary:
            receipt=Path(temporary)/'receipt.json';log=Path(temporary)/'native.log'
            with log.open('wb') as output:
                done=subprocess.run([sys.executable,'-B',str(here/'_native_tray_probe.py'),
                    str(here.parent/'python_tray.py'),str(receipt)],stdin=subprocess.DEVNULL,
                    stdout=output,stderr=subprocess.STDOUT,timeout=20)
            self.assertEqual(done.returncode,0,log.read_text(errors='replace'))
            result=json.loads(receipt.read_text())
            self.assertTrue(result['passed'])
            self.assertFalse(result['watchdog'])
            self.assertIn({'reason':'ApplicationExitCall','cancel':False},result['closing_events'])


if __name__=='__main__':unittest.main()
