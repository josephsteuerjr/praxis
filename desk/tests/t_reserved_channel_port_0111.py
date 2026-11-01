"""Actual Windows excluded ports and exclusive rival listeners; no live profile."""
import json,os,socket,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from localharness import channel_port


@unittest.skipUnless(sys.platform=='win32','Windows TCP exclusion contract')
class ReservedChannelPort(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='channel-port-',dir=Path(__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        self.config=Path(self.temporary.name)/'helene.json'

    def write(self,port):
        value={'mode':'local','port':port,'agent':{'name':'fixture'},'relay':{'port':5013,'key':'synthetic-port-fixture'}}
        self.config.write_text(json.dumps(value),encoding='utf-8');return value

    def test_real_reserved_slot_is_replaced_and_other_settings_are_exact(self):
        forbidden=None
        for first,last in channel_port.excluded_ranges():
            for port in (first,last):
                if channel_port.endpoint_exists(port):continue
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                    try:probe.bind(('127.0.0.1',port))
                    except OSError as error:
                        if error.winerror==10013:forbidden=port;break
            if forbidden is not None:break
        if forbidden is None:self.skipTest('OS currently has no inaccessible, unowned exclusion')
        before=self.write(forbidden);changed=channel_port.ensure_available(self.config)
        after=json.loads(self.config.read_text());self.assertEqual(after['port'],changed['port'])
        self.assertNotEqual(after['port'],forbidden);after['port']=forbidden;self.assertEqual(after,before)
        with socket.socket() as probe:probe.bind(('127.0.0.1',changed['port']))

    def test_rival_exclusive_listener_is_never_reassigned(self):
        with socket.socket() as rival:
            rival.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
            rival.bind(('127.0.0.1',0));rival.listen()
            before=self.write(rival.getsockname()[1]);raw=self.config.read_bytes()
            self.assertIsNone(channel_port.ensure_available(self.config))
            self.assertEqual(self.config.read_bytes(),raw)
            self.assertTrue(channel_port.endpoint_exists(before['port']))

    def test_free_port_does_not_rewrite_configuration(self):
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        self.write(port);before=self.config.read_bytes()
        self.assertIsNone(channel_port.ensure_available(self.config));self.assertEqual(self.config.read_bytes(),before)

    def test_automatic_phone_port_is_reconciled_with_an_available_channel(self):
        with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
        before=self.write(port);before['phone']={'mode':'automatic','local_port':1,'external':'https://synthetic-automatic.invalid'}
        self.config.write_text(json.dumps(before),encoding='utf-8')
        changed=channel_port.ensure_available(self.config);after=json.loads(self.config.read_text())
        self.assertEqual(changed['reason'],'automatic_phone_port')
        self.assertEqual(after['phone']['local_port'],port);self.assertEqual(after['port'],port)
        after['phone']['local_port']=1;self.assertEqual(after,before)


if __name__=='__main__':unittest.main()
