"""Prepare the XTTS extension for the existing server MiniApp without rebuilding it.

Pure transformations only. The deployment caller must back up and hash-check the
actual installed files before writing the returned backend/index/assets.
"""
from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
import re


def backend(source: str) -> str:
    if 'ROUTES += xtts_ui.routes(Route)' in source:
        raise ValueError('voice extension already installed')
    def replace(before, after):
        nonlocal source
        if source.count(before) != 1:
            raise ValueError('installed backend differs at: ' + before[:60])
        source = source.replace(before, after)
    replace('"/api/container-log/")', '"/api/container-log/", "/api/xtts/")')
    replace('"Access-Control-Allow-Headers": "Content-Type",',
            '"Access-Control-Allow-Headers": "Content-Type, Authorization",')
    replace('supplied = request.query.get("key") or request.cookies.get(COOKIE) or ""',
            'authorization = request.headers.get("Authorization", "")\n'
            '    bearer = authorization[7:] if authorization.startswith("Bearer ") else ""\n'
            '    supplied = request.query.get("key") or bearer or request.cookies.get(COOKIE) or ""')
    replace('    local_only: bool = False   # только с этой машины (окно): пары телефона',
            '    local_only: bool = False   # только с этой машины (окно): пары телефона\n'
            '    max_body_bytes: int = 0\n    private: bool = False')
    replace('\n\ndef _route_regex(path:',
            '\n\nfrom deskd import xtts_ui\nROUTES += xtts_ui.routes(Route)\n\ndef _route_regex(path:')
    replace('            body = await _json_body(request)\n        call = Call(',
            '            source = request.clone(client_max_size=route.max_body_bytes) if route.max_body_bytes else request\n'
            '            body = await _json_body(source)\n        call = Call(')
    replace('            return web.Response(status=result.status, text=result.error)\n        return _json(result)\n    return handler',
            '            response = web.Response(status=result.status, text=result.error)\n'
            '        else:\n            response = _json(result)\n'
            '        if route.private:\n            response.headers["Cache-Control"] = "private, no-store"\n'
            '            response.headers["X-Content-Type-Options"] = "nosniff"\n'
            '        return response\n    return handler')
    replace('    app = web.Application(middlewares=[auth_middleware])',
            '    app = web.Application(middlewares=[auth_middleware])\n    app.on_cleanup.append(xtts_ui.cleanup)')
    return source


def assets(index: str, base_js: bytes, voice_js: bytes, voice_css: bytes):
    if 'data-praxis-voice=' in index:
        raise ValueError('voice extension already installed')
    scripts = list(re.finditer(r'<script\b[^>]*\btype="module"[^>]*\bsrc="([^"]*assets/index-[A-Za-z0-9_-]+\.js)"[^>]*>', index))
    if len(scripts) != 1:
        raise ValueError('expected one installed Vite entrypoint')
    old = scripts[0].group(1)
    if '..' in PurePosixPath(old).parts:
        raise ValueError('invalid entrypoint path')
    fingerprint = hashlib.sha256(voice_js + b'\0' + voice_css).hexdigest()[:16]
    name = PurePosixPath(old).name
    alias = name[:-3] + '-xtts-' + fingerprint + '.js'
    prefix = old[:-len(name)]
    js_name, css_name = 'voice-' + fingerprint + '.js', 'voice-' + fingerprint + '.css'
    index = index.replace('src="' + old + '"', 'src="' + prefix + alias + '"', 1)
    index = index.replace('</head>', '<link rel="stylesheet" href="' + prefix + css_name + '"></head>')
    index = index.replace('</body>', '<script type="module" src="' + prefix + js_name + '" data-praxis-voice="legacy"></script></body>')
    return index, {alias: base_js, js_name: voice_js, css_name: voice_css}
