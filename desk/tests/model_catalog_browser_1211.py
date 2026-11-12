"""Production MiniApp model settings: late replies, failure and account changes."""
import argparse
import json
from pathlib import Path
import threading
import time
from http.server import ThreadingHTTPServer

from program_browser_1211 import ProgramFixture


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence=Path(evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),ProgramFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    state={'hold':True,'revision':'a','fail':False}
    held=[];requests=[];errors=[]
    def result(models):
        return {'ok':True,'models':models,'model_details':[{'id':name,'reasoning_efforts':['high','xhigh']} for name in models],
                'note':'fixture catalog','cached':False}
    def route_request(route):
        request=route.request
        if not request.url.startswith(origin):return route.abort()
        if '/api/program/invoke' not in request.url:return route.continue_()
        body=request.post_data_json or {};command=body.get('command');args=body.get('arguments',{})
        requests.append(body)
        if command=='relay_account':
            return route.fulfill(json={'reachable':True,'active_slot':'primary','catalog_revision':state['revision'],
                'slots':[{'slot':'primary','stored':True,'serving':True,'loaded':True}]})
        if command=='probe_model':
            assert args.get('key')=='','catalog must not receive provider secrets'
            if not args.get('refresh') and state['hold']:
                held.append(route);return
            if state['fail']:return route.fulfill(json={'ok':False,'models':[],'note':'fixture offline'})
            names=['gpt-6.1-sol','fresh-only'] if state['revision']=='a' else ['gpt-6-astra','account-b-only']
            return route.fulfill(json=result(names))
        return route.continue_()
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                page=browser.new_page(viewport={'width':820,'height':880})
                page.set_default_timeout(8000);page.on('pageerror',lambda error:errors.append(str(error)))
                page.route('**/*',route_request)
                page.goto(origin+'/tg/',wait_until='domcontentloaded')
                page.wait_for_selector('#app:not([hidden])')
                page.locator('[data-view="settings"]').click()
                page.get_by_text('Мозг и связь',exact=True).click()
                refresh=page.get_by_role('button',name='Обновить модели',exact=True)
                refresh.wait_for()
                deadline=time.monotonic()+5
                while not held and time.monotonic()<deadline:page.wait_for_timeout(50)
                assert held,'initial catalog call was not observed'
                refresh.click()
                page.get_by_role('button',name='fresh-only',exact=True).wait_for()
                state['hold']=False
                for request in held:request.fulfill(json=result(['old-only']))
                page.wait_for_timeout(150)
                assert page.get_by_role('button',name='old-only',exact=True).count()==0
                assert page.get_by_role('button',name='fresh-only',exact=True).is_visible()

                model=page.locator('#s-0-brain').get_by_label('Модель',exact=True).filter(visible=True)
                model.fill('unsaved-selected-model')
                state['fail']=True;refresh.click()
                page.get_by_text('fixture offline',exact=True).wait_for()
                assert model.input_value()=='unsaved-selected-model'
                assert page.get_by_role('button',name='fresh-only',exact=True).is_visible()

                state['fail']=False;state['revision']='b'
                page.get_by_role('button',name='Обновить состояние',exact=True).click()
                page.get_by_role('button',name='account-b-only',exact=True).wait_for()
                assert page.get_by_role('button',name='fresh-only',exact=True).count()==0
                assert model.input_value()=='unsaved-selected-model'
                assert not errors,errors
                (evidence/'PASS.json').write_text(json.dumps({'ok':True,'cases':[
                    'late initial reply cannot replace newer refresh',
                    'failed refresh preserves models and unsaved selection',
                    'account generation replaces old catalog without replacing draft'],
                    'model_requests':[r['arguments'] for r in requests if r.get('command')=='probe_model'],
                    'errors':errors},ensure_ascii=False,indent=2),encoding='utf8')
            finally:browser.close()
    finally:server.shutdown();server.server_close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True)
    run(parser.parse_args().evidence)
