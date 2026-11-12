"""Production full-program MiniApp context inspector; synthetic local data only."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
from program_browser_1211 import ProgramFixture

NAMES = [(datetime(2026,11,12,12,1,4,tzinfo=timezone.utc)-timedelta(seconds=i)).strftime('%Y%m%dT%H%M%S')+'-12345678.md' for i in range(65)]


class ContextFixture(ProgramFixture):
    seen = []
    slow = ''
    fail_page = False
    def do_GET(self):
        path=urlsplit(self.path)
        route=path.path
        if route.startswith('/program/'):
            route='/'+'/'.join(route.split('/')[3:])
        query=parse_qs(path.query)
        if route=='/api/chats':
            return self.reply([{'peer_id':'window','kind':'window','title':'Мира','messages':2},
                               {'peer_id':'window-abcdef12','kind':'window','title':'Проект Rocket','messages':2}])
        if route=='/api/shadow':
            return self.reply([{'stream':'dm-window','captures':65,'latest':NAMES[0]},
                               {'stream':'dm-window-abcdef12','captures':65,'latest':NAMES[1]},
                               {'stream':'dm-other','captures':1,'latest':NAMES[0]}])
        if route.startswith('/api/shadow/'):
            self.seen.append(route)
            if route.endswith('/captures'):
                if self.fail_page and query.get('before'):
                    return self.reply({'error':'paging offline'},503)
                start=NAMES.index(query['before'][0])+1 if query.get('before') else 0
                return self.reply(NAMES[start:start+30])
            if route.endswith('/capture'):
                name=query['name'][0]
                if self.slow==name:time.sleep(.8)
                result={'text':'# Теневая сборка\n\nСинтетический снимок '+name,'tools_status':'recorded',
                        'tools':[{'name':'computer','description':'<script>throw new Error("unescaped")</script>',
                                  'input_schema':{'type':'object','properties':{'timeout_ms':{'type':'integer'}}}},
                                 {'name':'python','input_schema':{'type':'object','properties':{'code':{'type':'string'}}}}]}
                if name==NAMES[-1]:result.update(tools=None,tools_status='not_recorded')
                return self.reply(result)
            if route.endswith('/metrics'):return self.reply([{'stream':route.split('/')[-2],'calls':1}])
            if route.endswith('/diff'):return self.reply({'zones':[],'diff':['synthetic diff']})
        super().do_GET()


def run(output):
    from playwright.sync_api import sync_playwright
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),ContextFixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    rows=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel='msedge',headless=True)
            for width in (390,820,1280):
                ctx=browser.new_context(viewport={'width':width,'height':900},timezone_id='Europe/Samara',reduced_motion='reduce')
                page=ctx.new_page();page.set_default_timeout(10000)
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/tg/',wait_until='networkidle')
                page.wait_for_selector('#app:not([hidden])')
                page.get_by_text('Проект Rocket',exact=True).first.wait_for(state='attached')
                if width<760:page.locator('#toggle-rail').click()
                page.locator('[data-view="frame"]').first.click()
                page.locator('[data-tool="computer"]').wait_for()
                if width<760 and page.evaluate("document.querySelector('#app').classList.contains('narrow-navigation')"):
                    page.locator('#toggle-rail').click()
                assert '16:01:04' in page.locator('#cap-new').inner_text()
                page.locator('[data-tool="computer"] summary').click()
                page.locator('[data-tool="computer"] pre').wait_for(state='visible')
                assert 'timeout_ms' in page.locator('[data-tool="computer"]').inner_text()
                assert not page.locator('#frame-out script').count()
                if not page.locator('[data-stream="dm-window-abcdef12"]').is_visible():
                    page.locator('#frame-stream-title').click()
                assert page.locator('[data-stream="dm-window-abcdef12"] span').first.inner_text().startswith('Проект Rocket')
                page.locator('[data-stream="dm-window-abcdef12"]').click()
                page.locator('[data-tool="computer"]').wait_for()
                assert page.locator('#frame-main h2').inner_text()=='Проект Rocket'
                ContextFixture.fail_page=True
                page.locator('#cap-more').click()
                page.locator('#cap-status [data-fail-retry]').wait_for()
                assert page.locator('#cap-more').inner_text()=='Ещё снимки'
                assert page.locator('#cap-new').evaluate('(node)=>node.options.length')==30
                ContextFixture.fail_page=False
                page.locator('#cap-status [data-fail-retry]').click()
                page.wait_for_function("document.querySelector('#cap-new').options.length===60")
                assert page.locator('#cap-status').inner_text()==''
                page.locator('#cap-more').click();page.wait_for_function("document.querySelector('#cap-new').options.length===65")
                ContextFixture.slow=NAMES[1]
                page.locator('#cap-prev').click();page.locator('#cap-prev').click()
                page.wait_for_timeout(1000)
                assert NAMES[2] in page.locator('#frame-out .md').inner_text()
                ContextFixture.slow=''
                page.locator('[data-tool="computer"] summary').click()
                assert not page.evaluate('document.documentElement.scrollWidth>innerWidth+1')
                page.locator('#frame-main').evaluate('(node)=>{node.closest(".view-content, .reading")?.scrollTo(0,0);node.scrollIntoView({block:"start"})}')
                page.screenshot(path=str(output/f'context-schemas-{width}.png'))
                page.locator('#cap-new').locator('..').locator('.paper-select-trigger').click()
                page.locator('.paper-select-option').last.click()
                page.get_by_text('У этого старого снимка схемы не сохранены.',exact=False).wait_for()
                page.screenshot(path=str(output/f'context-legacy-{width}.png'))
                assert not errors,errors
                rows.append({'width':width,'local_timezone':True,'paging':65,'paging_retry':True,'named_window_chat':True,'race':True,'schema_escaping':True,'legacy':True,'errors':errors})
                ctx.close()
            browser.close()
    finally:
        server.shutdown();server.server_close()
    (output/'PASS.json').write_text(json.dumps({'passed':True,'results':rows},indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'widths':len(rows),'screenshots':6}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    run(parser.parse_args().output)
