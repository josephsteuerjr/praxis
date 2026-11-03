"""Real enrollment/session persistence, isolated from owner data and network."""
import asyncio,json,sys,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'localharness')]
import deskapp
from aiohttp import web


class Continuity(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.tree=Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(deskapp.readers,'tree',return_value=self.tree).start()
        patch.object(deskapp,'_agent_name',return_value='Fixture agent').start()
        patch.dict(deskapp.os.environ,{'HELENE_DESK_STATE':str(self.tree/'channel')}).start()
        deskapp._PAIRS.clear();deskapp._PAIR_CACHE.update(path=None,stamp=None)
        deskapp._DEVICES_CACHE.update(stamp=None,rows=[])

    def restart(self):
        deskapp._PAIRS.clear();deskapp._PAIR_CACHE.update(path=None,stamp=None)
        deskapp._DEVICES_CACHE.update(stamp=None,rows=[])

    def pair(self):
        pair=deskapp._new_pair();got=deskapp._redeem(pair['token'],'iPhone','127.0.0.1')
        return pair,got['key']

    def manifest(self,key='',pair=''):
        request=SimpleNamespace(query={'pair':pair},cookies={deskapp.COOKIE:key})
        return json.loads(asyncio.run(deskapp.mobile_manifest(request)).text)

    def test_unfinished_pair_survives_channel_restart_without_extending_expiry(self):
        pair=deskapp._new_pair();expiry=deskapp._PAIRS[pair['token']]['expires']
        self.restart();got=deskapp._redeem(pair['token'],'iPhone','127.0.0.1')
        self.assertTrue(deskapp._device_ok(got['key']))
        self.assertEqual(deskapp._PAIRS[pair['token']]['expires'],expiry)
        self.assertEqual(got['uses_left'],pair['uses']-1)

    def test_same_device_keeps_key_and_single_row_across_restart(self):
        pair,key=self.pair();self.restart()
        self.assertEqual(deskapp._redeem(pair['token'],'iPhone','127.0.0.1')['key'],key)
        self.assertEqual(len(deskapp._devices()),1)

    def test_revocation_also_blocks_reusing_pending_code_and_manifest_bootstrap(self):
        pair,key=self.pair();deskapp._revoke_device(deskapp._device_rows()[0]['id'])
        self.restart()
        with self.assertRaises(web.HTTPForbidden):deskapp._redeem(pair['token'],'iPhone','127.0.0.1')
        self.assertEqual(self.manifest(key)['start_url'],'/m/')
        self.assertEqual(deskapp._devices(),[])

    def test_expired_enrollment_stays_expired_after_restart(self):
        pair=deskapp._new_pair();deskapp._PAIRS[pair['token']]['expires']=time.time()-1
        deskapp._save_pairs();self.restart()
        with self.assertRaises(web.HTTPForbidden) as caught:deskapp._redeem(pair['token'],'iPhone','127.0.0.1')
        self.assertEqual(json.loads(caught.exception.text)['code'],'pair_expired')

    def test_failed_ledger_write_does_not_leave_a_device_or_consume_enrollment(self):
        pair=deskapp._new_pair()
        with patch.object(deskapp,'_save_pairs',side_effect=OSError('read-only fixture')):
            with self.assertRaises(OSError):deskapp._redeem(pair['token'],'iPhone','127.0.0.1')
        self.assertEqual(deskapp._devices(),[])
        self.restart()
        self.assertEqual(deskapp._pairs()[pair['token']]['uses'],pair['uses'])
        self.assertNotIn('key',deskapp._pairs()[pair['token']])

    def test_manifest_identity_and_authorized_install_session_survive_shell_update(self):
        pair,key=self.pair();self.restart()
        first=self.manifest(key,pair['token'])
        with patch.object(deskapp,'_agent_name',return_value='Renamed agent'):
            second=self.manifest(key)
        self.assertEqual(first['id'],second['id'])
        self.assertEqual(first['start_url'],second['start_url'])
        self.assertIn('#device='+key,first['start_url']);self.assertNotIn('?pair=',first['start_url'])
        self.assertEqual(self.manifest()['start_url'],'/m/')

    def test_existing_device_cookie_refresh_never_creates_or_redeems_a_pair(self):
        pair,key=self.pair();uses=deskapp._PAIRS[pair['token']]['uses']
        request=SimpleNamespace(query={},headers={'Authorization':'Bearer '+key},transport=None,scheme='https')
        response=asyncio.run(deskapp.api_pair_redeem(request))
        self.assertEqual(json.loads(response.text)['key'],key)
        self.assertTrue(response.cookies[deskapp.COOKIE]['httponly'])
        self.assertTrue(response.cookies[deskapp.COOKIE]['secure'])
        self.assertEqual(deskapp._PAIRS[pair['token']]['uses'],uses)
        self.assertEqual(len(deskapp._devices()),1)


class DeviceScope(unittest.TestCase):
    def test_missing_or_other_profile_never_inherits_cached_device_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with patch.dict(deskapp.os.environ,{'HELENE_DESK_STATE':str(root/'one')}):
                pair=deskapp._new_pair();key=deskapp._redeem(pair['token'],'fixture','127.0.0.1')['key']
                self.assertTrue(deskapp._device_ok(key))
            with patch.dict(deskapp.os.environ,{'HELENE_DESK_STATE':str(root/'two')}):
                self.assertFalse(deskapp._device_ok(key));self.assertEqual(deskapp._devices(),[])
            with patch.dict(deskapp.os.environ,{'HELENE_DESK_STATE':str(root/'one')}):
                self.assertTrue(deskapp._device_ok(key))
                deskapp._devices_path().unlink()
                self.assertFalse(deskapp._device_ok(key))

if __name__=='__main__':unittest.main()
