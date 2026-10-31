"""ZIP visibility metadata and actual Windows Shell extraction on owned files."""
from pathlib import Path
import ctypes,json,os,sys,tempfile,time,unittest,zipfile
sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parents[1]/'installer')]
from portable_zip import copy_records,info_for

@unittest.skipUnless(sys.platform=='win32','Windows archive visibility')
class WindowsVisibility(unittest.TestCase):
    def test_archive_native_extraction_and_first_start_show_one_application_entry(self):
        from localharness.python_windows import powershell
        from localharness.program_state import tidy_program
        with tempfile.TemporaryDirectory(prefix='helene-zip-visibility-') as tmp:
            root=Path(tmp);source=root/'source.zip';archive=root/'current.zip';target=root/'extracted';target.mkdir()
            with zipfile.ZipFile(source,'w') as output:
                for name in ('helene.exe','body.exe','runtime/python.exe','app/module.py','README.md'):
                    output.writestr(info_for('Helene/'+name),b'owned non-executable fixture')
                output.writestr(info_for('Helene/python-package-files.json'),json.dumps({'files':dict.fromkeys(('helene.exe','body.exe','runtime/python.exe','app/module.py','README.md'))}))
            copy_records(source,archive,{},set(),windows_visibility=True)
            with zipfile.ZipFile(archive) as reader:
                self.assertEqual(reader.getinfo('Helene/helene.exe').external_attr&2,0)
                for name in ('body.exe','runtime/','app/','README.md'):
                    self.assertTrue(reader.getinfo('Helene/'+name).external_attr&2)
            powershell("$s=New-Object -ComObject Shell.Application;$z=$s.NameSpace($env:HELENE_FIXTURE_ZIP);$d=$s.NameSpace($env:HELENE_FIXTURE_OUT);if(!$z -or !$d){throw 'Native ZIP surface unavailable'};$d.CopyHere($z.Items(),1044)",
                       dict(os.environ,HELENE_FIXTURE_ZIP=str(archive),HELENE_FIXTURE_OUT=str(target)))
            deadline=time.monotonic()+15
            while not (target/'Helene/app/module.py').is_file():
                if time.monotonic()>deadline:self.fail('Native archive extraction did not complete')
                time.sleep(.05)
            before={name:ctypes.windll.kernel32.GetFileAttributesW(str(target/'Helene'/name)) for name in ('helene.exe','body.exe','runtime','app','README.md')}
            # Explorer can discard ZIP DOS attributes. The same production
            # first-start function applies them before the wizard/window opens.
            tidy_program(target/'Helene')
            attributes={name:ctypes.windll.kernel32.GetFileAttributesW(str(target/'Helene'/name)) for name in before}
            self.assertFalse(attributes['helene.exe']&2)
            for name in ('body.exe','runtime','app','README.md'):self.assertTrue(attributes[name]&2,(name,attributes))
            print('NATIVE_ZIP_VISIBILITY '+json.dumps({'extracted':before,'first_start':attributes}))

if __name__=='__main__':unittest.main()
