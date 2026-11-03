"""A non-Hélène device/cache path must never be lstat'ed by discovery."""
from pathlib import Path
import tempfile,unittest
from unittest.mock import patch
from localharness import first_start


class BoundedDiscovery(unittest.TestCase):
    def test_unrelated_inaccessible_entry_is_never_inspected(self):
        with tempfile.TemporaryDirectory(prefix='bounded-discovery-') as temporary:
            base=Path(temporary).resolve();current=base/'current';current.mkdir()
            unrelated=base/'Docker';unrelated.mkdir()
            legacy=base/'Helene-old';legacy.mkdir()
            real_linked=first_start.linked;seen=[]
            def linked(path):
                path=Path(path);seen.append(path)
                if path==unrelated:raise OSError(1920,'Synthetic inaccessible device path')
                return real_linked(path)
            with patch.object(first_start,'linked',side_effect=linked):
                result=first_start._roots(base,current)
            self.assertEqual(result,[legacy]);self.assertNotIn(unrelated,seen)

    def test_automatic_phone_without_fixed_ingress_allocates_a_fresh_channel(self):
        with tempfile.TemporaryDirectory(prefix='phone-port-') as temporary:
            root=Path(temporary);old=root/'old';new=root/'new';data=root/'data'
            old.mkdir();new.mkdir();data.mkdir()
            before={'port':1,'phone':{'mode':'automatic','local_port':1,'external':''}}
            actual=first_start._bind(before,old,new,data)
            self.assertNotEqual(actual['port'],1)
            self.assertEqual(actual['phone']['local_port'],actual['port'])
            self.assertEqual(actual['phone']['external'],before['phone']['external'])
            self.assertEqual(before['phone']['local_port'],1)

    def test_explicit_stable_phone_preserves_its_selected_port(self):
        with tempfile.TemporaryDirectory(prefix='phone-port-') as temporary:
            root=Path(temporary)
            before={'port':12345,'phone':{'mode':'stable','local_port':12345,'external':'https://synthetic-stable.invalid'}}
            actual=first_start._bind(before,root,root/'new',root/'data')
            self.assertEqual(actual['port'],before['port'])


if __name__=='__main__':unittest.main()
