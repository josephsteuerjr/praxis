"""Changed owner-UX contracts and installed UI on the second supported macOS."""
import os,subprocess,sys
from pathlib import Path
import rc23_macos_owner_ux_qa as qa

qa.OUT=qa.base.WORKSPACE/'evidence-rc23-owner-ux26';qa.OUT.mkdir(exist_ok=True)
qa.base.OUT=qa.OUT
if sys.argv[1]=='prepare':
    original=os.execv
    def enter(python,args):
        assert args[-1]=='warning'
        return original(python,[str(python),'-B',str(Path(__file__).resolve()),'subject'])
    os.execv=enter
    qa.prepare_warning()
elif sys.argv[1]=='subject':
    platform=subprocess.check_output(['sw_vers','-productVersion'],text=True).strip()
    assert platform.split('.')[0]=='26',platform
    qa.focused()
    qa.native()
    qa.save('MACOS26-CHANGED-DELTA-PASS.json',{'source_commit':qa.COMMIT,'macOS':platform,
        'current_source_targeted_contracts':'TARGETED-PYTHON.json','installed_UI':'NATIVE-OWNER-UX.json',
        'sealed_archive_reused':True,'product_runtime_native_frontend_rebuilds':0,
        'scope':'Only new birth/constitution/cancellation/first-start contracts and actual new constitution form/update warning on macOS26. Prior broad mechanical battery retained.'})
else:raise ValueError(sys.argv[1])
