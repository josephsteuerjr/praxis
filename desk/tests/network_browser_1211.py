"""Production MiniApp settings: saved app proxy, distinct observed connection stages."""
import argparse,json,threading
from urllib.parse import urlsplit
from pathlib import Path
from http.server import ThreadingHTTPServer
from program_browser_1211 import ProgramFixture


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),ProgramFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}'
    checks={'bot':{'state':'error','message':'Приём сообщений временно недоступен (fixture).'},
        'https':{'state':'ready','message':'Адрес подтверждён (fixture).'},
        'menu':{'state':'ready','message':'Кнопка прочитана обратно (fixture).'},
        'authorization':{'state':'unknown','message':'Вход ещё не проверен (fixture).'}}
    results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for width in (390,820):
                    context=browser.new_context(viewport={'width':width,'height':900},reduced_motion='reduce')
                    page=context.new_page();errors=[];saves=[];retries=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    def route(request):
                        url=request.request.url
                        if not url.startswith(origin):return request.abort()
                        path=urlsplit(url).path
                        if path.endswith('/api/phone'):
                            return request.fulfill(json={'state':'ready','message':'HTTPS отвечает','telegram':'ready','checks':checks})
                        if path.endswith('/api/phone/retry'):
                            retries.append(True);return request.fulfill(json={'ok':True})
                        if url.endswith('/api/program/invoke'):
                            body=request.request.post_data_json
                            if body.get('command')=='config_save':
                                saves.append(json.loads(body['arguments']['config']));return request.fulfill(json={'ok':True})
                        return request.continue_()
                    page.route('**/*',route);page.set_default_timeout(8000)
                    page.goto(origin+'/tg/',wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                    if width<760:page.locator('#toggle-rail').click()
                    page.locator('[data-view="settings"]').click();page.get_by_text('Мозг и связь',exact=True).click()
                    page.screenshot(path=str(evidence/f'before-{width}.png'))
                    (evidence/f'before-{width}.txt').write_text(page.locator('body').inner_text(),encoding='utf8')
                    (evidence/f'before-{width}.html').write_text(page.locator('body').inner_html(),encoding='utf8')
                    card=page.get_by_text('Сеть Telegram и Mini App',exact=True).locator('xpath=ancestor::section[contains(@class,"card")][1]')
                    card.get_by_role('button',name='Системные настройки',exact=True).click()
                    page.get_by_role('option',name='Через выбранный прокси',exact=True).click()
                    card.get_by_label('Сервер',exact=True).fill('127.0.0.1')
                    card.get_by_label('Порт',exact=True).fill('8888')
                    card.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'proxy-{width}.png'))
                    page.get_by_role('button',name='Сохранить',exact=True).click()
                    page.wait_for_timeout(150)
                    assert saves,'settings were not saved'
                    assert saves[-1]['network']['proxy']['host']=='127.0.0.1'
                    assert str(saves[-1]['network']['proxy']['port'])=='8888'
                    (evidence/f'after-{width}.html').write_text(page.locator('body').inner_html(),encoding='utf8')
                    page.get_by_text('Telegram-бот: Приём сообщений временно недоступен (fixture).',exact=True).wait_for()
                    page.get_by_role('button',name='Обновить подключение',exact=True).click()
                    assert retries,'retry was not requested'
                    page.locator('.phone-checks').scroll_into_view_if_needed()
                    page.screenshot(path=str(evidence/f'stages-{width}.png'))
                    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                    assert not errors,errors
                    results.append({'width':width,'proxy_saved':True,'retry':True,'errors':errors})
                    context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close()
    (evidence/'PASS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True)
    run(parser.parse_args().evidence)
