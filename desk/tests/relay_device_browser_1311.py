"""Built full MiniApp device sign-in; synthetic accounts, no provider/owner traffic."""
import argparse
import io
import json
from pathlib import Path
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from program_browser_1211 import ProgramFixture

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from localharness.relay_login import public_state,account_view,DEVICE_URL


class DeviceFixture(ProgramFixture):
    login={}
    prompt=None
    actions=[]
    def state(self):
        return {'port':5011,'accounts':[{'slot':'primary','configured':True,'active':True},
                {'slot':'secondary','configured':False,'active':False}],
                'login':self.login,'device_code':self.prompt,'login_url':'https://auth.openai.com/oauth/authorize?state=fixture'}
    def do_POST(self):
        if urlsplit(self.path).path!='/api/program/invoke':return super().do_POST()
        raw=self.rfile.read(int(self.headers.get('Content-Length',0)));body=json.loads(raw)
        command=body['command'];args=body.get('arguments',{})
        if command=='relay_account':return self.reply(account_view(public_state(self.state())))
        if command=='relay_login_url':return self.reply(public_state(self.state())['login']['url'])
        if command in ('relay_login','relay_cancel_login'):
            type(self).actions.append({'command':command,**args})
            if command=='relay_cancel_login':
                assert args.get('login_id')==self.login['id']
                type(self).login={};type(self).prompt=None
            else:
                type(self).login={'pending':True,'slot':args['slot'],'id':'ea562dc7-5709-4c83-bf8d-34017cf8a3c7','method':args['method']}
                type(self).prompt={'verification_url':DEVICE_URL,'user_code':'ABCD-EFGH','expires_at':int(time.time())+800}
            return self.reply(public_state(self.state()))
        self.rfile=io.BytesIO(raw)
        return super().do_POST()


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence=Path(evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),DeviceFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}';results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for width in (390,820,1280):
                    DeviceFixture.login={};DeviceFixture.prompt=None;DeviceFixture.actions=[]
                    context=browser.new_context(viewport={'width':width,'height':1000},reduced_motion='reduce',permissions=['clipboard-read','clipboard-write'])
                    page=context.new_page();page.set_default_timeout(10000);errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.route('**/*',lambda route:route.continue_() if route.request.url.startswith(origin) else route.abort())
                    page.goto(origin+'/tg/',wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                    if width<760:page.locator('#toggle-rail').click()
                    page.locator('[data-view="settings"]').click();page.get_by_text('Мозг и связь',exact=True).click()
                    accounts=page.locator('helene-relay-accounts');second=accounts.locator('section').nth(1)
                    second.get_by_role('button',name='Войти по коду',exact=True).click()
                    page.wait_for_function("document.querySelectorAll('helene-relay-accounts code')[1].textContent==='ABCD-EFGH'")
                    assert accounts.locator('section').first.get_by_role('button',name='Сменить аккаунт по коду').is_disabled()
                    second.get_by_role('button',name='Скопировать код',exact=True).click()
                    assert page.evaluate('navigator.clipboard.readText()')=='ABCD-EFGH'
                    page.wait_for_timeout(1600)
                    second.locator('.subscription-device-code').scroll_into_view_if_needed()
                    accounts.screenshot(path=str(evidence/f'device-{width}.png'))
                    page.screenshot(path=str(evidence/f'page-{width}.png'))
                    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                    DeviceFixture.prompt['expires_at']=time.time()-1
                    accounts.get_by_role('button',name='Обновить состояние').click()
                    second.get_by_text('Срок кода истёк.',exact=False).wait_for()
                    assert second.locator('code').inner_text()==''
                    second.get_by_role('button',name='Отменить вход',exact=True).click()
                    assert second.get_by_role('button',name='Войти через браузер на этом ПК',exact=True,include_hidden=True).is_hidden()
                    second.get_by_role('button',name='Войти по коду',exact=True).click()
                    page.wait_for_function("document.querySelectorAll('helene-relay-accounts code')[1].textContent==='ABCD-EFGH'")
                    assert not errors,errors
                    assert DeviceFixture.actions[-1]['method']=='device_code'
                    results.append({'width':width,'actions':list(DeviceFixture.actions),'errors':errors})
                    context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close()
    (evidence/'PASS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);run(parser.parse_args().evidence)
