"""HTTPS fallback must not override a system or explicitly selected CA store."""
from pathlib import Path
import os,ssl,sys,unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import tls_context

class Trust(unittest.TestCase):
 def test_minimal_host_uses_bundled_roots(self):
  context=Mock();context.get_ca_certs.return_value=[]
  with patch.dict(os.environ,{},clear=True),patch.object(ssl,'create_default_context',return_value=context),patch('certifi.where',return_value='/fixture/bundled-roots.pem'):
   self.assertIs(tls_context.default_context(),context)
  context.load_verify_locations.assert_called_once_with(cafile='/fixture/bundled-roots.pem')
 def test_system_roots_remain_authoritative(self):
  context=Mock();context.get_ca_certs.return_value=[{'fixture':'existing system root'}]
  with patch.dict(os.environ,{},clear=True),patch.object(ssl,'create_default_context',return_value=context):tls_context.default_context()
  context.load_verify_locations.assert_not_called()
 def test_explicit_owner_store_is_never_extended(self):
  for name in ('SSL_CERT_FILE','SSL_CERT_DIR'):
   with self.subTest(variable=name):
    context=Mock();context.get_ca_certs.return_value=[]
    with patch.dict(os.environ,{name:'/fixture/owner'},clear=True),patch.object(ssl,'create_default_context',return_value=context):tls_context.default_context()
    context.load_verify_locations.assert_not_called()
 def test_real_context_still_requires_trusted_certificate_and_hostname(self):
  context=tls_context.default_context()
  self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)

if __name__=='__main__':unittest.main()
