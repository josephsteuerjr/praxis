"""Actual common HTTP/WS route dispatch for owner and paired-device scopes."""
import asyncio,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest import mock
DESK=Path(__file__).resolve().parents[1];sys.path[:0]=[str(DESK),str(DESK/'localharness')]
import deskapp
from deskd import autonomy
import deskpkg

class Channel(unittest.IsolatedAsyncioTestCase):
    async def test_shared_controls_are_in_server_flavor_without_localharness(self):
        parts=deskpkg.parts(deskpkg.SERVER)
        self.assertTrue(any(p.src=='deskd' for p in parts))
        self.assertFalse(any(p.src=='localharness' for p in parts))
        self.assertEqual(Path(autonomy.__file__).parent.name,'deskd')
    async def test_owner_device_and_invalid_commands_use_same_selected_agent_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);reader=root/'memory/.control/desk_inbox/.reader.json';reader.parent.mkdir(parents=True)
            reader.write_text(json.dumps({'pid':os.getpid(),'at':time.time(),'started':'test-boot','autonomy':{'protocol':1,'stopped':False}}))
            with mock.patch.object(deskapp.readers,'tree',return_value=root):
                for role in ('owner','device'):
                    state=await deskapp._tunnel_dispatch('GET','/api/autonomy',None,role=role)
                    self.assertEqual(state['status'],200);self.assertTrue(state['body']['available'])
                    queued=await deskapp._tunnel_dispatch('POST','/api/autonomy',{'action':'pause'},role=role)
                    self.assertEqual(queued['status'],200);self.assertTrue(queued['body']['ok']);self.assertFalse(autonomy.state(root)['paused'])
                invalid=await deskapp._tunnel_dispatch('POST','/api/autonomy',[],role='device');self.assertEqual(invalid['status'],400)
                refused=await deskapp._tunnel_dispatch('POST','/api/autonomy',{'action':'panic'},role='owner');self.assertFalse(refused['body']['ok'])
                forbidden=await deskapp._tunnel_dispatch('GET','/api/anatomy',None,role='device');self.assertEqual(forbidden['status'],403)
                (root/'memory/.control/autonomy-paused.json').write_text('{}')
                with mock.patch.object(deskapp.readers,'state',return_value={'level':'ok','phrase':'ready','runner':{'alive':True}}):
                    view=await deskapp._tunnel_dispatch('GET','/api/state',None,role='device')
                self.assertTrue(view['body']['autonomy']['paused']);self.assertEqual(view['body']['phrase'],'На связи · агент на паузе')

if __name__=='__main__':unittest.main()
