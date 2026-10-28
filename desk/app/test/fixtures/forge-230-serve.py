"""Isolated actual reader/journal fixture; no production state or model calls.
python desk/app/test/fixtures/forge-230-serve.py [port=5687]
Only loopback. Generated synthetic profile is deleted when service exits.
"""
from __future__ import annotations
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parents[4]
sys.path[:0]=[str(ROOT/'desk'),str(ROOT/'helene/core')]
from deskd import forge_reader
import forge_runtime


def put(path,row):
    forge_runtime.atomic_json(path,row)


def prepare(base):
    root=base/'memory/.forge/tasks'
    task=root/'000-old-parent';put(task/'task.json',{'id':task.name,'goal':'Совмещение изменённого агента: длинная цель с сохранением авторских правок и Unicode 🐍漢字','status':'done','created':'2026-01-01'})
    parent=task/'agents/agent-parent'
    put(parent/'request.json',{'id':parent.name,'task_id':task.name,'role':'worker','model':'GPT-6.1 Sol','status':'running','brief':'Родитель уже завершён — ребёнок остаётся видимым'})
    put(parent/'result.json',{'status':'done','finished':'2026-01-02','result':'Parent result retained'})
    for unit,role,phase,stop in [('agent-child','worker','tool',False),('agent-grandchild','reviewer','waiting',True)]:
        folder=task/'agents'/unit
        req={'id':unit,'task_id':task.name,'role':role,'model':'очень-длинная-модель-gpt-6.1-sol-high-fast-🐍漢字','parent_unit_id':'agent-parent' if unit=='agent-child' else 'agent-child','status':'running','brief':('Проверить весь окончательный состав и не потерять долгую дочернюю работу — 漢字🐍 ')*8}
        put(folder/'request.json',req)
        journal=forge_runtime.Journal(folder)
        try:
            journal.start(req,{'next_boundary':'model_ready','iteration':3,'model':req['model'],'messages':[],'trace':[]})
            args=journal.payload(json.dumps({'command':'verify-composition','path':'synthetic/Unicode 🐍漢字','criteria':['full source','preserve data']},ensure_ascii=False))
            output=journal.payload('Полный неизменяемый результат 🐍漢字\n'*1000)
            journal.event('tool_start',tool='coding_verify',input_ref=args)
            journal.event('tool_result',tool='coding_verify',input_ref=args,output_ref=output)
            journal.save('model_ready',phase)
        finally:journal.close()
        if stop:put(folder/'stop.json',{'state':'pending'})
    for n in range(35):
        folder=root/f'{n+1:03}-finished'
        put(folder/'task.json',{'id':folder.name,'goal':f'Завершённая задача {n+1}','status':'done','created':f'2026-10-{n+1:02}'})
        put(folder/'agents/agent-done/request.json',{'id':'agent-done','task_id':folder.name,'status':'running','role':'scout','model':'fixture'})
        put(folder/'agents/agent-done/result.json',{'status':'done','result':'Completed synthetic observation','finished':f'2026-10-{n+1:02}'})


class Handler(SimpleHTTPRequestHandler):
    base=None
    def do_GET(self):
        parsed=urlsplit(self.path)
        if not parsed.path.startswith('/api/forge/'):
            return super().do_GET()
        q={k:v[0] for k,v in parse_qs(parsed.query).items()}
        try:
            if parsed.path=='/api/forge/tasks':value=forge_reader.activity(self.base,int(q.get('limit','20')),q.get('cursor',''))
            elif parsed.path=='/api/forge/events':value=forge_reader.events(self.base,q['task_id'],q['unit_id'],int(q.get('offset','0')),int(q.get('limit','50')),q.get('generation',''),int(q.get('after_seq','0')))
            elif parsed.path=='/api/forge/payload':value=forge_reader.payload(self.base,q['task_id'],q['unit_id'],q['sha256'],q.get('kind','payload'),int(q.get('offset','0')),int(q.get('limit','8000')))
            elif parsed.path=='/api/forge/control':value={'commands':[]}
            else:raise forge_reader.ReadError('Not a fixture route',404)
            data=json.dumps(value,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except (forge_reader.ReadError,ValueError,KeyError) as exc:self.send_error(getattr(exc,'status',400),str(exc))


if __name__=='__main__':
    directory=Path(__file__).with_name('forge-230-built')
    if not (directory/'index.html').is_file():raise SystemExit('Build fixture first with forge-230-build.mjs')
    with tempfile.TemporaryDirectory(prefix='.forge-230-browser-',dir=ROOT) as temp:
        Handler.base=Path(temp);prepare(Handler.base)
        server=ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1]) if len(sys.argv)>1 else 5687),functools.partial(Handler,directory=str(directory)))
        print('READY synthetic production Forge fixture',flush=True)
        try:server.serve_forever()
        finally:server.server_close()
