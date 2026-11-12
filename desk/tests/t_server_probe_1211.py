import sys,tempfile,types,unittest
import json,os
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import server_probe as probe


class Probe(unittest.TestCase):
    def test_unsaved_host_permit_is_exact_route_host_method_and_short_lived(self):
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory)
            with probe.issue(tree,'fixture.example.org') as headers:
                request=types.SimpleNamespace(method='GET',path='/api/phone/probe',headers=headers)
                self.assertTrue(probe.permitted(tree,request,'fixture.example.org'))
                self.assertFalse(probe.permitted(tree,request,'other.example.org'))
                for path in ('/api/state','/tg/','/api/program/invoke'):
                    self.assertFalse(probe.permitted(tree,types.SimpleNamespace(method='GET',path=path,headers=headers),'fixture.example.org'))
                self.assertFalse(probe.permitted(tree,types.SimpleNamespace(method='POST',path='/api/phone/probe',headers=headers),'fixture.example.org'))
                self.assertFalse(probe.permitted(tree,types.SimpleNamespace(method='GET',path='/api/phone/probe',headers={**headers,'Origin':'https://fixture.example.org'}),'fixture.example.org'))
                with mock.patch.object(probe.time,'time',return_value=probe.time.time()+60):
                    self.assertFalse(probe.permitted(tree,request,'fixture.example.org'))
            self.assertFalse(probe.permitted(tree,request,'fixture.example.org'))
            self.assertEqual(list((tree/'memory/.state/server-probes').iterdir()),[])


class ActualChannel(unittest.IsolatedAsyncioTestCase):
    async def test_unsaved_host_probe_is_narrow_over_actual_http(self):
        import deskapp
        from aiohttp.test_utils import TestClient,TestServer
        with tempfile.TemporaryDirectory() as directory:
            tree=Path(directory);config=tree/'helene.json'
            config.write_text(json.dumps({'phone':{'enabled':False,'mode':'automatic'}}))
            saved=(deskapp.TOKEN,deskapp._PHONE_ACCESS);deskapp.TOKEN='fixture-owner'
            try:
                with mock.patch.dict(os.environ,{'HELENE_CONFIG':str(config),'PRAXIS_DESK_TREE':directory}):
                    client=TestClient(TestServer(deskapp.build_app(port=8193)))
                    await client.start_server()
                    try:
                        with probe.issue(tree,'new.fixture.example.org') as token:
                            headers={**token,'Host':'new.fixture.example.org'}
                            result=await client.get('/api/phone/probe',headers=headers)
                            self.assertEqual(result.status,200);self.assertTrue((await result.json())['instance'])
                            for route in ('/api/state','/tg/','/api/program/invoke'):
                                result=await client.get(route,headers=headers);self.assertEqual(result.status,403,route)
                            result=await client.get('/api/phone/probe',headers={**headers,'Origin':'https://new.fixture.example.org'})
                            self.assertEqual(result.status,403)
                        result=await client.get('/api/phone/probe',headers=headers);self.assertEqual(result.status,403)
                    finally:await client.close()
            finally:deskapp.TOKEN,deskapp._PHONE_ACCESS=saved


if __name__=='__main__':unittest.main()
