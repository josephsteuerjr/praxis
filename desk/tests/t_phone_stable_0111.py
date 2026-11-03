"""A permanent origin is validated; programme moves keep its local ingress."""
import asyncio,copy,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
import phone_access
from localharness import first_start


class Stable(unittest.TestCase):
    def test_only_https_origin_without_embedded_credentials_is_advertised(self):
        config={'phone':{'enabled':True,'mode':'stable','external':'https://stable.example/'}}
        self.assertTrue(phone_access.enabled(config));self.assertEqual(phone_access.stable_origin(config),'https://stable.example/')
        for value in ('http://stable.example','https://u:p@stable.example','https://stable.example/m/','https://stable.example/?key=x','https://stable.example/#device=x'):
            config['phone']['external']=value
            with self.subTest(value=value),self.assertRaises(ValueError):phone_access.stable_origin(config)

    def test_code_folder_change_keeps_profile_port_for_permanent_address(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp);old=base/'old';new=base/'new';data=base/'profile/data'
            for folder in (old,new,data):folder.mkdir(parents=True)
            config={'port':31234,'phone':{'mode':'stable','local_port':31234,'external':'https://stable.example'}}
            result=first_start._bind(config,old,new,data)
            self.assertEqual(result['port'],config['phone']['local_port'])
            self.assertEqual(result['phone'],config['phone'])
            self.assertEqual(result['tree'],str(data));self.assertEqual(result['code'],str(new/'tree'))
            config['phone']['mode']='automatic'
            automatic=first_start._bind(config,old,new,data)
            self.assertEqual(automatic['port'],config['phone']['local_port'],'existing fixed ingress survives while automatic tunnel is selected too')

class StableCycle(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_failure_and_wrong_instance_are_not_certificate_errors(self):
        class Response:
            status=502
            async def json(self):return {'instance':'different'}
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Client:
            def get(self,url):return Response()
        ok,reason=await phone_access.probe_origin(Client(),'https://fixture.invalid/','expected')
        self.assertFalse(ok);self.assertIn('HTTP 502',reason);self.assertNotIn('сертификат',reason)
        Response.status=200
        ok,reason=await phone_access.probe_origin(Client(),'https://fixture.invalid/','expected')
        self.assertFalse(ok);self.assertIn('другой копии',reason)

    async def test_configured_origin_uses_no_quick_tunnel_or_download(self):
        with tempfile.TemporaryDirectory() as temp:
            config={'phone':{'enabled':True,'mode':'stable','external':'https://stable.example'}}
            runtime=phone_access.PhoneAccess(Path(temp),lambda:copy.deepcopy(config),31234,owner_token=True)
            responses=[]
            class Response:
                status=200
                async def json(self):return {'instance':runtime.instance}
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
            class Client:
                async def __aenter__(self):return self
                async def __aexit__(self,*args):pass
                def get(self,url):responses.append(url);return Response()
            async def done(_):config['phone']['enabled']=False
            runtime.delay=done
            with patch('aiohttp.ClientSession',return_value=Client()),patch.object(phone_access,'ensure_cloudflared',new_callable=AsyncMock) as download,patch.object(phone_access,'QuickTunnel') as quick:
                await runtime.cycle(copy.deepcopy(config))
            download.assert_not_awaited();quick.assert_not_called()
            self.assertEqual(responses,['https://stable.example/api/phone/probe'])


if __name__=='__main__':unittest.main()
