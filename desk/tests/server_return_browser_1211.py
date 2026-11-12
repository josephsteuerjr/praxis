"""Production MiniApp return screen with isolated controller replies; no SSH, no owner data.

Review before anything stops, project decisions, an interrupted download that
continues, the finished return and its full restart, and reopening an unfinished
return. Requires Playwright and the built desk/miniapp dist.
"""
import argparse
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from program_browser_1211 import ProgramFixture

PROJECTS = ['/srv/projects/site', '/srv/projects/archive']
SCOPE = {'ok': True, 'version': '2.7.0', 'local_version': '2.7.0', 'compatible': True, 'return_id': '1' * 16,
         'scope': {'roots': [
             {'kind': 'data', 'path': '/opt/helene/data', 'files': 5120, 'bytes': 734003200, 'git': [{'path': 'workspace/site/.git', 'kind': 'directory'}],
              'links': [{'path': 'workspace/mnt/site', 'target': PROJECTS[0], 'error': ''}], 'errors': [], 'special': [], 'empty_directories': 3},
             {'kind': 'code', 'path': '/opt/helene/tree', 'files': 812, 'bytes': 9437184, 'git': [], 'links': [], 'errors': [], 'special': [], 'empty_directories': 0},
             {'kind': 'application', 'path': '/opt/helene/app', 'files': 640, 'bytes': 20971520, 'git': [], 'links': [], 'errors': [], 'special': [], 'empty_directories': 0}],
             'projects': [{'path': PROJECTS[0], 'via': ['sandbox.mounts']}, {'path': PROJECTS[1], 'via': ['data:workspace/mnt/archive']}],
             'unavailable': [{'path': 'C:/Users/owner/Desktop', 'via': 'memory/.forge/tasks/x/task.json', 'reason': 'bound_project_requires_mapping'}]},
         'host_exclusions': [{'path': '.serverboot', 'reason': 'ключ окна и надзор принадлежат серверу'},
                             {'path': 'models', 'reason': 'модели голоса — снаряжение машины, скачиваются заново'}]}
DONE = {'ok': True, 'phase': 'done', 'restart': 'application', 'previous_data': 'C:/Fixture/data',
        'message': 'Агент вернулся: память, код и проекты с сервера на месте. Прежняя копия и архив сохранены. '
                   'Перезапусти программу и возобнови агента, когда проверишь.',
        'code': {'summary': 'правок агента в коде: 3; перенесено 2, слито 1, не легло 0', 'folder': 'workspace/return-2.7.0'}}


LOCAL = {**SCOPE, 'scope': {**SCOPE['scope'], 'projects': [{'path': 'C:/Users/owner/site', 'via': ['sandbox.mounts']}]}}


