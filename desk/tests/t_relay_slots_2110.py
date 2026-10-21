"""Slot management through the Python desktop command, using isolated credentials."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import python_relay
from localharness import relay_network


class SlotTests(unittest.TestCase):
    def test_proxy_modes_and_credentials_are_private_child_inputs(self):
        self.assertEqual(relay_network.environment({'proxy':{'mode':'direct'}}),{'RELAY_NETWORK_MODE':'direct','RELAY_PROXY_URL':''})
        value=relay_network.environment({'proxy':{'mode':'proxy','protocol':'socks5h','host':'::1','port':'1080','username':'a@b','password':'p:/?'}})
        self.assertEqual(value['RELAY_PROXY_URL'],'socks5h://a%40b:p%3A%2F%3F@[::1]:1080')
        for config in ({'mode':'proxy','host':'host/path','port':'1080'}, {'mode':'proxy','host':'host','port':0}, {'mode':'proxy','host':'host','port':1080,'password':'secret'}, {'mode':'invented'}):
            with self.assertRaises(ValueError):relay_network.environment({'proxy':config})

    def test_manual_proxy_check_uses_saved_relay_api_not_model_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=self.owner(Path(tmp))
            with patch.object(python_relay,'control',return_value={'ok':True,'generation_sent':False}) as request:
                result=python_relay.invoke(owner,'relay_network_check',{})
                self.assertFalse(result['generation_sent'])
                self.assertEqual(request.call_args.args[1:],('network_check',))

    def test_invalid_proxy_save_leaves_working_settings_unchanged(self):
        from localharness import config_cli
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);owner=self.owner(root);config=owner.current().config
            before=config.read_bytes()
            for host in ('host/path','[bad-ipv6]',']'):
                raw=json.dumps({'relay':{'proxy':{'mode':'proxy','host':host,'port':1080}}})
                with self.assertRaises(ValueError):config_cli.save(config,raw,None)
                self.assertEqual(config.read_bytes(),before)

    def owner(self,root):
        config=root/'helene.json';config.write_text(json.dumps({'relay':{'port':5011,'key':'fixture'}}))
        agent=SimpleNamespace(tree=root,config=config)
        return SimpleNamespace(current=lambda:agent,root=root,helpers=[])

    def credential(self,root,slot,legacy=False):
        path=root/('relay/local_auth/auth.json' if legacy else f'relay/local_auth/accounts/{slot}/auth.json')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'tokens':{'access_token':'fixture-'+slot}}))
        return path

    def test_both_slots_remain_visible_when_relay_unreachable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.owner(root);self.credential(root,'primary',True)
            with patch.object(python_relay,'control',side_effect=OSError('offline')):
                result=python_relay.invoke(owner,'relay_account',{})
            self.assertFalse(result['reachable'])
            self.assertEqual([(r['slot'],r['stored']) for r in result['slots']],[('primary',True),('secondary',False)])

    def test_primary_logout_removes_legacy_alias_but_preserves_secondary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.owner(root)
            primary=self.credential(root,'primary');legacy=self.credential(root,'primary',True);secondary=self.credential(root,'secondary')
            with patch.object(python_relay,'control',return_value={}) as control:
                result=python_relay.invoke(owner,'relay_logout',{'slot':'primary'})
            self.assertEqual(result['phase'],'applied')
            self.assertEqual(control.call_args.args[1], 'logout')
            self.assertEqual(control.call_args.kwargs, {'slot':'primary'})
            self.assertTrue(primary.exists());self.assertTrue(legacy.exists());self.assertTrue(secondary.exists())

    def test_secondary_logout_does_not_touch_primary_or_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.owner(root)
            primary=self.credential(root,'primary');legacy=self.credential(root,'primary',True);secondary=self.credential(root,'secondary')
            with patch.object(python_relay,'control',return_value={}) as control:
                python_relay.invoke(owner,'relay_logout',{'slot':'secondary'})
            self.assertEqual(control.call_args.kwargs, {'slot':'secondary'})
            self.assertTrue(primary.exists());self.assertTrue(legacy.exists());self.assertTrue(secondary.exists())

    def test_switch_waits_for_backend_receipt_and_uses_explicit_slot(self):
        with tempfile.TemporaryDirectory() as tmp:
            owner=self.owner(Path(tmp))
            with patch.object(python_relay,'control',return_value={'accounts':[{'slot':'secondary','active':True}]}) as request:
                result=python_relay.invoke(owner,'relay_switch',{'slot':'secondary'})
                self.assertEqual(result['active_slot'],'secondary')
                self.assertEqual(request.call_args.args[1:],('switch',))
                self.assertEqual(request.call_args.kwargs,{'slot':'secondary'})
            with patch.object(python_relay,'control',return_value={'accounts':[{'slot':'primary','active':True}]}):
                with self.assertRaisesRegex(ValueError,'did not confirm'):python_relay.invoke(owner,'relay_switch',{'slot':'secondary'})

    def test_invalid_slot_refuses_before_touching_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.owner(root);primary=self.credential(root,'primary')
            with self.assertRaises(ValueError):python_relay.invoke(owner,'relay_logout',{'slot':'../primary'})
            self.assertTrue(primary.exists())

    def test_live_selection_and_disk_login_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);owner=self.owner(root);self.credential(root,'primary');self.credential(root,'secondary')
            with patch.object(python_relay,'control',return_value={'port':5013,'accounts':[{'slot':'primary','active':False,'configured':True},{'slot':'secondary','active':True,'configured':True}]}):
                result=python_relay.invoke(owner,'relay_account',{})
            self.assertTrue(result['slots'][1]['serving']);self.assertFalse(result['slots'][0]['serving'])
            self.assertTrue(all(r['stored'] for r in result['slots']))


if __name__=='__main__':unittest.main()
