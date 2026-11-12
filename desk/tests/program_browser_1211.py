"""Full production MiniApp, isolated SDK/controller fixture, no owner services."""
import argparse
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer
from miniapp_browser_0511 import Fixture, SDK


class ProgramFixture(Fixture):
    config={'setup_complete':True,'agent':{'name':'Мира'},'owner':{'name':'Владелец'},'model':{'model':'gpt-6.1-sol','provider':'chatgpt','framework':'openai'},
            'relay':{'enabled':True,'port':5011},'telegram':{'owner_id':123},'phone':{'enabled':True,'mode':'automatic'}}
    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/program/'):
            self.path='/'+'/'.join(self.path.split('/')[3:]);path=urlsplit(self.path).path
        if path=='/api/rooms':return self.reply([{'id':'window','name':'Мира','title':'Мира'}])
        if path=='/pair/devices':return self.reply([])
        if path=='/api/tasks':return self.reply([])
        if path=='/api/panic':return self.reply({'stopped':False})
        if path=='/api/self-review':return self.reply({'active':False})
        if path=='/api/phone':return self.reply({'state':'ready','message':'HTTPS проверен','telegram':'ready'})
        if path=='/api/voice/status':return self.reply({'ready':False})
        super().do_GET()
    def do_POST(self):
        path=urlsplit(self.path).path
        if path!='/api/program/invoke':return super().do_POST()
        body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
        self.requests.append(('POST',path,body));command=body['command'];args=body.get('arguments',{})
        if command in ('program_bootstrap','switch_agent'):
            identity=args.get('id') or body.get('agent') or 'base'
            return self.reply({'agent':'Мира','agent_id':identity,'base':'/program/'+identity,'agents':[], 'local_agent':True})
        if command=='app_info':return self.reply({'platform':'win32','version':json.loads((Path(__file__).resolve().parents[1]/'app/package.json').read_bytes())['version'],'root':'C:/Fixture'})
        if command=='appearance_read':return self.reply({'values':{},'seen':'1'})
        if command=='config_load':return self.reply({'config':self.config,'mtime_ns':'1','tree':'C:/Fixture/data','path':'C:/Fixture/helene.json','exe_dir':'C:/Fixture'})
        if command=='config_save':
            self.config=json.loads(args['config']);return self.reply({'ok':True})
        if command in ('components_status','backup_list'):return self.reply([])
        if command=='agents_list':return self.reply({'agents':[],'current':body.get('agent') or 'base'})
        if command=='relay_status':return self.reply({'reachable':True,'active_slot':'primary'})
        if command=='probe_model':return self.reply({'ok':True,'cached':not args.get('refresh'),'models':['gpt-6.1-sol','gpt-6-astra'],
            'model_details':[{'id':'gpt-6.1-sol','reasoning_efforts':['high','xhigh']},{'id':'gpt-6-astra'}],'note':'Сохранённый каталог'})
        if command=='autostart_get':return self.reply(False)
        return self.reply({})


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence=Path(evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),ProgramFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for width in (390,820):
                    context=browser.new_context(viewport={'width':width,'height':880},reduced_motion='reduce')
                    page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                    page.set_default_timeout(8000)
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.abort())
                    page.goto(origin+'/tg/',wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                    page.screenshot(path=str(evidence/f'main-{width}.png'))
                    if width<760:page.locator('#toggle-rail').click()
                    page.locator('[data-view="settings"]').click()
                    page.get_by_text('Мозг и связь',exact=True).click()
                    page.get_by_role('button',name='Обновить модели',exact=True).wait_for()
                    page.get_by_role('button',name='Обновить модели',exact=True).click()
                    page.get_by_role('button',name='gpt-6.1-sol',exact=True).wait_for()
                    page.screenshot(path=str(evidence/f'settings-{width}.png'))
                    geometry=page.evaluate('({overflow:document.documentElement.scrollWidth>innerWidth,chrome:document.querySelector("#chrome").getBoundingClientRect().top,app:document.querySelector("#app").getBoundingClientRect().bottom})')
                    assert not geometry['overflow'],geometry
                    assert not errors,errors
                    results.append({'width':width,'geometry':geometry,'errors':errors})
                    context.close()
                context=browser.new_context();page=context.new_page()
                context.add_init_script('window.__scenario={auth:false}')
                before=len(ProgramFixture.requests)
                page.goto(origin+'/tg/',wait_until='networkidle')
                assert page.locator('.program-login').is_visible()
                assert not page.locator('#app').is_visible()
                assert not any(len(r)>2 and r[1]=='/api/program/invoke' for r in ProgramFixture.requests[before:])
                context.close()
                (evidence/'PASS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
            finally:browser.close()
    finally:server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);args=parser.parse_args();run(args.evidence)
