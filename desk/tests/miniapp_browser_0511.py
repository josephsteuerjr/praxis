"""Real production-bundle browser acceptance with an inert Telegram SDK/API.

Requires Playwright and built desk/{miniapp,mobile}/dist. No real Telegram,
model, profile, bot, cloudflared or owner installation. Example:
python desk/tests/miniapp_browser_0511.py --evidence <directory> --channel msedge
This proves browser emulation, never physical iPhone/Android acceptance.
"""
import argparse
import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

DESK = Path(__file__).resolve().parents[1]
SDK = r'''
window.__sdkCalls=[];window.__handlers={};window.__browserFullscreen=0;
Element.prototype.requestFullscreen=function(){window.__browserFullscreen++;throw Error('Browser fullscreen forbidden')};
window.__visual=new EventTarget();Object.assign(window.__visual,{height:innerHeight,offsetTop:0});
Object.defineProperty(window,'visualViewport',{value:window.__visual});
const options=window.__scenario||{};
if(options.sdk!==false){
  const emit=(name)=>{for(const fn of window.__handlers[name]||[])fn()};
  window.__emit=emit;
  const tg=window.Telegram={WebApp:{
    initData:options.auth===false?'':'fixture-init-data',colorScheme:options.theme||'light',
    viewportHeight:innerHeight,isFullscreen:false,
    safeAreaInset:options.safe||{top:47,bottom:34,left:0,right:0},
    contentSafeAreaInset:options.content||{top:44,bottom:0,left:0,right:0},
    isVersionAtLeast:v=>options.legacy!==true,
    ready:()=>window.__sdkCalls.push('ready'),expand:()=>window.__sdkCalls.push('expand'),
    setHeaderColor:c=>window.__sdkCalls.push(['header',c]),
    requestFullscreen:()=>{window.__sdkCalls.push('fullscreen');if(options.throwFullscreen)throw Error('unsupported');
      if(options.denyFullscreen){setTimeout(()=>emit('fullscreenFailed'),0);return}
      window.Telegram.WebApp.isFullscreen=true;setTimeout(()=>emit('fullscreenChanged'),0)},
    onEvent:(name,fn)=>(window.__handlers[name]??=[]).push(fn),
    BackButton:{show:()=>window.__sdkCalls.push('back-show'),hide:()=>window.__sdkCalls.push('back-hide'),onClick:fn=>window.__back=fn}
  }};
}
'''


