import asyncio,json,unittest
from unittest.mock import patch,AsyncMock
from aiohttp.test_utils import TestClient,TestServer
import test_praxis_phone
from test_praxis_phone import app,TREE
from deskd import control,usage
import deskctl

ACTIVE={'ok':True,'available':True,'active_state':'active','sub_state':'running','pid':123}
QUOTAS={'providers':[{'name':'Codex · реле','status':'ok','windows':usage.normalize_codex({'rate_limit':{'secondary_window':{'used_percent':37,'limit_window_seconds':604800,'reset_at':2000000000}}})}]}

class PhoneControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app.TOKEN='synthetic-owner';app._DEVICES_CACHE={'stamp':None,'rows':[]}
        app._save_devices([{'id':'fixture','hash':app._key_hash('synthetic-phone')}])
        self.headers={'Authorization':'Bearer synthetic-phone'}
        self.client=TestClient(TestServer(app.build_app()));await self.client.start_server()
    async def asyncTearDown(self):await self.client.close()
    async def test_unauthenticated_and_foreign_origin_cannot_control_service(self):
        with patch.object(control,'deskctl_call') as call:
            for method,path,headers in [('GET','/api/xtts/service',{}),('POST','/api/xtts/service',{}),('POST','/api/xtts/service',{**self.headers,'Origin':'https://foreign.invalid'})]:
                r=await self.client.request(method,path,headers=headers,json={'action':'stop'} if method=='POST' else None)
                self.assertEqual(r.status,403)
            call.assert_not_called()
    async def test_paired_phone_gets_real_service_state_without_voice_import(self):
        with patch.object(control,'deskctl_call',return_value=ACTIVE) as call,patch.object(app.xtts_ui,'_get',side_effect=AssertionError('must work when voice is stopped')):
            r=await self.client.get('/api/xtts/service',headers=self.headers)
            self.assertEqual(r.status,200);self.assertEqual((await r.json())['active_state'],'active')
            self.assertEqual(r.headers['Cache-Control'],'private, no-store');call.assert_called_once_with('/services/xtts')
    async def test_phone_can_only_request_fixed_start_stop(self):
        with patch.object(control,'deskctl_call',return_value={'ok':True,'accepted':True,'service':ACTIVE}) as call:
            for body in ({'action':'restart'},{'action':'stop','unit':'ssh'},{'action':'start','command':'id'},{}):
                r=await self.client.post('/api/xtts/service',headers=self.headers,json=body);self.assertFalse((await r.json())['ok'])
            call.assert_not_called()
            r=await self.client.post('/api/xtts/service',headers=self.headers,json={'action':'start'});self.assertTrue((await r.json())['accepted'])
            call.assert_called_once_with('/services/xtts',method='POST',body={'action':'start'},timeout=45)
    async def test_allowances_use_existing_control_network_instead_of_ui_dns(self):
        with patch.object(control,'deskctl_where',return_value=('http://synthetic-control','synthetic-secret')),patch.object(control,'deskctl_call',return_value=QUOTAS) as call,patch.object(usage,'allowances',side_effect=AssertionError('wrong network')):
            r=await self.client.get('/api/allowances',headers=self.headers);v=await r.json()
            self.assertEqual(r.status,200);self.assertEqual(v['providers'][0]['windows'][0]['remaining_percent'],63)
            self.assertNotIn('synthetic-secret',json.dumps(v));call.assert_called_once_with('/allowances')

def show(active='active',pid=123):
    return 0,f'LoadState=loaded\nActiveState={active}\nSubState=running\nMainPID={pid}\nResult=success\nUnitFileState=enabled\n'

class HostControls(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patcher=patch.object(deskctl,'TOKEN','synthetic-control');self.patcher.start()
        self.client=TestClient(TestServer(deskctl.app()));await self.client.start_server()
        self.headers={'Authorization':'Bearer synthetic-control'}
    async def asyncTearDown(self):await self.client.close();self.patcher.stop()
    async def test_host_routes_require_the_existing_broker_token(self):
        with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock) as run:
            for path in ('/services/xtts','/allowances'):
                r=await self.client.get(path);self.assertEqual(r.status,403)
            r=await self.client.post('/services/xtts',json={'action':'stop'});self.assertEqual(r.status,403)
            run.assert_not_awaited()
    async def test_no_other_unit_command_or_extra_payload_can_be_forwarded(self):
        with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock) as run:
            for body in ({'action':'restart'},{'action':'start','unit':'ssh'},{'action':'stop; reboot'},[],None):
                r=await self.client.post('/services/xtts',headers=self.headers,json=body);self.assertEqual(r.status,400)
            r=await self.client.post('/services/ssh',headers=self.headers,json={'action':'stop'});self.assertEqual(r.status,404)
            run.assert_not_awaited()
    async def test_get_is_read_only_and_active_start_is_idempotent(self):
        with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock,return_value=show()) as run:
            r=await self.client.get('/services/xtts',headers=self.headers);self.assertEqual((await r.json())['pid'],123)
            r=await self.client.post('/services/xtts',headers=self.headers,json={'action':'start'});self.assertFalse((await r.json())['changed'])
            self.assertTrue(all(c.args[0]=='show' and c.args[1]==deskctl.XTTS_UNIT for c in run.await_args_list))
    async def test_stop_and_start_use_no_block_fixed_unit_and_verify_state(self):
        for action,before,after,pid in [('stop','active','inactive',0),('start','inactive','activating',234)]:
            with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock,side_effect=[show(before), (0,''), show(after,pid)]) as run:
                r=await self.client.post('/services/xtts',headers=self.headers,json={'action':action});v=await r.json()
                self.assertTrue(v['accepted']);self.assertTrue(v['changed']);self.assertEqual(v['service']['active_state'],after)
                self.assertEqual(run.await_args_list[1].args,('--no-block',action,deskctl.XTTS_UNIT))
    async def test_unknown_state_and_host_failure_are_not_reported_as_success(self):
        with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock,side_effect=asyncio.TimeoutError):
            r=await self.client.post('/services/xtts',headers=self.headers,json={'action':'stop'});self.assertFalse((await r.json())['ok'])
        with patch.object(deskctl,'_xtts_systemctl',new_callable=AsyncMock,side_effect=[show(),(1,''),show()]) as run:
            r=await self.client.post('/services/xtts',headers=self.headers,json={'action':'stop'});self.assertFalse((await r.json())['ok'])
            self.assertEqual(sum(c.args[0]=='--no-block' for c in run.await_args_list),1)
    async def test_broker_returns_normalized_allowances_without_provider_secrets(self):
        with patch.object(usage,'allowances',return_value=QUOTAS) as read:
            r=await self.client.get('/allowances',headers=self.headers);self.assertEqual(await r.json(),QUOTAS);read.assert_called_once_with(deskctl.TREE)

if __name__=='__main__':unittest.main()
