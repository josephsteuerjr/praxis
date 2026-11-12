"""Production MiniApp/server settings with isolated SSH/install controller replies."""
import argparse
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from program_browser_1211 import ProgramFixture


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence=Path(evidence);evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),ProgramFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f'http://127.0.0.1:{server.server_port}';results=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            try:
                for width in (390,820):
                    config=copy.deepcopy(ProgramFixture.config);config['port']=18765
                    context=browser.new_context(viewport={'width':width,'height':960},reduced_motion='reduce')
                    page=context.new_page();page.set_default_timeout(10000)
                    errors=[];calls=[];saves=[];profiles=[];bridges=[];operations=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    bridge={'id':'b'*32,'name':'Мой сервер','external':'https://helene.example.org','phase':'configured',
                            'ports':{'local_proxy_port':8888,'remote_proxy_port':8888,'local_app_port':18765,'remote_app_port':18766}}
                    installed=False;apply_count=0
                    def route(request):
                        nonlocal installed,apply_count,config
                        url=request.request.url
                        if not url.startswith(origin):return request.abort()
                        if urlsplit(url).path!='/api/program/invoke':return request.continue_()
                        body=request.request.post_data_json;command=body['command'];args=body.get('arguments',{})
                        if command=='config_load':return request.fulfill(json={'config':config,'mtime_ns':str(len(saves)+1),'tree':'C:/Fixture/data','path':'C:/Fixture/helene.json','exe_dir':'C:/Fixture'})
                        if command=='config_save':
                            config=json.loads(args['config']);saves.append(copy.deepcopy(config));return request.fulfill(json={'ok':True,'mtime_ns':str(len(saves)+1)})
                        if command=='component_install':installed=True;return request.fulfill(json={'ok':True,'state':'completed','restart_required':False})
                        if command=='components_status':return request.fulfill(json=[{'id':'server-bridge','ready':True,'installed':installed,'restart_required':False}])
                        if command not in ('server_connection','server_bridge'):return request.continue_()
                        action=args['action'];raw=json.loads(args.get('request') or '{}');calls.append((command,action,raw))
                        if command=='server_connection':
                            if action=='list':return request.fulfill(json=profiles)
                            if not raw.get('confirmed_host'):return request.fulfill(json={'ok':False,'trust_needed':True,'fingerprint':'SHA256:fixture-pin','message':'Сверь отпечаток сервера.'})
                            assert raw['fingerprint']=='SHA256:fixture-pin'
                            assert raw['password']=='-----BEGIN OPENSSH PRIVATE KEY-----\nfixture-only\n-----END OPENSSH PRIVATE KEY-----'
                            profile={'id':'a'*32,'name':'Мой сервер','host':'fixture.example.org','port':22,'login':'root'}
                            profiles.append(profile);return request.fulfill(json={'ok':True,'profile':profile,'message':'Подключение сохранено.'})
                        if action=='list':return request.fulfill(json={'bridges':bridges,'states':{},'component':{'installed':installed}})
                        if action=='operations':return request.fulfill(json=operations)
                        if action=='prepare':
                            assert raw['server_profile']=='a'*32 and raw['https'] and raw['proxy']
                            operations.append({'id':'b'*32,'name':'Мой сервер','phase':'planned','message':'Просмотри изменения.',
                                'plan':{'sha256':'c'*64,'changes':[{'message':'Добавить отдельного пользователя и HTTPS-сайт; сохранить прежние сайты.'}]}})
                            return request.fulfill(json=operations[0])
                        if action=='install':
                            assert raw['accepted_sha256']=='c'*64
                            apply_count+=1
                            if apply_count==1:operations[0].update(phase='interrupted',message='Связь прервалась; продолжи сохранённый план.')
                            else:
                                operations[0].update(phase='installed',message='Службы настроены; проверь подключение.');operations[0].pop('plan');bridges.append(bridge)
                            return request.fulfill(json=operations[0])
                        if action=='check':return request.fulfill(json={'ok':True,'checks':[{'message':'HTTPS и прокси проверены (fixture).'}]})
                        if action=='retry':return request.fulfill(json={'ok':True})
                        if action=='add-existing':
                            assert raw['server_profile']=='a'*32 and raw['external']=='https://existing.example.org'
                            existing={**bridge,'id':'e'*32,'name':'Готовый сервер','external':raw['external']}
                            bridges.append(existing);return request.fulfill(json={'ok':True,'bridge':existing,'message':'Добавлено.'})
                        raise AssertionError((command,action))
                    page.route('**/*',route)
                    try:
                        page.goto(origin+'/tg/',wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                        if width<760:page.locator('#toggle-rail').click()
                        page.locator('[data-view="settings"]').click();page.get_by_role('tab',name='Программа',exact=True).click()
                        card=page.get_by_role('heading',name='Серверный мост',exact=True).locator('..')
                        card.get_by_role('button',name='Установить серверный мост',exact=True).click()
                        card.get_by_role('button',name='Настроить подключение',exact=True).click()
                        card.get_by_label('Адрес сервера',exact=True).fill('fixture.example.org')
                        password=card.get_by_label('Пароль или SSH-ключ',exact=False)
                        password.evaluate("node=>{const data=new DataTransfer();data.setData('text','-----BEGIN OPENSSH PRIVATE KEY-----\\nfixture-only\\n-----END OPENSSH PRIVATE KEY-----');node.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}")
                        assert password.input_value()==''
                        card.get_by_role('button',name='Проверить и сохранить подключение',exact=True).click()
                        page.get_by_text('SHA256:fixture-pin',exact=True).wait_for()
                        assert len([row for row in calls if row[:2]==('server_connection','save')])==1
                        assert not profiles and not operations
                        card.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'trust-{width}.png'))
                        page.get_by_role('button',name='Отпечаток совпадает — продолжить',exact=True).click()
                        page.get_by_text('Подключение сохранено.',exact=True).wait_for()
                        card.get_by_role('switch',name='Постоянный HTTPS для Mini App',exact=True).click()
                        card.get_by_label('Домен для HTTPS',exact=True).fill('helene.example.org')
                        card.get_by_label('Название подключения',exact=True).fill('Мой сервер')
                        card.get_by_role('button',name='Проверить и подготовить',exact=True).click()
                        apply=card.get_by_role('button',name='Применить эти изменения',exact=True);apply.wait_for()
                        assert apply_count==0
                        apply.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'plan-{width}.png'))
                        apply.click();card.get_by_role('button',name='Продолжить этот план',exact=True).click()
                        card.get_by_role('button',name='Проверить соединение',exact=True).click()
                        card.get_by_role('button',name='Использовать для этого агента',exact=True).click()
                        page.get_by_role('button',name='Сохранить',exact=True).click();page.wait_for_timeout(180)
                        assert saves and saves[-1]['phone']['bridge_profile']==bridge['id'],saves
                        assert saves[-1]['phone']['mode']=='stable'
                        assert saves[-1]['network']['bridge_profile']==bridge['id']
                        assert saves[-1]['network']['proxy']['host']=='127.0.0.1'
                        assert apply_count==2
                        page.get_by_role('tab',name='Мозг и связь',exact=True).click()
                        phone=page.get_by_role('heading',name='Mini App Telegram',exact=True).locator('..')
                        phone.get_by_label('Постоянный HTTPS-адрес',exact=False).scroll_into_view_if_needed()
                        assert phone.get_by_label('Постоянный HTTPS-адрес',exact=False).input_value()==bridge['external']
                        assert phone.locator('select[aria-label="Подключение сервера для HTTPS"]').input_value()==bridge['id']
                        phone.get_by_text('HTTPS проверен',exact=True).wait_for()
                        page.screenshot(path=str(evidence/f'selected-{width}.png'))
                        network=page.get_by_role('heading',name='Сеть Telegram и Mini App',exact=True).locator('..')
                        network.get_by_label('Порт',exact=True).fill('8889')
                        page.get_by_role('button',name='Сохранить',exact=True).click();page.wait_for_timeout(180)
                        assert 'bridge_profile' not in saves[-1]['network']
                        assert saves[-1]['phone']['bridge_profile']==bridge['id']
                        page.reload(wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                        if width<760:page.locator('#toggle-rail').click()
                        page.locator('[data-view="settings"]').click();page.get_by_role('tab',name='Мозг и связь',exact=True).click()
                        phone=page.get_by_role('heading',name='Mini App Telegram',exact=True).locator('..')
                        assert phone.locator('select[aria-label="Подключение сервера для HTTPS"]').input_value()==bridge['id']
                        phone.get_by_role('button',name='Подключение сервера для HTTPS',exact=True).click()
                        page.get_by_role('option',name='Свой HTTPS-адрес без встроенного моста',exact=True).click()
                        phone.get_by_role('button',name='Подключение сервера для HTTPS',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · https://helene.example.org',exact=True).click()
                        page.get_by_role('button',name='Сохранить',exact=True).click();page.wait_for_timeout(180)
                        assert saves[-1]['network']['bridge_profile']==bridge['id']
                        page.get_by_role('tab',name='Программа',exact=True).click()
                        card=page.get_by_role('heading',name='Серверный мост',exact=True).locator('..')
                        card.locator('summary').filter(has_text='Как устроить').click()
                        with page.expect_download() as downloaded:card.get_by_role('button',name='Скачать полную инструкцию',exact=True).click()
                        manual=downloaded.value;assert manual.suggested_filename=='Helene-server-bridge.md'
                        assert 'Уже настроенный сервер' in Path(manual.path()).read_text(encoding='utf8')
                        before=len([row for row in calls if row[1] in ('prepare','install')])
                        card.get_by_role('button',name='Настроить подключение',exact=True).click()
                        card.get_by_role('button',name='Сервер уже настроен',exact=True).click()
                        card.get_by_role('button',name='Сохранённое подключение сервера',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · root@fixture.example.org',exact=True).click()
                        assert card.get_by_label('Адрес сервера',exact=True).is_disabled()
                        card.get_by_role('switch',name='Постоянный HTTPS для Mini App',exact=True).click()
                        card.get_by_label('Готовый HTTPS-адрес',exact=True).fill('https://existing.example.org')
                        card.get_by_role('button',name='Проверить и подготовить',exact=True).click()
                        card.get_by_role('button',name='Использовать для этого агента',exact=True).wait_for()
                        assert len([row for row in calls if row[1] in ('prepare','install')])==before
                        placement=page.get_by_role('heading',name='Размещение агента',exact=True).locator('..')
                        placement.get_by_role('button',name='Открыть управление сервером',exact=True).click()
                        placement.get_by_role('button',name='Обновить сервер',exact=True).wait_for()
                        placement.get_by_role('button',name='Сохранённое подключение сервера',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · root@fixture.example.org',exact=True).click()
                        page.screenshot(path=str(evidence/f'existing-{width}.png'))
                        if width<760:page.locator('#toggle-rail').click()
                        page.locator('[data-view="learn"]').click()
                        guide=page.locator('#connection-guide');guide.locator('[data-goal="bridge"]').click()
                        row=guide.get_by_role('heading',name='Мой сервер',exact=True).locator('..').filter(has=page.get_by_role('button',name='Проверить соединение',exact=True))
                        row.get_by_role('button',name='Проверить соединение',exact=True).click()
                        row.get_by_role('button',name='Использовать для этого агента',exact=True).click()
                        guide.get_by_role('button',name='Перезапустить движок',exact=True).wait_for()
                        assert saves[-1]['phone']['bridge_profile']==bridge['id']
                        assert saves[-1]['network']['bridge_profile']==bridge['id']
                        row.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'guide-{width}.png'))
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                        assert not errors,errors
                        results.append({'width':width,'trust_before_auth':True,'plan_review_resume':True,'both_routes_saved':True,'manual_proxy_detaches':True,
                            'reopen_saved_profile':True,'existing_server_no_install':True,'offline_manual':True,'persistent_management':True,'guide_saved':True,'errors':errors})
                    except BaseException:
                        page.screenshot(path=str(evidence/f'failure-{width}.png'))
                        (evidence/f'failure-{width}.txt').write_text(page.locator('body').inner_text(),encoding='utf8');raise
                    finally:context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close()
    (evidence/'PASS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);run(parser.parse_args().evidence)