class Fixture(BaseHTTPRequestHandler):
    requests = []
    def log_message(self,*args): pass
    def reply(self,value,status=200,kind='application/json'):
        body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        path=urlsplit(self.path).path;self.requests.append(('GET',path))
        if path=='/fixture-sdk.js':return self.reply(SDK.encode(),kind='text/javascript')
        if path.endswith('/config.js'):return self.reply(b'window.DESK_CONFIG={agent:"Fixture"};',kind='text/javascript')
        if path=='/api/state':return self.reply({'agent':'Мира · проверка Mini App','owner':'Владелец','level':'ok','phrase':'На связи',
            'runner':{'alive':True,'busy':False,'run':'','since':0},'brain':{'last_call_at':time.time()},'anatomy':False})
        if path=='/api/runs':return self.reply([])
        if path=='/api/chats':return self.reply([{'peer_id':'123','title':'Владелец','kind':'telegram','messages':2}])
        if path.startswith('/api/chat/'):
            return self.reply([{'timestamp':'2026-11-05T02:00:00Z','text':'Проверяем текущий вход через бота.','sender_name':'Владелец'},
                {'timestamp':'2026-11-05T02:00:10Z','text':'Я на связи. Здесь та же переписка и действия агента. '+('Бумажный стиль и ровные отступы. '*6),'outgoing':True}])
        if path.startswith('/api/chat-turns/'):return self.reply([])
        if path=='/api/usage':return self.reply({'status':'unavailable','reason':'Fixture: счётчик не подключён'})
        if path=='/api/allowances':return self.reply({'providers':[]})
        if path=='/api/phone/subscription':return self.reply({'available':False,'note':'Fixture: подписки отключены'})
        if path=='/api/phone/settings':return self.reply({'groups':[['agent','Агент'],['telegram','Связь'],['look','Оформление']],
            'fields':[{'path':'agent.name','label':'Имя агента','group':'agent','section':'Агент','type':'text','value':'Мира'},
                      {'path':'telegram.owner_id','label':'Владелец','group':'telegram','section':'Telegram','type':'text','value':'123'}],
            'mtime_ns':'1','scope_id':'fixture','agent':'Мира'})
        if path=='/api/phone/settings/receipt':return self.reply({})
        if path=='/events':return self.reply(b'data: {"t":"health"}\n\n',kind='text/event-stream')
        if path=='/api/version':return self.reply({'version':'2.3.0','stamp':'fixture'})
        if path.startswith('/api/'):return self.reply({})
        for prefix,front in [('/tg/','miniapp'),('/m/','mobile')]:
            if path.startswith(prefix):
                relative=path[len(prefix):] or 'index.html';root=(DESK/front/'dist').resolve()
                file=(root/relative).resolve()
                if not file.is_relative_to(root) or not file.is_file():return self.reply({},404)
                data=file.read_bytes()
                if file.name=='index.html':data=data.replace(b'https://telegram.org/js/telegram-web-app.js',b'/fixture-sdk.js')
                return self.reply(data,kind=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        self.reply({},404)
    def do_POST(self):
        path=urlsplit(self.path).path;body=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))) or '{}')
        self.requests.append(('POST',path,body))
        if path=='/pair/telegram':return self.reply({'key':'dk-fixture-device-key','agent':'Мира'})
        if path=='/api/say':return self.reply({'ok':True})
        self.reply({'ok':True,'mtime_ns':'2'})


