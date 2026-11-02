"""A missing visual proof only. Keep exact sealed package and all healthy lanes."""
from pathlib import Path
import os,subprocess,sys
import rc23_macos_owner_ux_qa as qa

qa.OUT=qa.base.WORKSPACE/'evidence-rc23-owner-ux26-visual';qa.OUT.mkdir(exist_ok=True);qa.base.OUT=qa.OUT
if sys.argv[1]=='prepare':
    original=os.execv
    def enter(python,args):return original(python,[str(python),'-B',str(Path(__file__).resolve()),'subject'])
    os.execv=enter;qa.prepare_warning()
elif sys.argv[1]=='subject':
    version=subprocess.check_output(['sw_vers','-productVersion'],text=True).strip();assert version.split('.')[0]=='26'
    original=subprocess.Popen
    def observed(argv,*a,**kw):
        if isinstance(argv,(tuple,list)):
            argv=[str(Path(__file__).with_name('rc23_macos_owner_ux26_visual_window.py')) if str(x).endswith('/rc23_macos_owner_ux_window.py') else x for x in argv]
        return original(argv,*a,**kw)
    subprocess.Popen=observed
    try:qa.native(warning_only=True)
    finally:subprocess.Popen=original
    qa.save('MACOS26-WARNING-VISUAL.json',{'source_commit':qa.COMMIT,'macOS':version,'renderer_unmodified':True,
        'no_rebuild_or_repeated_source_tests':True,'frames':'NATIVE26-WARNING-PAINT-FRAMES.json','images_require_actual_review':True})
else:raise ValueError(sys.argv[1])
