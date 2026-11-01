"""Narrow renderer-ready proof using the already sealed Mac candidate."""
from pathlib import Path
import hashlib,json,os,shutil,subprocess,sys,time,zipfile
import rc23_macos_candidate_qa as qa
ARCHIVE_SHA='15b56cad2c7df77372d8ea4253f00db8c2768d025a4020b4bbab55cf9e5ca0ae'
ARTIFACT_SHA='cbc876fc2d70402f026b83b3fbac8c3a58ad1e8d25f1842393f914533c75318e'
def prepare():
    from build_python_macos_rc import unpack
    qa.subject_identity(materialize=True)
    outer=qa.TEMP/'qualified-release-artifact.zip';assert qa.digest(outer)==qa.OUTER_SHA
    base=qa.TEMP/'qualified-base.zip'
    with zipfile.ZipFile(outer) as z:
        row=next(i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.18-macos-arm64.zip')
        base.write_bytes(z.read(row))
    assert qa.digest(base)==qa.BASE_SHA;unpack(base,qa.BASE.parent)
    current=qa.TEMP/'rc23-candidate-artifact.zip';assert qa.digest(current)==ARTIFACT_SHA
    qa.PACK.mkdir()
    with zipfile.ZipFile(current) as z:
        rows=[i for i in z.infolist() if Path(i.filename).name=='Helene-2.3.0-rc.23-macos-arm64.zip'];assert len(rows)==1
        archive=qa.PACK/Path(rows[0].filename).name;archive.write_bytes(z.read(rows[0]))
    assert qa.digest(archive)==ARCHIVE_SHA
    with zipfile.ZipFile(archive) as z:
        assert json.loads(z.read('Helene/macos-inputs.json'))['source_commit']==qa.PRODUCT_COMMIT
    qa.save('SEALED-CANDIDATE-REUSED.json',{'archive_sha256':ARCHIVE_SHA,'artifact_sha256':ARTIFACT_SHA,
        'retained_build_and_native15_26_run':41360743312,'no_frontend_runtime_native_product_rebuild':True,
        'scope':'Only update/rollback renderer-ready screenshots; old black screenshot is not visual acceptance.'})
    driver=qa.TEMP/'visual-driver-runtime';shutil.copytree(qa.BASE/'runtime',driver)
    python=driver/'bin/python3';os.execv(str(python),[str(python),'-B',str(Path(__file__).resolve()),'subject'])
def subject():
    import rc18_macos_pipeline as helper
    run=subprocess.run;images=[]
    def ready_run(argv,*args,**kwargs):
        if argv[0]=='screencapture' and Path(argv[-1]).name in ('after-old18-to-new23.png','after-failed-start-rollback.png'):
            helper.wait(lambda:helper.control(qa.BASE) and helper.control(qa.BASE).get('ui_ready'),150)
            assert helper.invoke(qa.BASE,'app_info')['version']==qa.VERSION
            time.sleep(3);images.append(Path(argv[-1]).name)
        return run(argv,*args,**kwargs)
    subprocess.run=ready_run
    try:qa.upgrade()
    finally:subprocess.run=run
    qa.save('RENDERER-READY-PROOF.json',{'archive_sha256':ARCHIVE_SHA,'source_commit':qa.PRODUCT_COMMIT,
        'actual_new_renderer_ui_ready_before_screenshot':images,'screenshots_need_visual_review':True,
        'full_archive_update_and_failed_start_rollback':json.loads((qa.OUT/'FULL-ARCHIVE-UPDATE.json').read_text()),
        'retained_other_native15_26_proofs_run':41360743312})
if __name__=='__main__':globals()[sys.argv[1]]()
