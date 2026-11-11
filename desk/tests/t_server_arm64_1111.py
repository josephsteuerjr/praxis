"""Server target admission, asset selection and native relay architecture."""
from pathlib import Path
import importlib.util,json,sys,tempfile,unittest
from unittest.mock import patch
DESK=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(DESK),str(DESK/'installer'),str(DESK/'server/updater')]
import updater as up
from localharness import python_platform
from build_server_bundle import relay_bytes

class ArmServer(unittest.TestCase):
    def release(self,names):
        return {'tag_name':'v2.5.4','assets':[{'name':n,'digest':'sha256:'+'a'*64,'browser_download_url':'https://example.test/'+n} for n in names]}

    def test_selects_native_server_before_legacy(self):
        rel=self.release(['Helene-2.5.4.zip','Helene-2.5.4-server-linux-arm64.zip','Helene-2.5.4-server-linux-x86_64.zip'])
        for machine,arch in [('aarch64','arm64'),('ARM64','arm64'),('AMD64','x86_64')]:
            row=up.pick_release(rel,'Helene',machine=machine)
            self.assertEqual(row['asset'],f'Helene-2.5.4-server-linux-{arch}.zip')
            self.assertEqual(row['sha256'],'a'*64)

    def test_arm_never_falls_back_to_x64_archive(self):
        rel=self.release(['Helene-2.5.4.zip','Helene-2.5.4-server-linux-x86_64.zip'])
        with self.assertRaises(up.UpdateError):up.pick_release(rel,'Helene',machine='aarch64')
        self.assertEqual(up.pick_release(self.release(['Helene-2.5.4.zip']),'Helene',machine='x86_64')['asset'],'Helene-2.5.4.zip')

    def test_server_component_runtime_identity(self):
        with patch.object(python_platform.sys,'platform','linux'),patch.object(python_platform,'_machine',return_value='aarch64'):
            self.assertEqual(python_platform.runtime_target(),'linux-arm64')

    def test_relay_machine_must_match_artifact(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'relay';raw=bytearray(32);raw[:6]=b'\x7fELF\x02\x01';raw[18:20]=(183).to_bytes(2,'little');p.write_bytes(raw)
            self.assertEqual(relay_bytes(p,'arm64'),raw)
            with self.assertRaises(ValueError):relay_bytes(p,'x86_64')

if __name__=='__main__':unittest.main()
