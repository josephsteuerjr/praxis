"""Production MiniApp/server settings with isolated SSH/install controller replies.

2.8.12 (owner, 20.11): «Сеть и прокси» is ① proxy sources (Hélène bridges) and
② routes, where Telegram and the ChatGPT relay each choose their own way out.
No «use for this agent» step, no manual refresh, no old Telegram gateway.
"""
import argparse
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from program_browser_1211 import ProgramFixture

KEY='-----BEGIN OPENSSH PRIVATE KEY-----\nfixture-only\n-----END OPENSSH PRIVATE KEY-----'
READY='Ниже, в «Кто через него ходит», выбери его для Telegram, реле или обоих и сохрани настройки.'


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
                    errors=[];calls=[];saves=[];profiles=[];bridges=[];operations=[];relay_checks=[];probes=[];violations=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    bridge={'id':'b'*32,'name':'Мой сервер','external':'https://helene.example.org','phase':'configured',
                            'ports':{'local_proxy_port':18888,'remote_proxy_port':8888,'local_app_port':18765,'remote_app_port':18766}}
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
                        if command=='relay_network_check':
                            relay_checks.append(config['relay']['proxy'].copy())
                            return request.fulfill(json={'ok':True,'http_status':401,'generation_sent':False})
                        if command=='local_proxy_probe':
                            probes.append(args);return request.fulfill(json={'ok':True,'state':'proxy','status':200,'port':args['port']})
                        if command=='component_install':installed=True;return request.fulfill(json={'ok':True,'state':'completed','restart_required':False})
                        if command=='components_status':return request.fulfill(json=[{'id':'server-bridge','ready':True,'installed':installed,'restart_required':False}])
                        if command not in ('server_connection','server_bridge'):return request.continue_()
                        action=args['action'];raw=json.loads(args.get('request') or '{}');calls.append((command,action,raw))
                        if command=='server_connection':
                            if action=='list':return request.fulfill(json=profiles)
                            if action=='remove':
                                assert not [row for row in bridges if row.get('server_profile')==raw['id']]
                                profiles[:]=[row for row in profiles if row['id']!=raw['id']]
                                return request.fulfill(json={'ok':True,'message':'Подключение удалено с этого компьютера. На сервере ничего не менялось.'})
                            if not raw.get('confirmed_host'):return request.fulfill(json={'ok':False,'trust_needed':True,'fingerprint':'SHA256:fixture-pin','message':'Сверь отпечаток сервера.'})
                            assert raw['fingerprint']=='SHA256:fixture-pin'
                            assert raw['password']==KEY
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
                                operations[0].update(phase='installed',bridge=bridge['id'],message='Службы настроены; проверь подключение.');operations[0].pop('plan');bridges.append(bridge)
                            return request.fulfill(json=operations[0])
                        if action=='check':return request.fulfill(json={'ok':True,'checks':[{'message':'HTTPS и прокси проверены (fixture).'}]})
                        if action=='retry':return request.fulfill(json={'ok':True})
                        if action=='remove':
                            # The backend refuses to forget a bridge that saved settings still use.
                            if raw['id'] in {(config.get(name) or {}).get('bridge_profile') for name in ('phone','network','relay')}:violations.append(('remove-used',raw['id']))
                            bridges[:]=[row for row in bridges if row['id']!=raw['id']]
                            return request.fulfill(json={'ok':True,'message':'Мост удалён с этого компьютера. На сервере ничего не менялось.'})
                        if action=='add-existing':
                            assert raw['verify'] is True,'the bridge is proven before it is saved'
                            if raw['ports'].get('local_proxy_port')=='8888':
                                return request.fulfill(json={'ok':False,'checks':[],'error_code':'local_port_unavailable',
                                    'message':'Локальный порт 8888 уже занят: там, вероятно, Tray Bridge или другой туннель. Оставь поле пустым, и Hélène выберет свободный порт сама.'})
                            if not raw['external']:
                                assert raw['server_profile']=='a'*32 and raw['ports']=={'local_proxy_port':'18889','remote_proxy_port':8888}
                                only_proxy={'id':'f'*32,'name':'Proxy only','external':'','server_profile':raw['server_profile'],'ports':{'local_proxy_port':18889,'remote_proxy_port':8888}}
                                bridges.append(only_proxy);return request.fulfill(json={'ok':True,'bridge':only_proxy,'checks':[{'message':'HTTPS-запрос в Telegram прошёл через этот прокси.'}],'message':'Мост проверен и сохранён.'})
                            assert raw['server_profile']=='a'*32 and raw['external']=='https://existing.example.org'
                            assert raw['ports']['local_proxy_port']=='','an empty local port lets the backend choose a free one'
                            existing={**bridge,'id':'e'*32,'name':'Готовый сервер','external':raw['external'],'server_profile':raw['server_profile'],
                                      'ports':{**bridge['ports'],'local_proxy_port':18890}}
                            bridges.append(existing);return request.fulfill(json={'ok':True,'bridge':existing,'checks':[{'message':'HTTPS и прокси проверены (fixture).'}],'message':'Мост проверен и сохранён.'})
                        raise AssertionError((command,action))
                    page.route('**/*',route)

                    def settings(tab='Мозг и связь'):
                        if width<760:page.locator('#toggle-rail').click()
                        page.locator('[data-view="settings"]').click();page.get_by_role('tab',name=tab,exact=True).click()
                    def save():
                        count=len(saves);page.get_by_role('button',name='Сохранить',exact=True).click()
                        for _ in range(50):
                            if len(saves)>count:return saves[-1]
                            page.wait_for_timeout(50)
                        raise AssertionError('settings were not saved')
                    def card():return page.get_by_role('heading',name='Сеть и прокси',exact=True).locator('..')
                    def sources():return card().locator('.network-sources')
                    def routes(title):return card().locator('section.network-route').filter(has=page.get_by_role('heading',name=title,exact=True))
                    def bridge_row(name):return sources().locator('section.bridge-row').filter(has=page.get_by_role('heading',name=name,exact=True))
                    UNCLIP="""node=>{const kept=[];for(let up=node.parentElement;up;up=up.parentElement){
                        kept.push([up,up.getAttribute('style')]);up.style.overflow='visible';up.style.height='auto';up.style.maxHeight='none';}
                        window.__kept=kept;}"""
                    def whole(name):
                        # The settings view scrolls inside itself: lift the clipping for one picture of the card.
                        card().evaluate(UNCLIP);card().screenshot(path=str(evidence/f'{name}-{width}.png'))
                        page.evaluate("()=>{for(const [node,style] of window.__kept)style===null?node.removeAttribute('style'):node.setAttribute('style',style)}")
                    def checked(radio):return radio.get_attribute('aria-checked')=='true'
                    def used(name,text):bridge_row(name).get_by_text(text,exact=True).wait_for()
                    telegram,relay='Telegram и Mini App','Реле ChatGPT'
                    try:
                        page.goto(origin+'/tg/',wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                        settings('Программа')
                        assert page.get_by_role('heading',name='Размещение агента',exact=True).is_visible()
                        assert not page.get_by_role('button',name='Добавить мост',exact=True).is_visible()
                        page.get_by_role('tab',name='Мозг и связь',exact=True).click()
                        # One card, two steps: where the proxy comes from, then who uses it.
                        card().get_by_role('heading',name='① Откуда прокси',exact=True).wait_for()
                        card().get_by_role('heading',name='② Кто через него ходит',exact=True).wait_for()
                        for gone in ('Обновить состояние серверов','Использовать для этого агента'):
                            assert page.get_by_role('button',name=gone,exact=True).count()==0,gone
                        assert page.get_by_role('switch',name='Telegram через сервер',exact=True).count()==0,'the old gateway stays hidden while it is off'
                        assert checked(routes(telegram).get_by_role('radio',name='Системные настройки',exact=False))
                        assert checked(routes(relay).get_by_role('radio',name='Системные настройки',exact=False))
                        # External Tray Bridge: a custom proxy in each route, filled by one button.
                        routes(telegram).get_by_role('radio',name='Свой прокси',exact=False).click()
                        routes(telegram).get_by_role('button',name='Подставить Tray Bridge · 127.0.0.1:8888',exact=True).click()
                        routes(telegram).get_by_text('Telegram и Mini App пойдут через 127.0.0.1:8888 после сохранения: нажми «Сохранить». Tray Bridge отвечает и пропускает HTTPS.',exact=True).wait_for()
                        assert probes==[{'port':8888}]
                        assert routes(telegram).get_by_label('Порт',exact=True).input_value()=='8888'
                        assert routes(telegram).get_by_label('Сервер',exact=True).input_value()=='127.0.0.1'
                        saved=save();assert saved['network']['proxy']['port']=='8888' and 'bridge_profile' not in saved['network']
                        routes(relay).get_by_role('radio',name='Свой прокси',exact=False).click()
                        routes(relay).get_by_role('button',name='Подставить Tray Bridge · 127.0.0.1:8888',exact=True).click()
                        routes(relay).get_by_text('Реле пойдёт через 127.0.0.1:8888 после сохранения: нажми «Сохранить». Tray Bridge отвечает и пропускает HTTPS.',exact=True).wait_for()
                        routes(telegram).get_by_role('button',name='Подставить Tray Bridge · 127.0.0.1:8888',exact=True).click()
                        routes(telegram).get_by_text('Для Telegram и Mini App уже указан 127.0.0.1:8888. Tray Bridge отвечает и пропускает HTTPS.',exact=True).wait_for()
                        check=routes(relay).get_by_role('button',name='Проверить сохранённое подключение',exact=True)
                        assert check.is_disabled() and not relay_checks
                        assert saves[-1]['relay']['proxy']['mode']=='system'
                        save();check.click()
                        routes(relay).get_by_text('ChatGPT доступен по сохранённому маршруту. Запрос модели не отправлялся.',exact=True).wait_for()
                        assert relay_checks==[{'mode':'proxy','protocol':'http','host':'127.0.0.1','port':'8888','username':'','password':''}]
                        card().scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'network-{width}.png'))

                        # ① A new bridge: automatic setup of the server.
                        sources().get_by_role('button',name='Добавить мост',exact=True).click()
                        assert installed,'adding a bridge enables the already bundled component'
                        setup=sources()
                        assert checked(setup.get_by_role('radio',name='Сервер уже настроен',exact=False))
                        assert not checked(setup.get_by_role('radio',name='Настроить сервер автоматически',exact=False))
                        assert setup.get_by_placeholder('~/.ssh/id_ed25519',exact=True).is_visible()
                        assert not setup.get_by_role('button',name='Проверить и сохранить подключение',exact=True).count(),'one primary action'
                        assert checked(setup.get_by_role('switch',name='Раздавать прокси сервера на этом ПК',exact=True))
                        setup.get_by_role('radio',name='Настроить сервер автоматически',exact=False).click()
                        assert checked(setup.get_by_role('radio',name='Настроить сервер автоматически',exact=False))
                        assert not checked(setup.get_by_role('radio',name='Сервер уже настроен',exact=False))
                        setup.get_by_label('Адрес сервера',exact=True).fill('fixture.example.org')
                        password=setup.get_by_label('Пароль или SSH-ключ',exact=False)
                        password.evaluate("(node,key)=>{const data=new DataTransfer();data.setData('text',key);node.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));}",KEY)
                        assert password.input_value()==''
                        setup.get_by_role('switch',name='Постоянный HTTPS для Mini App',exact=True).click()
                        setup.get_by_label('Домен для HTTPS',exact=True).fill('helene.example.org')
                        setup.get_by_label('Название подключения',exact=True).fill('Мой сервер')
                        setup.get_by_role('button',name='Проверить и подготовить',exact=True).click()
                        page.get_by_text('SHA256:fixture-pin',exact=True).wait_for()
                        setup.get_by_text('Сверь отпечаток сервера выше и подтверди его: проверка продолжится сама.',exact=True).wait_for()
                        assert len([row for row in calls if row[:2]==('server_connection','save')])==1
                        assert not profiles and not operations
                        setup.get_by_text('SHA256:fixture-pin',exact=True).scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'trust-{width}.png'))
                        page.get_by_role('button',name='Отпечаток совпадает — продолжить',exact=True).click()
                        apply=setup.get_by_role('button',name='Применить эти изменения',exact=True);apply.wait_for()
                        assert profiles,'the confirmed fingerprint saved the connection and continued to the plan'
                        assert apply_count==0
                        assert setup.get_by_role('button',name='Проверить и подготовить',exact=True).count()==0,'the plan takes the place of the form'
                        apply.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'plan-{width}.png'))
                        apply.click();setup.get_by_role('button',name='Продолжить этот план',exact=True).click()
                        mine=bridge_row('Мой сервер');mine.wait_for()
                        assert apply_count==2
                        assert sources().get_by_role('heading',name='Мой сервер',exact=True).count()==1,'a finished setup is shown once, as its bridge'
                        mine.get_by_text('Раздаёт прокси на этом ПК: 127.0.0.1:18888',exact=True).wait_for()
                        mine.get_by_text('HTTPS для Mini App: https://helene.example.org',exact=True).wait_for()
                        mine.get_by_text('Пока через него никто не ходит: выбери его ниже, в «Кто через него ходит».',exact=True).wait_for()
                        mine.get_by_role('button',name='Проверить соединение',exact=True).click()
                        mine.get_by_text('HTTPS и прокси проверены (fixture).',exact=False).wait_for()
                        # The first bridge in an empty list reaches both routes without a reload.
                        pick=routes(telegram).get_by_role('radio',name='Мост «Мой сервер»',exact=False);pick.wait_for()
                        assert routes(relay).get_by_role('radio',name='Мост «Мой сервер»',exact=False).count()==1
                        pick.get_by_text('127.0.0.1:18888 на этом ПК',exact=True).wait_for()

                        # ② Telegram through the bridge; relay keeps its own route.
                        pick.click();used('Мой сервер','Через него ходит: Telegram и Mini App')
                        saved=save()
                        assert saved['network']['bridge_profile']==bridge['id'] and saved['network']['proxy']['host']=='127.0.0.1' and saved['network']['proxy']['port']=='18888'
                        assert 'bridge_profile' not in saved['relay'] and saved['relay']['proxy']['port']=='8888'
                        assert 'bridge_profile' not in saved['phone'],'a proxy route does not require or enable HTTPS'
                        routes(relay).get_by_role('radio',name='Мост «Мой сервер»',exact=False).click()
                        used('Мой сервер','Через него ходит: Telegram и Mini App, Реле ChatGPT')
                        saved=save();assert saved['relay']['bridge_profile']==bridge['id'] and saved['relay']['proxy']['port']=='18888'
                        assert saved['relay']['port']==config['relay']['port']==5011
                        # Telegram leaves the bridge; the relay alone keeps it.
                        routes(telegram).get_by_role('radio',name='Свой прокси',exact=False).click()
                        routes(telegram).get_by_label('Порт',exact=True).fill('8889')
                        used('Мой сервер','Через него ходит: Реле ChatGPT')
                        saved=save();assert 'bridge_profile' not in saved['network'] and saved['network']['proxy']['port']=='8889'
                        assert saved['relay']['bridge_profile']==bridge['id'],'a relay-only route keeps its bridge'
                        card().scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'routes-{width}.png'))
                        whole('card-routes')

                        # Mini App HTTPS is chosen in the Mini App card and shows in the bridge row.
                        phone=page.get_by_role('heading',name='Mini App Telegram',exact=True).locator('..')
                        phone.get_by_role('button',name='Подключение Mini App',exact=True).click()
                        page.get_by_role('option',name='Постоянный HTTPS-сервер',exact=True).click()
                        phone.get_by_role('button',name='Подключение сервера для HTTPS',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · https://helene.example.org',exact=True).click()
                        phone.get_by_text('Подключение выбрано. Сохрани настройки.',exact=True).wait_for()
                        used('Мой сервер','Через него ходит: Реле ChatGPT, HTTPS для Mini App')
                        saved=save()
                        assert saved['phone']['bridge_profile']==bridge['id'] and saved['phone']['mode']=='stable' and saved['phone']['external']==bridge['external']
                        assert 'bridge_profile' not in saved['network'],'choosing Mini App HTTPS does not reroute Telegram'
                        phone.get_by_text('HTTPS проверен',exact=True).wait_for()
                        phone.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'selected-{width}.png'))

                        # The saved choices come back after a reload.
                        page.reload(wait_until='networkidle');page.wait_for_selector('#app:not([hidden])')
                        settings()
                        assert checked(routes(telegram).get_by_role('radio',name='Свой прокси',exact=False))
                        assert routes(telegram).get_by_label('Порт',exact=True).input_value()=='8889'
                        assert checked(routes(relay).get_by_role('radio',name='Мост «Мой сервер»',exact=False))
                        assert not routes(relay).get_by_role('button',name='Проверить сохранённое подключение',exact=True).is_disabled(),'a bridge address learned on load is not an edit'
                        used('Мой сервер','Через него ходит: Реле ChatGPT, HTTPS для Mini App')
                        phone=page.get_by_role('heading',name='Mini App Telegram',exact=True).locator('..')
                        assert phone.locator('select[aria-label="Подключение сервера для HTTPS"]').input_value()==bridge['id']

                        # The manual follows the new layout.
                        sources().locator('summary').filter(has_text='Как устроить').click()
                        with page.expect_download() as downloaded:sources().get_by_role('button',name='Скачать полную инструкцию',exact=True).click()
                        manual=downloaded.value;assert manual.suggested_filename=='Helene-server-bridge.md'
                        text=Path(manual.path()).read_text(encoding='utf8')
                        assert 'Мозг и связь → Сеть и прокси → ① Откуда прокси → Добавить мост' in text
                        assert 'Использовать для этого агента' not in text and '② Кто через него ходит' in text

                        # A server that is already set up: no installation, a free local port.
                        before=len([row for row in calls if row[1] in ('prepare','install')])
                        sources().get_by_role('button',name='Добавить мост',exact=True).click()
                        setup=sources()
                        setup.get_by_role('button',name='Сохранённое подключение сервера',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · root@fixture.example.org',exact=True).click()
                        assert setup.get_by_label('Адрес сервера',exact=True).is_disabled()
                        setup.get_by_role('switch',name='Постоянный HTTPS для Mini App',exact=True).click()
                        setup.get_by_label('Готовый HTTPS-адрес',exact=True).fill('https://existing.example.org')
                        setup.get_by_role('button',name='Подключить и проверить',exact=True).click()
                        setup.get_by_text('Мост раздаёт 127.0.0.1:18890. '+READY,exact=False).wait_for()
                        bridge_row('Готовый сервер').get_by_text('Раздаёт прокси на этом ПК: 127.0.0.1:18890',exact=True).wait_for()
                        assert setup.get_by_label('Готовый HTTPS-адрес',exact=True).count()==0,'a saved bridge leaves only its result line'
                        routes(telegram).get_by_role('radio',name='Мост «Готовый сервер»',exact=False).wait_for()
                        assert len([row for row in calls if row[1] in ('prepare','install')])==before

                        # Proxy only, through Tray Bridge's port first: refused, nothing saved.
                        sources().get_by_role('button',name='Добавить мост',exact=True).click()
                        setup=sources()
                        setup.get_by_role('button',name='Сохранённое подключение сервера',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · root@fixture.example.org',exact=True).click()
                        setup.locator('details').filter(has=page.get_by_label('Локальный порт прокси',exact=False)).locator('summary').click()
                        local=setup.get_by_label('Локальный порт прокси',exact=False)
                        assert local.input_value()=='' and local.get_attribute('placeholder')=='авто'
                        before_bridges=len(bridges)
                        local.fill('8888');setup.get_by_role('button',name='Подключить и проверить',exact=True).click()
                        setup.get_by_text('Локальный порт 8888 уже занят',exact=False).wait_for()
                        assert len(bridges)==before_bridges,'a refused port leaves no bridge record'
                        local.fill('18889');setup.get_by_role('button',name='Подключить и проверить',exact=True).click()
                        setup.get_by_text('Мост раздаёт 127.0.0.1:18889. '+READY,exact=False).wait_for()
                        only=bridge_row('Proxy only');only.get_by_text('Раздаёт прокси на этом ПК: 127.0.0.1:18889',exact=True).wait_for()
                        assert only.get_by_text('HTTPS для Mini App',exact=False).count()==0
                        assert len([row for row in calls if row[1] in ('prepare','install')])==before
                        routes(telegram).get_by_role('radio',name='Мост «Proxy only»',exact=False).click()
                        used('Proxy only','Через него ходит: Telegram и Mini App')
                        saved=save()
                        assert saved['network']['bridge_profile']=='f'*32 and saved['network']['proxy']['port']=='18889'
                        assert saved['relay']['bridge_profile']==bridge['id'] and saved['relay']['proxy']['port']=='18888'
                        assert saved['relay']['port']==5011
                        only.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'proxy-only-{width}.png'))
                        whole('card-sources')

                        # Forget an unused bridge: gone from the list and from both routes.
                        ready=bridge_row('Готовый сервер')
                        ready.get_by_text('Пока через него никто не ходит: выбери его ниже, в «Кто через него ходит».',exact=True).wait_for()
                        ready.get_by_role('button',name='Удалить мост',exact=True).click()
                        ready.get_by_role('button',name='Точно удалить? Нажми ещё раз',exact=True).click()
                        sources().get_by_text('Мост удалён с этого компьютера. На сервере ничего не менялось.',exact=True).wait_for()
                        assert not [row for row in bridges if row['id']=='e'*32]
                        assert bridge_row('Готовый сервер').count()==0
                        routes(telegram).get_by_role('radio',name='Мост «Proxy only»',exact=False).wait_for()
                        page.wait_for_function("()=>![...document.querySelectorAll('section.network-route [role=radio]')].some(node=>node.textContent.includes('Готовый сервер'))")

                        page.get_by_role('tab',name='Программа',exact=True).click()
                        placement=page.get_by_role('heading',name='Размещение агента',exact=True).locator('..')
                        placement.get_by_role('button',name='Открыть управление сервером',exact=True).click()
                        placement.get_by_role('button',name='Обновить сервер',exact=True).wait_for()
                        placement.get_by_role('button',name='Сохранённое подключение сервера',exact=True).click()
                        page.get_by_role('option',name='Мой сервер · root@fixture.example.org',exact=True).click()
                        page.screenshot(path=str(evidence/f'existing-{width}.png'))

                        # Onboarding guide: a checked bridge is applied to the chosen routes.
                        if width<760:page.locator('#toggle-rail').click()
                        page.locator('[data-view="learn"]').click()
                        guide=page.locator('#connection-guide');guide.locator('[data-goal="bridge"]').click()
                        row=guide.locator('section.bridge-row').filter(has=page.get_by_role('heading',name='Мой сервер',exact=True))
                        row.get_by_role('button',name='Проверить соединение',exact=True).click()
                        assert checked(row.get_by_role('switch',name=telegram,exact=True))
                        assert not checked(row.get_by_role('switch',name=relay,exact=True))
                        row.get_by_role('button',name='Применить',exact=True).click()
                        guide.get_by_role('button',name='Перезапустить движок',exact=True).wait_for()
                        assert saves[-1]['network']['bridge_profile']==bridge['id'] and saves[-1]['network']['proxy']['port']=='18888'
                        assert saves[-1]['phone']['bridge_profile']==bridge['id']
                        row=guide.locator('section.bridge-row').filter(has=page.get_by_role('heading',name='Proxy only',exact=True))
                        row.get_by_role('button',name='Проверить соединение',exact=True).click()
                        row.get_by_role('switch',name=telegram,exact=True).click()
                        assert row.get_by_role('button',name='Применить',exact=True).is_disabled(),'nothing chosen, nothing to apply'
                        row.get_by_role('switch',name=relay,exact=True).click()
                        row.get_by_role('button',name='Применить',exact=True).click()
                        row.get_by_text('Маршрут реле сохранён: реле подхватит его само, перезапуск не нужен.',exact=False).wait_for()
                        assert saves[-1]['relay']['bridge_profile']=='f'*32 and saves[-1]['relay']['proxy']['port']=='18889'
                        assert saves[-1]['network']['bridge_profile']==bridge['id'],'the relay alone does not move Telegram'
                        assert saves[-1]['phone']['bridge_profile']==bridge['id'],'a proxy-only bridge leaves the phone HTTPS alone'
                        row.scroll_into_view_if_needed();page.screenshot(path=str(evidence/f'guide-{width}.png'))
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                        assert not violations,violations
                        assert not errors,errors
                        results.append({'width':width,'sources_and_routes':True,'tray_custom_routes':True,'check_after_save':True,'trust_before_auth':True,
                            'plan_review_resume':True,'first_bridge_reaches_routes':True,'telegram_route':True,'relay_only_route':True,'mini_app_https_separate':True,
                            'usage_shown':True,'reload_keeps_choices':True,'manual_current':True,'existing_server_no_install':True,'refused_port_no_record':True,
                            'remove_unused':True,'persistent_management':True,'guide_targets':True,'errors':errors})
                    except BaseException:
                        page.screenshot(path=str(evidence/f'failure-{width}.png'))
                        (evidence/f'failure-{width}.txt').write_text(page.locator('body').inner_text(),encoding='utf8');raise
                    finally:context.close()
            finally:browser.close()
    finally:server.shutdown();server.server_close()
    (evidence/'PASS.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);run(parser.parse_args().evidence)