def forward(browser, origin, evidence, results):
    """The move to a server shows the same review; owner folders stay unless chosen."""
    config = copy.deepcopy(ProgramFixture.config)
    context = browser.new_context(viewport={'width': 390, 'height': 960}, reduced_motion='reduce')
    page = context.new_page()
    page.set_default_timeout(10000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(request):
        url = request.request.url
        if not url.startswith(origin):
            return request.abort()
        if urlsplit(url).path != '/api/program/invoke':
            return request.continue_()
        body = request.request.post_data_json
        command, args = body['command'], body.get('arguments', {})
        if command == 'config_load':
            return request.fulfill(json={'config': config, 'mtime_ns': '1', 'tree': 'C:/Fixture/data',
                                         'path': 'C:/Fixture/helene.json', 'exe_dir': 'C:/Fixture'})
        if command == 'server_connection':
            return request.fulfill(json=[])
        if command == 'server_transfer':
            action = args['action']
            if action == 'transfer-scope':
                return request.fulfill(json=LOCAL)
            if action == 'status':
                return request.fulfill(json={'ok': True, 'pending': False, 'message': '', 'return': {'returned': False, 'phase': None}})
            raise AssertionError(action)
        return request.continue_()
    page.route('**/*', route)
    try:
        page.goto(origin + '/tg/', wait_until='networkidle')
        page.wait_for_selector('#app:not([hidden])')
        page.locator('#toggle-rail').click()
        page.locator('[data-view="settings"]').click()
        page.get_by_role('heading', name='Имена', exact=True).first.wait_for()
        tab = page.get_by_role('tab', name='Программа', exact=True)
        if tab.count():
            tab.click()
        placement = page.get_by_role('heading', name='Размещение агента', exact=True).locator('..')
        placement.get_by_role('button', name='Открыть управление сервером', exact=True).click()
        heading = placement.get_by_role('heading', name='Поедет на сервер', exact=True)
        heading.wait_for()
        switch = placement.get_by_role('switch', name='C:/Users/owner/site', exact=True)
        assert switch.get_attribute('aria-checked') == 'false'
        assert not placement.get_by_role('button', name='Вернуть агента с сервера', exact=True).is_visible()
        heading.scroll_into_view_if_needed()
        page.screenshot(path=str(evidence / 'forward-review-390.png'))
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert not errors, errors
        results.append({'width': 390, 'forward_review': True, 'owner_folders_default_left': True, 'errors': errors})
    except BaseException:
        page.screenshot(path=str(evidence / 'failure-forward-390.png'))
        raise
    finally:
        context.close()


def run(evidence):
    from playwright.sync_api import sync_playwright
    evidence = Path(evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), ProgramFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    results = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            try:
                forward(browser, origin, evidence, results)
                for width, unfinished in ((390, False), (820, True)):
                    config = copy.deepcopy(ProgramFixture.config)
                    config.update(mode='remote', base='https://agent.example.org', key='window-key')
                    context = browser.new_context(viewport={'width': width, 'height': 960}, reduced_motion='reduce')
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    errors, calls, restarts = [], [], []
                    state = {'phase': 'snapshot' if unfinished else None, 'returns': 0}
                    page.on('pageerror', lambda error: errors.append(str(error)))

                    def route(request):
                        url = request.request.url
                        if not url.startswith(origin):
                            return request.abort()
                        if urlsplit(url).path != '/api/program/invoke':
                            return request.continue_()
                        body = request.request.post_data_json
                        command, args = body['command'], body.get('arguments', {})
                        if command == 'config_load':
                            return request.fulfill(json={'config': config, 'mtime_ns': '1', 'tree': 'C:/Fixture/data',
                                                         'path': 'C:/Fixture/helene.json', 'exe_dir': 'C:/Fixture'})
                        if command == 'restart_self':
                            restarts.append(args)
                            return request.fulfill(json={'ok': True})
                        if command == 'server_connection':
                            return request.fulfill(json=[])
                        if command != 'server_transfer':
                            return request.continue_()
                        action, raw = args['action'], json.loads(args.get('request') or '{}')
                        calls.append((action, raw))
                        if action == 'status':
                            back = {'returned': False, 'phase': state['phase'],
                                    'projects': [{'path': PROJECTS[0], 'bring': True}, {'path': PROJECTS[1], 'bring': False}] if unfinished else [],
                                    'progress': {'done': 314572800, 'total': 734003200} if state['phase'] == 'snapshot' else None}
                            return request.fulfill(json={'ok': True, 'pending': True, 'phase': 'ready', 'host': 'fixture.example.org',
                                                         'login': 'root', 'ssh_port': 22, 'message': '',
                                                         'deployment': {'directory': '/opt/helene', 'project': 'helene', 'container': 'helene', 'port': 8094},
                                                         'return': back})
                        if action == 'return-scope':
                            assert raw['expected_config'] == 'C:/Fixture/helene.json' and raw['host'] == 'fixture.example.org'
                            return request.fulfill(json=SCOPE)
                        if action == 'return':
                            state['returns'] += 1
                            if not unfinished and state['returns'] == 1:
                                state['phase'] = 'snapshot'
                                return request.fulfill(json={'ok': False, 'local_data_kept': True,
                                                             'message': 'Связь с сервером оборвалась при скачивании. Повтори возврат: загрузка продолжится с места обрыва.'})
                            state['phase'] = None
                            return request.fulfill(json=DONE)
                        raise AssertionError((command, action))
                    page.route('**/*', route)
                    try:
                        page.goto(origin + '/tg/', wait_until='networkidle')
                        page.wait_for_selector('#app:not([hidden])')
                        if width < 760:
                            page.locator('#toggle-rail').click()
                        page.locator('[data-view="settings"]').click()
                        # A window watching a server agent lists every card without the Programme tab.
                        page.get_by_role('heading', name='Имена', exact=True).first.wait_for()
                        tab = page.get_by_role('tab', name='Программа', exact=True)
                        if tab.count():
                            tab.click()
                        placement = page.get_by_role('heading', name='Размещение агента', exact=True).locator('..')
                        placement.get_by_role('button', name='Открыть управление сервером', exact=True).click()
                        placement.get_by_role('button', name='Вернуть агента с сервера', exact=True).click()
                        screen = placement.get_by_role('heading', name='Вернуть агента на этот компьютер', exact=True).locator('..')
                        screen.wait_for()
                        page.wait_for_function("document.querySelector('input')&&[...document.querySelectorAll('input')].some(i=>i.value==='fixture.example.org')")
                        if unfinished:
                            screen.get_by_text('Найден незавершённый возврат', exact=False).wait_for()
                            proceed = screen.get_by_role('button', name='Продолжить возврат', exact=True)
                            assert proceed.is_enabled()
                            assert screen.get_by_role('button', name='Отменить незавершённый возврат', exact=True).is_visible()
                            screen.scroll_into_view_if_needed()
                            page.screenshot(path=str(evidence / f'unfinished-{width}.png'))
                            proceed.click()
                            screen.get_by_text('правок агента в коде: 3', exact=False).wait_for()
                            sent = [row for row in calls if row[0] == 'return'][-1][1]
                            assert sent['projects'] == [{'path': PROJECTS[0], 'bring': True}, {'path': PROJECTS[1], 'bring': False}]
                        else:
                            assert screen.get_by_role('button', name='Вернуть агента', exact=True).is_disabled()
                            screen.get_by_role('button', name='Проверить, что вернётся', exact=True).click()
                            screen.get_by_text('Память, навыки и рабочая папка — файлов 5120', exact=False).wait_for()
                            screen.get_by_text('проект привязан к другому устройству', exact=False).wait_for(state='attached')
                            assert [row for row in calls if row[0] in ('return',)] == []
                            screen.get_by_role('switch', name=PROJECTS[1], exact=True).click()
                            screen.scroll_into_view_if_needed()
                            page.screenshot(path=str(evidence / f'review-{width}.png'))
                            screen.get_by_role('button', name='Вернуть агента', exact=True).click()
                            screen.get_by_text('Связь с сервером оборвалась', exact=False).wait_for()
                            sent = [row for row in calls if row[0] == 'return'][-1][1]
                            assert sent['projects'] == [{'path': PROJECTS[0], 'bring': True}, {'path': PROJECTS[1], 'bring': False}]
                            assert screen.get_by_role('button', name='Отменить незавершённый возврат', exact=True).is_visible()
                            page.screenshot(path=str(evidence / f'interrupted-{width}.png'))
                            screen.get_by_role('button', name='Продолжить возврат', exact=True).click()
                        screen.get_by_text('правок агента в коде: 3', exact=False).wait_for()
                        # MiniApp presentation: the phone cannot restart the computer's programme.
                        screen.get_by_text('Перезапусти программу на компьютере', exact=False).wait_for()
                        assert not screen.get_by_role('button', name='Перезапустить программу', exact=True).is_visible()
                        page.screenshot(path=str(evidence / f'done-{width}.png'))
                        assert restarts == []
                        assert not screen.get_by_text('Не получилось.', exact=True).count()
                        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
                        assert not errors, errors
                        results.append({'width': width, 'unfinished_reopen': unfinished, 'review_before_stop': not unfinished,
                                        'project_decisions_sent': True, 'interrupted_then_continued': not unfinished,
                                        'computer_restart_named': True, 'errors': errors})
                    except BaseException:
                        page.screenshot(path=str(evidence / f'failure-{width}.png'))
                        (evidence / f'failure-{width}.txt').write_text(page.locator('body').inner_text(), encoding='utf8')
                        raise
                    finally:
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
    (evidence / 'PASS.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence', required=True)
    run(parser.parse_args().evidence)
