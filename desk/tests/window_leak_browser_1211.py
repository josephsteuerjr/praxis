"""Window memory/CPU stand: a busy agent starts a new run every 2 s; each run's detail is large.

12.11: one renderer grew to 3.8 GB over a day of Jarvis's work and spent ~20 % of a core.
The production window bundle (MiniApp build) with an isolated fixture; JS heap after a
forced GC is sampled while runs come and go. Default mode asserts a plateau.
Modes: (default) heap, `metrics` main-thread costs, `profile` CPU profile, `shot` live dot.
Usage: uv run --with playwright python window_leak_browser_1211.py <dir> [runs] [mode]
"""
import json, sys, threading, time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parent))
from program_browser_1211 import ProgramFixture  # noqa: E402

BIG = 'x' * (300 * 1024)  # a long tool output kept in the run detail


def main():
    from playwright.sync_api import sync_playwright
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    server = ThreadingHTTPServer(('127.0.0.1', 0), ProgramFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{server.server_port}'
    state = {'n': 0, 'started': time.time()}

    def run_id(n):
        return f'run-{n:05d}'

    def route(request):
        url = request.request.url
        if not url.startswith(origin):
            return request.abort()
        path = urlsplit(url).path
        if '/program/' in path:
            path = '/' + '/'.join(path.split('/')[3:])
        n = int((time.time() - state['started']) // 2)
        state['n'] = n
        if path == '/api/state':
            return request.fulfill(json={'agent': 'Мира', 'owner': 'Владелец', 'level': 'ok', 'phrase': 'Ведёт ход',
                                         'runner': {'alive': True, 'busy': True, 'run': run_id(n), 'since': time.time() - 1},
                                         'brain': {'last_call_at': time.time()}, 'anatomy': False})
        if path == '/api/runs':
            rows = [{'id': run_id(i), 'kind': 'chat', 'status': 'running' if i == n else 'done', 'chat_id': 'window',
                     'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'goal_head': 'Работа ' + str(i)}
                    for i in range(n, max(-1, n - 200), -1)]
            return request.fulfill(json=rows)
        if path.startswith('/api/chat-turns/'):
            return request.fulfill(json=[{'run_id': run_id(i), 'text': 'Сделано ' + str(i), 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
                                         for i in range(max(0, n - 50), n)])
        if path.startswith('/api/run/'):
            identity = path.split('/')[-1]
            return request.fulfill(json={'manifest': {'id': identity, 'status': 'running' if identity == run_id(n) else 'done'},
                                         'items': [{'status': 'done', 'tools': [{'tool': 'read', 'output': BIG}]}],
                                         'steps': [{'tool': 'read', 'output': BIG}]})
        return request.continue_()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel='msedge', headless=True)
            page = browser.new_context(viewport={'width': 1280, 'height': 900}, reduced_motion=('reduce' if 'REDUCED' in __import__('os').environ else 'no-preference')).new_page()
            page.route('**/*', route)
            page.goto(origin + '/tg/', wait_until='networkidle')
            page.wait_for_selector('#app:not([hidden])')
            page.locator('[data-view="talk"]').first.click()
            cdp = page.context.new_cdp_session(page)
            cdp.send('Performance.enable')

            def heap():
                cdp.send('HeapProfiler.collectGarbage')
                return {m['name']: m['value'] for m in cdp.send('Performance.getMetrics')['metrics']}
            if len(sys.argv) > 3 and sys.argv[3] == 'shot':
                page.wait_for_timeout(6000)
                dot = page.locator('.turn[data-status="live"] .turn-dot').first
                dot.wait_for()
                box = dot.bounding_box()
                for i, delay in enumerate((0, 500, 1000)):
                    page.wait_for_timeout(delay and 500)
                    page.screenshot(path=str(out / f'live-dot-{i}.png'), clip={'x': box['x'] - 60, 'y': box['y'] - 30, 'width': 420, 'height': 90})
                page.screenshot(path=str(out / 'live-panel.png'))
                browser.close(); return
            if len(sys.argv) > 3 and sys.argv[3] == 'metrics':
                page.wait_for_timeout(15000)
                keys = ('TaskDuration', 'RecalcStyleCount', 'RecalcStyleDuration', 'LayoutCount', 'LayoutDuration', 'ScriptDuration')
                a = {k: v for k, v in heap().items() if k in keys}
                page.wait_for_timeout(20000)
                b = {k: v for k, v in heap().items() if k in keys}
                print({k: round(b[k] - a[k], 3) for k in keys})
                browser.close(); return
            if len(sys.argv) > 3 and sys.argv[3] == 'profile':
                import collections
                page.wait_for_timeout(15000)
                cdp.send('Profiler.enable'); cdp.send('Profiler.setSamplingInterval', {'interval': 500}); cdp.send('Profiler.start')
                page.wait_for_timeout(20000)
                prof = cdp.send('Profiler.stop')['profile']
                nodes = {n['id']: n for n in prof['nodes']}
                parents = {c: n['id'] for n in prof['nodes'] for c in n.get('children', [])}
                step = (prof['endTime'] - prof['startTime']) / max(1, len(prof['samples'])) / 1000
                own, total = collections.Counter(), collections.Counter()
                for node_id in prof['samples']:
                    f = nodes[node_id]['callFrame']
                    own[(f['functionName'] or '(anon)', f['url'].split('/')[-1], f['lineNumber'], f['columnNumber'])] += step
                    seen, cur = set(), node_id
                    while cur in nodes:
                        g = nodes[cur]['callFrame']
                        key = (g['functionName'] or '(anon)', g['url'].split('/')[-1], g['lineNumber'], g['columnNumber'])
                        if key not in seen:
                            total[key] += step; seen.add(key)
                        cur = parents.get(cur)
                (out / 'profile-busy.json').write_text(json.dumps({'self': [[round(v, 1), *k] for k, v in own.most_common(30)],
                    'total': [[round(v, 1), *k] for k, v in total.most_common(40)]}, ensure_ascii=False, indent=0), encoding='utf-8')
                print('self', [[round(v, 1), *k] for k, v in own.most_common(12)])
                print('total', [[round(v, 1), *k] for k, v in total.most_common(25)])
                browser.close(); return
            samples = []
            start = state['n']
            last = heap()['TaskDuration']
            while state['n'] - start < total:
                page.wait_for_timeout(10000)
                m = heap()
                samples.append({'runs_seen': state['n'] - start, 'heap_mb': round(m['JSHeapUsedSize'] / 2 ** 20, 2),
                                'nodes': int(m['Nodes']), 'listeners': int(m['JSEventListeners']),
                                'main_thread_pct': round((m['TaskDuration'] - last) / 10 * 100, 1)})
                last = m['TaskDuration']
                print(samples[-1], flush=True)
            (out / 'leak.json').write_text(json.dumps(samples, indent=1), encoding='utf-8')
            later = [row['heap_mb'] for row in samples if row['runs_seen'] >= 25]
            # The bounded cache fills by ~16 runs; afterwards the heap must stay flat.
            assert later and max(later) - min(later) < 2, samples
            (out / 'PASS.json').write_text(json.dumps({'runs': total, 'heap_plateau_mb': later}, indent=1), encoding='utf-8')
            browser.close()
    finally:
        server.shutdown(); server.server_close()


main()
