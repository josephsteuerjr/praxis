"""Real child lifecycle; run only across the Linux/Docker boundary."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'localharness'))
from phone_tunnel import cloudflare_tunnel as cf

@unittest.skipIf(os.name=='nt','Process-spawning gates must run in Linux/Docker, never native Codex Windows.')
class Process(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp=tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.root=Path(tmp.name)
        self.binary=self.root/'synthetic-cloudflared'
    async def run_tunnel(self,body):
        self.binary.write_text('#!'+sys.executable+'\n'+body)
        self.binary.chmod(0o700)
        tunnel=cf.QuickTunnel(self.binary,self.root,8094)
        with patch.object(cf,'_installed_path',return_value=self.binary), patch.object(cf,'_verify_binary'):
            await tunnel.start()
        return tunnel
    async def test_real_process_url_and_idempotent_stop_cleanup(self):
        tunnel=await self.run_tunnel("import sys,time\nprint('https://paper-check.trycloudflare.com',file=sys.stderr,flush=True)\ntime.sleep(60)\n")
        try:
            self.assertEqual(await tunnel.wait_url(3),'https://paper-check.trycloudflare.com/')
            self.assertIsNone(tunnel.returncode)
        finally: await tunnel.stop()
        await tunnel.stop()
        self.assertIsNotNone(tunnel.returncode)
        self.assertFalse(list((self.root/'cloudflared/quick-tunnel-runs').glob('home-*')))
        self.assertIsNone(tunnel._watchdog_write_fd)
    async def test_real_crash_before_url_is_observed_and_watchdog_disarmed(self):
        tunnel=await self.run_tunnel('raise SystemExit(7)\n')
        try:
            with self.assertRaises(cf.CloudflaredError): await tunnel.wait_url(3)
        finally: await tunnel.stop()
        self.assertEqual(tunnel.returncode,7)
        self.assertIsNone(tunnel._watchdog_write_fd)

if __name__=='__main__': unittest.main()
