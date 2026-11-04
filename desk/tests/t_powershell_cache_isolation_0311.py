"""Real Windows PowerShell cache writes stay outside the source under test."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness import source_update_battery as battery
from localharness import source_verification_bundle as bundle

HELENE_TEST_ISOLATION='quiescent'


@unittest.skipUnless(os.name=='nt','Windows PowerShell module-analysis cache')
class WindowsModuleCache(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='powershell-cache-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name).resolve()
        self.repository=self.root/'repository'
        self.app=self.repository/'desk'
        self.app.mkdir(parents=True)

    def environment(self,name):
        home=self.root/name
        return home,battery._environment(home,self.repository/'live',self.app)

    def test_cache_uses_absolute_private_profile_and_ignores_owner_override(self):
        owner=self.root/'owner-cache'
        with mock.patch.dict(os.environ,{'PSModuleAnalysisCachePath':str(owner)}):
            home,env=self.environment('home')
            self.assertEqual(os.environ['PSModuleAnalysisCachePath'],str(owner))
        path=Path(env['PSModuleAnalysisCachePath'])
        self.assertTrue(path.is_absolute())
        self.assertTrue(path.is_relative_to(home))
        self.assertFalse(path.is_relative_to(self.repository))
        self.assertTrue(path.parent.is_dir())
        self.assertNotEqual(path,owner)

    def test_independent_verification_profiles_do_not_share_module_cache(self):
        first,a=self.environment('first-home')
        second,b=self.environment('second-home')
        self.assertNotEqual(a['PSModuleAnalysisCachePath'],b['PSModuleAnalysisCachePath'])
        self.assertTrue(Path(a['PSModuleAnalysisCachePath']).is_relative_to(first))
        self.assertTrue(Path(b['PSModuleAnalysisCachePath']).is_relative_to(second))

    def test_actual_powershell_background_cache_preserves_exact_source_inventory(self):
        (self.app/'fixture.txt').write_text('source fixture',encoding='utf8')
        before=bundle.inventory(self.repository)
        home,env=self.environment('native-home')
        expected=(home/'local/powershell/ModuleAnalysisCache').resolve()
        powershell=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
        env['PSModulePath']=str(powershell.parent/'Modules')
        env['HELENE_EXPECTED_CACHE']=str(expected)
        script="""$ErrorActionPreference='Stop';
Import-Module Microsoft.PowerShell.Management -Force;
Get-Command -Module Microsoft.PowerShell.Management | Out-Null;
$deadline=[DateTime]::UtcNow.AddSeconds(25);
while(!(Test-Path -LiteralPath $env:HELENE_EXPECTED_CACHE) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 100};
if(!(Test-Path -LiteralPath $env:HELENE_EXPECTED_CACHE)){throw 'Private module cache was not written'};
Write-Output 'PRIVATE_MODULE_CACHE_WRITTEN'
"""
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-Command',script],
            cwd=self.app,env=env,capture_output=True,timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf8',errors='replace'))
        self.assertIn(b'PRIVATE_MODULE_CACHE_WRITTEN',result.stdout)
        self.assertGreater(expected.stat().st_size,0)
        self.assertEqual(bundle.inventory(self.repository),before)
        changed,_=battery._test_copy_changes(self.repository,before,[])
        self.assertEqual(changed,[])


if __name__=='__main__':unittest.main()
