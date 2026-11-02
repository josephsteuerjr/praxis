"""External native DOM driver. All business logic comes from installed bytes."""
from pathlib import Path
import hashlib,json,os,subprocess,sys,threading,time,traceback

MODE=sys.argv[1];ROOT=Path(sys.argv[2]).resolve();DATA=Path(sys.argv[3]).resolve();OUT=Path(sys.argv[4]).resolve()
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tree')]
import webview
from localharness import boot,first_birth,owner_stop,posix_first_start
import python_app
assert Path(first_birth.__file__).resolve().is_relative_to(ROOT/'app')
TEXT='# Native owner constitution\nUnicode: Привет\n<script>text stays plain</script>\nEdited before Create.\n'
NAME='Owner UX native fixture';OWNER='Disposable CI'
def save(name,value):(OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def wait(fn,seconds=120):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if value:return value
        time.sleep(.2)
    raise TimeoutError('Native DOM did not reach the required state')
def snapshot(name):subprocess.run(['screencapture','-x',str(OUT/name)],check=True)
def evaluate(win,code):return win.evaluate_js(code)
original_window=webview.create_window
WRITES=[]
original_invoke=python_app.Application.invoke
def observed_invoke(self,command,args):
    if command in ('update_start','update_install','update_agent_confirm'):WRITES.append(command)
    return original_invoke(self,command,args)
python_app.Application.invoke=observed_invoke

def create_dom(win):
    wait(lambda:evaluate(win,"!!document.getElementById('form') && !document.getElementById('form').hidden"))
    scripts_before=evaluate(win,"Array.from(document.scripts,x=>x.textContent)")
    snapshot('owner-ux-first-start-top.png')
    # Exercise the actual browser File.text handler, then edit the result.
    loaded=TEXT.removesuffix('Edited before Create.\n')
    evaluate(win,"""(()=>{const file=new File([%s],'constitution.md',{type:'text/markdown'});const transfer=new DataTransfer();transfer.items.add(file);document.getElementById('soul-file').files=transfer.files;document.getElementById('soul-file').dispatchEvent(new Event('change'));return true})()"""%json.dumps(loaded))
    wait(lambda:evaluate(win,"document.getElementById('soul-text').value===%s"%json.dumps(loaded)))
    evaluate(win,"document.getElementById('soul-text').value+=%s;document.getElementById('soul-text').scrollIntoView({block:'center'});true"%json.dumps('Edited before Create.\n'))
    wait(lambda:evaluate(win,"!document.getElementById('soul-editor').hidden && document.getElementById('soul-text').value===%s"%json.dumps(TEXT)))
    time.sleep(1);snapshot('owner-ux-custom-constitution.png')
    values={'name':NAME,'owner':OWNER,'provider':'openai','model':'fixture-unused','key':'fixture-unused','base':'http://127.0.0.1:9/v1'}
    evaluate(win,"(()=>{for(const [id,value] of Object.entries(%s))document.getElementById(id).value=value;document.getElementById('provider').dispatchEvent(new Event('change'));document.getElementById('create').scrollIntoView({block:'center'});return document.getElementById('form').checkValidity()})()"%json.dumps(values))
    assert evaluate(win,"document.getElementById('form').checkValidity()")
    save('NATIVE-FORM-DOM.json',{'actual_file_text_handler':True,'edited_text_exact':True,
        'no_HTML_element_injected':scripts_before==evaluate(win,"Array.from(document.scripts,x=>x.textContent)"),
        'first_create_not_called_yet':not (OUT/'NATIVE-FIRST-CREATE.json').exists()})
    evaluate(win,"document.getElementById('form').requestSubmit();true")

def warning_dom(win):
    wait(lambda:evaluate(win,"!!document.querySelector('.rail-item[data-view=settings]')"))
    evaluate(win,"document.querySelector('.rail-item[data-view=settings]').click();true")
    wait(lambda:evaluate(win,"!!document.getElementById('update-reopen-hint')"))
    evaluate(win,"(()=>{const tab=Array.from(document.querySelectorAll('.settings-tabs [role=tab]')).find(n=>n.textContent.trim()==='Программа');if(!tab)throw Error('Programme settings group unavailable');tab.click();return true})()")
    evaluate(win,"(()=>{const about=Array.from(document.querySelectorAll('nav a')).find(a=>a.textContent.trim()==='О программе');if(about)about.click();return true})()")
    evaluate(win,"document.getElementById('update-reopen-hint').scrollIntoView({block:'center'});true")
    def current():
        return evaluate(win,"(()=>{const n=document.getElementById('update-reopen-hint');const r=n.getBoundingClientRect();return {text:n.textContent,visible:r.height>0&&r.top>=0&&r.bottom<=innerHeight&&getComputedStyle(n).visibility!=='hidden'&&!!n.getClientRects().length}})()")
    row=wait(lambda:(r if (r:=current()).get('visible') else None))
    assert 'несколько минут' in row['text'] and 'Не запускай программу повторно' in row['text']
    time.sleep(2);snapshot('owner-ux-update-warning.png')
    row['update_writes_before_warning']=list(WRITES)
    row['actual_installed_renderer']=True;save('NATIVE-UPDATE-WARNING.json',row)

def create_window(*args,**kwargs):
    win=original_window(*args,**kwargs)
    def loaded():
        def run():
            try:(create_dom if MODE=='create' else warning_dom)(win)
            except BaseException as e:
                save('NATIVE-DOM-ERROR-'+MODE+'.json',{'error':str(e),'traceback':traceback.format_exc()})
                snapshot('owner-ux-DOM-error-'+MODE+'.png')
        threading.Thread(target=run,name='external-native-DOM-proof',daemon=True).start()
    win.events.loaded+=loaded
    return win
webview.create_window=create_window

if MODE=='create':
    import first_start_window
    original_first=first_start_window.FirstStartWindow.invoke
    def invoke(self,command,args):
        if command=='first_open':owner_stop.change_pause(DATA,True,reason='Disposable native fixture remains paused',via='fixture')
        result=original_first(self,command,args)
        if command=='first_create':
            soul=(DATA/'soul/SOUL.md').read_bytes();authority=json.loads((ROOT/'kit-birth.json').read_text())
            assert soul==TEXT.encode()
            assert authority['source']==first_birth.SOURCE and authority['state'] in ('partial','complete') and authority['creation']['published']
            assert (ROOT/'kit-birth.json.claim').is_file()
            save('NATIVE-FIRST-CREATE.json',{'actual_SOUL_exact':True,'SOUL_sha256':sha(soul),'real_birth_authority_consumed':True,
                'birth_state':authority['state'],'creation_source':authority['source'],'app_module_from':str(Path(first_birth.__file__).resolve()),
                'pending_login_transfer_required':False,'fixture_only_paused_before_open':True,'provider_acceptance':False})
        return result
    first_start_window.FirstStartWindow.invoke=invoke
    raise SystemExit(first_start_window.main(ROOT,places=posix_first_start.Places(DATA),flow=posix_first_start))
elif MODE=='warning':
    # A new sealed-archive warning-only run needs one disposable paused profile.
    # No relay/provider login or first-start GUI is repeated for this fixture.
    if not posix_first_start.agent_exists(DATA):
        places=posix_first_start.Places(DATA);posix_first_start.prepare(ROOT,places=places)
        posix_first_start.create_agent(ROOT,{'name':NAME,'owner':OWNER,'provider':'openai','model':'fixture-unused',
            'key':'fixture-unused','base_url':'http://127.0.0.1:9/v1','soul':{'kind':'text','text':TEXT}},places=places)
        owner_stop.change_pause(DATA,True,reason='Warning-only disposable fixture remains paused',via='fixture')
    raise SystemExit(python_app.main(['--root',str(ROOT)]))
else:raise ValueError(MODE)
