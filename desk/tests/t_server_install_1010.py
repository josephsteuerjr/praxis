"""Execute the actual first-install/recovery script with an isolated Docker fixture."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
DESK=Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform.startswith('linux'),'server shell runs inside the Linux boundary')
class ServerInstall(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.program=self.root/'program';self.target=self.root/'installed'
        (self.program/'server').mkdir(parents=True);self.bin=self.root/'bin';self.bin.mkdir()
        shutil.copy(DESK/'server/install.sh',self.program/'server/install.sh')
        (self.program/'helene-build.json').write_text(json.dumps({'version':'2.7.9'},indent=2))
        (self.program/'server/helene.server.json').write_text('{"mode":"local","tree":"data"}')
        (self.program/'server/docker-compose.yml').write_text('name: helene')
        (self.program/'server/updater').mkdir();(self.program/'server/updater/updater.py').write_text('# fixture')
        self.archive=self.root/'agent.zip';self.archive.write_bytes(b'cold archive')
        docker=self.bin/'docker';docker.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
r=Path(os.environ['TEST_ROOT']);a=sys.argv[1:]
with (r/'calls').open('a') as out:out.write(json.dumps(a)+'\\n')
if a and a[0]=='inspect':
    if not (r/'running').exists():sys.exit(1)
    if '-f' in a:print(os.environ['HELENE_DIR']+'/server')
elif a and a[0]=='compose' and 'up' in a and not any('updater' in arg for arg in a):
    (r/'running').write_text('yes')
elif a and a[0]=='run' and any('server_prepare.py' in arg for arg in a):
    if (r/'fail-voice').exists():sys.exit(1)
elif a and a[0]=='logs':print('ключ окна\\ntest-key-only')
''');docker.chmod(0o755)
        self.env={**os.environ,'PATH':str(self.bin)+':'+os.environ['PATH'],'HELENE_DIR':str(self.target),'HELENE_AUTOMATIC_CONNECTION':'1','TEST_ROOT':str(self.root)}
    def run_script(self,archive=None):
        return subprocess.run(['sh',str(self.program/'server/install.sh'),str(archive or self.archive)],env=self.env,capture_output=True,text=True,timeout=15)
    def calls(self):return [json.loads(row) for row in (self.root/'calls').read_text().splitlines()]
    def test_first_install_prepares_enabled_voice_before_agent_starts(self):
        result=self.run_script();self.assertEqual(result.returncode,0,result.stderr)
        calls=self.calls();prepare=next(i for i,row in enumerate(calls) if any('server_prepare.py' in arg for arg in row))
        up=next(i for i,row in enumerate(calls) if 'up' in row)
        self.assertLess(prepare,up)
        self.assertIn('--connect',calls[prepare])
        self.assertTrue((self.target/'.transfer-import.sha256').is_file())
    def test_voice_failure_keeps_agent_off_and_retry_does_not_reimport(self):
        fail=self.root/'fail-voice';fail.write_text('test')
        first=self.run_script();self.assertNotEqual(first.returncode,0)
        self.assertFalse((self.root/'running').exists())
        fail.unlink();second=self.run_script();self.assertEqual(second.returncode,0,second.stderr)
        imports=[row for row in self.calls() if 'import' in row]
        self.assertEqual(len(imports),1,'resuming model preparation must preserve already imported memory')
    def test_different_archive_is_refused_during_recovery(self):
        (self.root/'fail-voice').write_text('test');self.run_script()
        other=self.root/'other.zip';other.write_bytes(b'other agent')
        result=self.run_script(other);self.assertNotEqual(result.returncode,0)
        self.assertIn('другого архива',result.stderr)
        self.assertEqual(len([r for r in self.calls() if 'import' in r]),1)
        self.assertFalse((self.root/'running').exists())

if __name__=='__main__':unittest.main(verbosity=2)
