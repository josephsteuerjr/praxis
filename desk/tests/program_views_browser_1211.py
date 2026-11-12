"""Every section of the full programme inside the production MiniApp build.

Walks each navigation view and each settings tab at phone (390) and tablet (820)
width: no horizontal page scroll, no page errors, a screenshot of each. A
computer-only action answers on the phone with its reason instead of acting on
the computer. Isolated controller fixture; no owner services or Telegram.
"""
import argparse
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from program_browser_1211 import ProgramFixture

REFUSAL = 'На компьютере: файл открывается там — C:/Fixture/data'


AGENTS = [{'id': 'base', 'name': 'Мира', 'port': 18765, 'enabled': True, 'conflict': '', 'base': True},
          {'id': 'second', 'name': 'Ника', 'port': 18766, 'enabled': True, 'conflict': '', 'base': False}]


def switching(browser, origin, evidence, results):
    """Two agents: the MiniApp switches its own channel; the computer's window is not asked."""
    context = browser.new_context(viewport={'width': 820, 'height': 900}, reduced_motion='reduce')
    page = context.new_page()
    page.set_default_timeout(10000)
    errors, boots, switches, gets = [], [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: gets.append(urlsplit(request.url).path) if request.method == 'GET' else None)

    def route(request):
        url = request.request.url
        if not url.startswith(origin):
            return request.abort()
        if urlsplit(url).path != '/api/program/invoke':
            return request.continue_()
        body = request.request.post_data_json
        command, args = body['command'], body.get('arguments', {})
        if command in ('program_bootstrap', 'switch_agent'):
            identity = args.get('id') if command == 'switch_agent' else (body.get('agent') or 'base')
            (switches if command == 'switch_agent' else boots).append(identity)
            name = next(row['name'] for row in AGENTS if row['id'] == identity)
            return request.fulfill(json={'agent': name, 'agent_id': identity, 'base': '/program/' + identity,
                                         'agents': AGENTS, 'local_agent': True})
        if command == 'agents_list':
            return request.fulfill(json={'agents': AGENTS, 'current': body.get('agent') or 'base'})
        return request.continue_()
    page.route('**/*', route)
    try:
        page.goto(origin + '/tg/', wait_until='networkidle')
        page.wait_for_selector('#app:not([hidden])')
        page.locator('.agent-current').click()
        page.locator('.agent-row', has_text='Ника').click()
        for _ in range(100):
            if boots and boots[-1] == 'second':
                break
            page.wait_for_timeout(100)
        page.wait_for_selector('#app:not([hidden])')
        page.wait_for_load_state('networkidle')
        assert switches == ['second'], switches
        assert boots[-1] == 'second', boots
        assert any(path.startswith('/program/second/api/') for path in gets), gets[-20:]
        page.locator('.agent-current', has_text='Ника').wait_for()
        page.screenshot(path=str(evidence / 'switched-820.png'))
        assert not errors, errors
        results.append({'width': 820, 'switch_agent': True, 'channel_after_reload': '/program/second', 'errors': errors})
    except BaseException:
        page.screenshot(path=str(evidence / 'failure-switch-820.png'))
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
                switching(browser, origin, evidence, results)
                for width in (390, 820):
                    config = copy.deepcopy(ProgramFixture.config)
                    context = browser.new_context(viewport={'width': width, 'height': 900}, reduced_motion='reduce')
                    page = context.new_page()
                    page.set_default_timeout(10000)
                    errors, refused, visited, patches = [], [], [], []
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
                        if command == 'appearance_read':
                            return request.fulfill(json={'values': {}, 'seen': 'seen-1'})
                        if command == 'appearance_patch':
                            patches.append(args)
                            return request.fulfill(json={'ok': True, 'values': args['patch'], 'seen': 'seen-' + str(len(patches) + 1)})
                        if command == 'open_path' and not str(args.get('path', '')).startswith('http'):
                            # What the session host answers for the MiniApp presentation.
                            refused.append(args['path'])
                            return request.fulfill(status=400, body=REFUSAL, content_type='text/plain; charset=utf-8')
                        return request.continue_()
                    page.route('**/*', route)

                    def overflow():
                        return page.evaluate('document.documentElement.scrollWidth>innerWidth+1')

                    def rail_open():
                        return page.evaluate("document.querySelector('#app').classList.contains('narrow-navigation')")

                    def open_navigation():
                        if width < 760 and not rail_open():
                            page.locator('#toggle-rail').click()
                            page.wait_for_timeout(200)
                    try:
                        page.goto(origin + '/tg/', wait_until='networkidle')
                        page.wait_for_selector('#app:not([hidden])')
                        open_navigation()
                        views = page.locator('[data-view]').evaluate_all('nodes=>[...new Set(nodes.map(n=>n.dataset.view))]')
                        assert len(views) >= 8, views
                        for view in views:
                            open_navigation()
                            page.locator(f'[data-view="{view}"]').first.click()
                            page.wait_for_timeout(400)
                            if width < 760 and rail_open():
                                page.locator('#toggle-rail').click()
                                page.wait_for_timeout(200)
                            page.screenshot(path=str(evidence / f'view-{view}-{width}.png'))
                            assert not overflow(), ('horizontal scroll', view)
                            visited.append(view)
                        open_navigation()
                        page.locator('[data-view="settings"]').first.click()
                        tabs = page.get_by_role('tab')
                        tabs.first.wait_for()
                        names = tabs.all_inner_texts()
                        for name in names:
                            page.get_by_role('tab', name=name, exact=True).click()
                            page.wait_for_timeout(300)
                            page.screenshot(path=str(evidence / f'settings-{len(visited)}-{width}.png'), full_page=False)
                            assert not overflow(), ('horizontal scroll', 'settings', name)
                            visited.append('settings:' + name)
                        page.get_by_role('tab', name='Вид', exact=True).click()
                        page.get_by_role('radio', name='Тёмная', exact=True).click()
                        for _ in range(50):
                            if patches:
                                break
                            page.wait_for_timeout(100)
                        # The owner's theme reaches the programme, with the read revision.
                        assert patches[0] == {'seen': 'seen-1', 'patch': {'frame.theme': 'dark'}}, patches
                        page.get_by_role('tab', name='Агент', exact=True).click()
                        page.get_by_role('button', name='Открыть папку', exact=True).first.click()
                        page.locator('#toast').get_by_text(REFUSAL, exact=True).wait_for()
                        page.screenshot(path=str(evidence / f'computer-only-{width}.png'))
                        assert refused == ['C:/Fixture/data'], refused
                        assert not errors, errors
                        results.append({'width': width, 'visited': visited, 'computer_only_reason_shown': True,
                                        'theme_saved_to_programme': True, 'errors': errors})
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
