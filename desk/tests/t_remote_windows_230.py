import sys,unittest,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'helene/core'))
import forge
from test_forge_remote_admission_2710 import RemoteAdmission
class WindowsRemote(unittest.TestCase):
    setUp=RemoteAdmission.setUp
    patch=RemoteAdmission.patch
    host=RemoteAdmission.host
    def test_windows_body_start_and_stop_use_operation_identity(self):
        remote=self.host();self.task['scope']='windows';forge._save_task(self.task)
        from contextlib import nullcontext
        self.patch('_remote_observations',lambda _:nullcontext())
        remote.process_start.return_value={'ok':True,'operation_id':'windows-owned-7'}
        remote.process_status.return_value={'ok':True,'status':'succeeded','terminal':{'status':'succeeded','exit_code':0}}
        value=json.loads(forge.run(self.tid,'Write-Output test',timeout=5))
        self.assertEqual(value['remote_id'],'windows-owned-7')
        remote.process_status.assert_called_with('windows-owned-7',30000)
        self.assertEqual(forge._units(self.tid,'processes')[0]['status'],'done')
        self.assertEqual(forge.control(self.tid,'stop')['status'],'settled')
        remote.call.assert_not_called()


if __name__=='__main__':unittest.main()