def run(args):
    from playwright.sync_api import sync_playwright
    evidence=Path(args.evidence).resolve();evidence.mkdir(parents=True,exist_ok=True)
    server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_port}'
    checks=[];shots=[]
    def check(ok,name):
        if not ok:raise AssertionError(name)
        checks.append(name)
    def snapshot(page,name):
        path=evidence/(name+'.png');page.screenshot(path=str(path));shots.append(str(path))
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(channel=None if args.executable_path else args.channel,
                                      executable_path=args.executable_path,headless=True)
            try:
                def open_page(width,height,scenario=None,path='/tg/',denied=False):
                    context=browser.new_context(viewport={'width':width,'height':height},reduced_motion='reduce')
                    context.add_init_script('window.__scenario='+json.dumps(scenario or {})+';localStorage.setItem("frame.tg.device","dk-old-account");localStorage.setItem("helene.look",JSON.stringify({uiScale:window.__scenario.scale||1}));')
                    context.add_cookies([{'name':'desk_key','value':'dk-old-account','url':origin}])
                    page=context.new_page();errors=[];external=[]
                    page.set_default_timeout(6000)
                    page.on('pageerror',lambda err:errors.append(str(err)))
                    def route(request):
                        if not request.request.url.startswith(origin+'/'):
                            external.append(request.request.url);request.abort()
                        elif denied and request.request.url.endswith('/pair/telegram'):
                            request.fulfill(status=403,body='wrong owner')
                        else:request.continue_()
                    page.route('**/*',route)
                    page.goto(origin+path,wait_until='networkidle')
                    page.evaluate('document.fonts.ready');page.wait_for_timeout(100)
                    return context,page,errors,external

                def geometry(page,name):
                    data=page.evaluate('''() => {
                      const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height}};
                      const h=window.__visual.height,o=window.__visual.offsetTop;
                      const t=Telegram.WebApp,s=t.safeAreaInset,c=t.contentSafeAreaInset;
                      return {app:rect('#app'),top:rect('#top'),tabs:rect('#tabs'),screen:rect('#screen'),
                        viewport:h,offset:o,minTop:o+(s.top||0)+(c.top||0),maxBottom:o+h-(s.bottom||0)-(c.bottom||0),
                        minLeft:(s.left||0)+(c.left||0),maxRight:innerWidth-(s.right||0)-(c.right||0),
                        labels:[...document.querySelectorAll('.tab > span')].every(span=>span.scrollWidth<=span.clientWidth+1),
                        style:document.documentElement.getAttribute('style'),overflow:document.documentElement.scrollWidth>innerWidth,browserFullscreen:window.__browserFullscreen};
                    }''')
                    check(data['top']['top']>=data['minTop']-.5,name+': header below Telegram/system safe areas')
                    check(data['tabs']['bottom']<=data['viewport']+data['offset']+.5,name+': navigation inside visible viewport '+repr(data))
                    check(data['top']['left']>=data['minLeft']-.5 and data['top']['right']<=data['maxRight']+.5,name+': landscape side insets respected')
                    check(data['screen']['height']>50 and not data['overflow'],name+': scroll surface remains usable without horizontal overflow')
                    check(data['labels'],name+': navigation labels wrap inside equal buttons')
                    check(data['browserFullscreen']==0,name+': browser fullscreen unused')
                    return data

                for name,w,h,scenario in [
                    ('portrait',390,844,{}),
                    ('narrow',320,568,{'safe':{'top':24,'bottom':16},'content':{'top':40}}),
                    ('wide',1280,800,{'safe':{},'content':{'top':32}}),
                    ('landscape',844,390,{'safe':{'top':0,'left':44,'right':44,'bottom':21},'content':{'top':32}}),
                    ('dark',390,844,{'theme':'dark'}),
                    ('scaled',390,844,{'scale':1.25}),
                    ('legacy',390,844,{'legacy':True,'safe':{},'content':{}}),
                    ('fullscreen-denied',390,844,{'denyFullscreen':True}),
                    ('fullscreen-throws',390,844,{'throwFullscreen':True}),
                ]:
                    context,page,errors,external=open_page(w,h,scenario)
                    try:
                        check(page.locator('#pair').is_hidden(),name+': authenticated renderer entered')
                        geometry(page,name)
                        calls=page.evaluate('window.__sdkCalls')
                        check(('fullscreen' in calls)==(not scenario.get('legacy',False)),name+': version-gated Telegram requestFullscreen')
                        if scenario.get('denyFullscreen') or scenario.get('throwFullscreen'):
                            check(page.locator('html').get_attribute('data-fullscreen')=='unavailable',name+': graceful expanded fallback')
                        elif not scenario.get('legacy'):
                            check(page.locator('html').get_attribute('data-fullscreen')=='active',name+': fullscreenChanged observed')
                        page.locator('[data-tab="chats"]').click()
                        page.locator('.room').filter(has_text='Владелец').click()
                        page.locator('#say').fill('Проверка компоновки без отправки в Telegram')
                        if name=='portrait':
                            snapshot(page,'portrait-chat')
                            page.evaluate('''() => {window.__visual.height=460;window.__visual.dispatchEvent(new Event('resize'));}''')
                            page.wait_for_timeout(80);geometry(page,'keyboard-visual-only')
                            check(page.locator('#composer').bounding_box()['y']+page.locator('#composer').bounding_box()['height']<=460,'keyboard: composer remains visible with stale SDK height')
                            snapshot(page,'portrait-keyboard')
                            page.evaluate('''() => {window.__visual.height=innerHeight;Telegram.WebApp.viewportHeight=innerHeight;window.__emit('viewportChanged');}''')
                            page.wait_for_timeout(60)
                            page.evaluate('window.__back()');page.wait_for_timeout(60)
                            check(page.locator('#composer').is_hidden(),'Telegram BackButton returns to chats')
                            page.locator('[data-tab="system"]').click();page.locator('[data-open-agent-settings]').click()
                            page.wait_for_timeout(100)
                            check(page.locator('.phone-dialog').is_visible(),'settings use the real shared dialog')
                            dialog=page.locator('.phone-dialog').bounding_box()
                            check(dialog['y']>=91 and dialog['y']+dialog['height']<=810,'settings: close/save remain within safe frame')
                            snapshot(page,'portrait-settings')
                            page.locator('.phone-settings input').first.focus()
                            page.evaluate('''() => {window.__visual.height=460;window.__visual.offsetTop=12;window.__visual.dispatchEvent(new Event('resize'));}''')
                            page.wait_for_timeout(80)
                            dialog=page.locator('.phone-dialog').bounding_box()
                            check(dialog['y']>=103 and dialog['y']+dialog['height']<=438,'settings keyboard: shared dialog tracks visual offset/height')
                            check(page.locator('.phone-settings input').first.bounding_box()['y']+page.locator('.phone-settings input').first.bounding_box()['height']<=page.locator('.phone-settings-save').bounding_box()['y'],
                                  'settings keyboard: focused field stays above save actions')
                            snapshot(page,'settings-keyboard')
                            page.evaluate('''() => {window.__visual.height=innerHeight;window.__visual.offsetTop=0;window.__visual.dispatchEvent(new Event('resize'));}''')
                            page.wait_for_timeout(80)
                            page.evaluate('window.__back()');page.wait_for_timeout(300)
                            check(page.locator('.phone-dialog').count()==0,'Telegram BackButton closes settings')
                            # Same running app after orientation change; new SDK insets.
                            page.set_viewport_size({'width':844,'height':390})
                            page.evaluate('''() => {const t=Telegram.WebApp;t.safeAreaInset={left:44,right:44,bottom:21};t.contentSafeAreaInset={top:32};t.viewportHeight=390;window.__visual.height=390;window.__emit('safeAreaChanged');window.__emit('contentSafeAreaChanged');window.dispatchEvent(new Event('orientationchange'));}''')
                            page.wait_for_timeout(80);geometry(page,'live-orientation')
                            snapshot(page,'live-landscape')
                        else:snapshot(page,name+'-chat')
                        check(not errors,name+': no browser exceptions: '+repr(errors))
                        check(not external,name+': no external requests')
                    except Exception:
                        snapshot(page,name+'-failure')
                        raise
                    finally:context.close()

                for name,scenario,denied,path in [('no-telegram',{'sdk':False},False,'/tg/?key=old-owner#device=private'),
                        ('unsigned',{'auth':False},False,'/tg/'),('wrong-owner',{},True,'/tg/'),
                        ('legacy-bookmark',{'auth':False},False,'/m/?pair=old#device=private')]:
                    before=len(Fixture.requests)
                    context,page,errors,external=open_page(390,844,scenario,path,denied)
                    try:
                        check(page.locator('#pair').is_visible(),name+': old cookie/storage do not bypass initData gate')
                        check(not any(row[1]=='/api/state' for row in Fixture.requests[before:]),name+': no privileged state request before current signed owner')
                        check(page.url.endswith('/m/' if name=='legacy-bookmark' else '/tg/'),name+': credentials removed from URL')
                        check(page.locator('link[rel=manifest]').count()==0,name+': no standalone install manifest')
                        check(not errors and not external,name+': no exceptions/external traffic')
                        snapshot(page,name)
                    finally:context.close()
                report={'passed':True,'level':'Real Edge/Chromium production bundle, controlled SDK/API; NOT physical Telegram',
                        'browser_version':browser.version,'checks':checks,'screenshots':shots,
                        'requests':Fixture.requests}
                (evidence/'browser-acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                print(json.dumps({'passed':len(checks),'screenshots':len(shots),'evidence':str(evidence)},ensure_ascii=False))
            finally:browser.close()
    finally:server.shutdown();server.server_close();thread.join(timeout=2)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True);parser.add_argument('--channel',default='msedge')
    parser.add_argument('--executable-path',default=None)
    run(parser.parse_args())
