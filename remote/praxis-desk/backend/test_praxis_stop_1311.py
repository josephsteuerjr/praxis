import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer
import test_praxis_phone
from test_praxis_phone import app
import deskctl
from deskd import control, praxis_control


def container(running):
    return 200,json.dumps({'State':{'Running':running,'Status':'running' if running else 'exited'}}).encode()


class ProcessControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.tree=Path(self.tmp.name)
        self.patches=[patch.object(deskctl,'TOKEN','fixture-control'),patch.object(deskctl,'CONTAINERS',('praxis',)),patch.object(deskctl,'TREE',self.tree)]
        for p in self.patches:p.start();self.addCleanup(p.stop)
        self.client=TestClient(TestServer(deskctl.app()));await self.client.start_server()
        self.addAsyncCleanup(self.client.close)
        self.headers={'Authorization':'Bearer fixture-control'}

    async def test_authentication_and_fixed_target_before_any_process_effect(self):
        with patch.object(deskctl,'_docker',new_callable=AsyncMock) as docker:
            r=await self.client.post('/praxis/control',json={'action':'stop'});self.assertEqual(r.status,403)
            for value in ({'action':'stop','container':'relay'},{'action':'reboot'},[],{'action':'cancel','run_id':'../owner'}):
                r=await self.client.post('/praxis/control',headers=self.headers,json=value)
                self.assertFalse((await r.json())['ok'])
            docker.assert_not_awaited()
            self.assertFalse((self.tree/'memory/.panic').exists())

    async def test_stop_is_persistent_precedes_kill_and_requires_observed_exit(self):
        calls=[]
        async def docker(path,**kw):
            calls.append(path)
            if '/stop' in path:
                self.assertTrue((self.tree/'memory/.panic').exists())
                return 204,b''
            return container(len(calls)==1)
        with patch.object(deskctl,'_docker',side_effect=docker):
            r=await self.client.post('/praxis/control',headers=self.headers,json={'action':'stop'});v=await r.json()
        self.assertTrue(v['ok']);self.assertTrue(v['stopped']);self.assertEqual(calls[1],'/containers/praxis/stop?t=0')

    async def test_failed_stop_does_not_claim_stopped_and_retains_persistent_intent(self):
        with patch.object(deskctl,'_docker',new_callable=AsyncMock,side_effect=[container(True),(500,b''),container(True)]):
            r=await self.client.post('/praxis/control',headers=self.headers,json={'action':'stop'});v=await r.json()
        self.assertFalse(v['ok']);self.assertFalse(v['stopped']);self.assertTrue((self.tree/'memory/.panic').exists())

    async def test_start_clears_stop_and_is_confirmed_independently(self):
        praxis_control.write(self.tree/'memory/.panic',{'fixture':True})
        with patch.object(deskctl,'_docker',new_callable=AsyncMock,side_effect=[container(False),(204,b''),container(True)]):
            r=await self.client.post('/praxis/control',headers=self.headers,json={'action':'start'});v=await r.json()
        self.assertTrue(v['ok']);self.assertFalse((self.tree/'memory/.panic').exists())

    async def test_cancel_is_durable_and_unknown_effects_are_never_erased(self):
        rid='run-fixture';directory=self.tree/'memory/runs/2026'/rid
        directory.mkdir(parents=True);manifest=directory/'manifest.json';raw=json.dumps({'status':'in_doubt','outstanding_call_ids':['unknown']});manifest.write_text(raw)
        async def acknowledge():
            path=self.tree/'memory/.control/run-cancel'/f'{rid}.json'
            while not path.exists():await asyncio.sleep(.01)
            request=praxis_control.read(path)
            praxis_control.write(path.with_suffix('.receipt'),{'request_id':request['id'],'run_id':rid,'ok':True,'processes':[{'pid':123,'stopped':True}]})
        task=asyncio.create_task(acknowledge())
        r=await self.client.post('/praxis/control',headers=self.headers,json={'action':'cancel','run_id':rid});v=await r.json();await task
        self.assertTrue(v['confirmed']);self.assertEqual(manifest.read_text(),raw)
        self.assertTrue((self.tree/'memory/.control/run-cancel'/f'{rid}.json').exists())

    async def test_terminal_status_is_not_a_substitute_for_process_death_receipt(self):
        rid='run-terminal';directory=self.tree/'memory/runs/2026'/rid
        directory.mkdir(parents=True);(directory/'manifest.json').write_text(json.dumps({'status':'cancelled'}))
        task=asyncio.create_task(praxis_control.cancel_run(self.tree,rid))
        await asyncio.sleep(.08)
        self.assertFalse(task.done())
        path=self.tree/'memory/.control/run-cancel'/f'{rid}.json';request=praxis_control.read(path)
        praxis_control.write(path.with_suffix('.receipt'),{'request_id':request['id'],'ok':True,'processes':[{'pid':123,'stopped':True}]})
        self.assertTrue((await task)['confirmed'])


class PhoneAccess(unittest.IsolatedAsyncioTestCase):
    async def test_paired_phone_routes_share_owner_controls_and_do_not_cache(self):
        app.TOKEN='synthetic-owner';app._DEVICES_CACHE={'stamp':None,'rows':[]}
        app._save_devices([{'id':'fixture','hash':app._key_hash('synthetic-phone')}])
        client=TestClient(TestServer(app.build_app()));await client.start_server()
        try:
            with patch.object(control,'praxis_control',return_value={'ok':True}) as call:
                r=await client.post('/api/praxis/control',json={'action':'stop'});self.assertEqual(r.status,403);call.assert_not_called()
                headers={'Authorization':'Bearer synthetic-phone'}
                r=await client.get('/api/praxis/control',headers=headers);self.assertEqual(r.status,200);self.assertEqual(r.headers['Cache-Control'],'private, no-store')
                r=await client.post('/api/run/run-fixture/cancel',headers=headers,json={});self.assertEqual(r.status,200)
                call.assert_called_with({'action':'cancel','run_id':'run-fixture'})
        finally:await client.close()


if __name__=='__main__':unittest.main()
