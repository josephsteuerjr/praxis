"""Native typed Windows MCP hands. One upstream name, no opaque RPC aliases."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import tempfile
import frame_language

TOOLS = json.loads(Path(__file__).with_name('windows_mcp_schemas.json').read_text(encoding='utf-8'))
SCHEMAS = {tool['name']: tool['input_schema'] for tool in TOOLS}
# Reviewed English summaries of the pinned tools, shared by the compact pointer.
PURPOSES = {'windows_mcp_' + name: purpose for name, purpose in {
    'ControlStatus': 'inspect desktop-control ownership or release it',
    'App': 'launch, switch, move or resize a Windows application',
    'DisplayInventory': 'list displays and their coordinate spaces',
    'PowerShell': 'execute PowerShell with a bounded timeout',
    'FileSystem': 'read, write, copy, move, delete, list, search or inspect local files',
    'Snapshot': 'inspect the desktop accessibility tree and interactive targets',
    'Screenshot': 'capture a fast desktop screenshot',
    'Click': 'click an observed desktop target or coordinate',
    'Type': 'type into an observed control or the focused application',
    'Scroll': 'scroll at an observed target or coordinate',
    'Move': 'move the pointer or drag between coordinates',
    'Shortcut': 'send a keyboard shortcut to the active application',
    'Wait': 'wait for a bounded duration',
    'WaitFor': 'wait for a desktop condition',
    'Scrape': 'read a web page or the active browser DOM without model sampling',
    'MultiSelect': 'select several items in a desktop control',
    'MultiEdit': 'edit several desktop controls',
    'Clipboard': 'read or set clipboard text',
    'Process': 'list or terminate Windows processes',
    'Notification': 'show a Windows notification',
    'Registry': 'read, list, set or delete Windows registry values',
}.items()}
assert set(PURPOSES) == set(SCHEMAS)
_call = None
_allowed = None

# Only this closed producer-owned envelope is re-rendered. Server text, file
# content and arbitrary upstream structuredContent always remain literal.
_MESSAGES = {
    'wrong_type': {'path'}, 'expected': {'path','kind'}, 'choose': {'path','choices'},
    'required': {'path'}, 'unknown_fields': {'path'}, 'array_length': {'path'},
    'unknown_tool': set(), 'component_needed': set(), 'unsupported_image': {'mime'},
    'empty_result': set(), 'computer_disabled': set(), 'missing_scopes': {'scopes'},
    'sovereign': set(), 'app_unavailable': set(), 'unknown_caller': set(),
    'unconfirmed': set(), 'not_running': set(), 'start_exited': {'log'},
    'invalid_catalog': set(), 'not_ready': set(), 'exited': set(),
    'unsupported_platform': set(),
    'remote_unavailable':set(), 'remote_refused':set(),
}


@frame_language.pinned
def _format_message(spec, depth=0):
    if (not isinstance(spec,dict) or depth>3 or not isinstance(spec.get('code'),str)
            or spec['code'] not in _MESSAGES):
        return None
    values=spec.get('values',{})
    if (not isinstance(values,dict) or set(values)!=_MESSAGES[spec['code']]
            or any(not isinstance(value,str) for value in values.values())
            or not isinstance(spec.get('detail',''),str)):
        return None
    result=frame_language.text('mcp.'+spec['code'],**values)
    if spec.get('cause'):
        cause=_format_message(spec['cause'],depth+1)
        if cause is None:return None
        result+='\n'+cause
    if spec.get('detail'):result+='\n'+spec['detail']
    return result


def configure(call, allowed):
    global _call, _allowed
    _call, _allowed = call, allowed


def validate(value, schema, path='arguments'):
    """Validate the pinned scalar/array/object subset before crossing the app boundary."""
    if 'anyOf' in schema:
        for candidate in schema['anyOf']:
            try:
                validate(value, candidate, path)
                break
            except ValueError:
                pass
        else:
            raise ValueError(frame_language.text('mcp.wrong_type',path=path))
    kind = schema.get('type')
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'boolean': type(value) is bool, 'integer': type(value) is int,
               'number': type(value) in (int, float), 'string': isinstance(value, str), 'null': value is None}
    if kind and not matches.get(kind, False):
        raise ValueError(frame_language.text('mcp.expected',path=path,kind=kind))
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(frame_language.text('mcp.choose',path=path,choices=repr(schema['enum'])))
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValueError(frame_language.text('mcp.required',path=path))
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(frame_language.text('mcp.unknown_fields',path=path))
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], path + '.' + key)
    if kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100000):
            raise ValueError(frame_language.text('mcp.array_length',path=path))
        for index, item in enumerate(value):
            validate(item, schema.get('items', {}), f'{path}[{index}]')


def has_side_effect(name, args):
    name = name.removeprefix('windows_mcp_')
    if name in ('DisplayInventory', 'Wait', 'WaitFor', 'Scrape'):
        return False
    if name == 'ControlStatus':
        return bool(args.get('release'))
    if name in ('Snapshot', 'Screenshot'):
        return bool(args.get('save_path'))
    if name == 'FileSystem':
        return args.get('mode') not in ('read', 'list', 'search', 'info')
    if name == 'Registry':
        return args.get('mode') not in ('get', 'list')
    if name == 'Clipboard':
        return args.get('mode') != 'get'
    if name == 'Process':
        return args.get('mode') != 'list'
    return True


@frame_language.pinned
def invoke(name, arguments):
    import agent
    import tool_report
    from catalog_families import PreflightRefusal
    if name not in SCHEMAS:
        raise PreflightRefusal(frame_language.text('mcp.unknown_tool'))
    try:
        validate(arguments, SCHEMAS[name])
    except ValueError as exc:
        raise PreflightRefusal(str(exc)) from None
    if _call is None or _allowed is None:
        raise PreflightRefusal(frame_language.text('mcp.component_needed'))
    native = name.removeprefix('windows_mcp_')
    refusal = _allowed(native, arguments)
    if refusal:
        raise PreflightRefusal(_format_message(refusal) or str(refusal))
    args = dict(arguments)
    if native == 'Scrape':
        args['use_sampling'] = False
    result = _call(native, args)
    text, images = [], []
    owned=_format_message(result.get('helene_error'))
    blocks=[dict(type='text',text=owned)] if owned is not None else result.get('content', [])
    for block in blocks:
        if block.get('type') == 'text':
            text.append(str(block.get('text', '')))
        elif block.get('type') == 'image':
            mime = block.get('mimeType')
            if mime not in ('image/png', 'image/jpeg', 'image/webp'):
                text.append(frame_language.text('mcp.unsupported_image',mime=str(mime)))
                continue
            raw = base64.b64decode(block.get('data', ''), validate=True)
            parent = agent.STATE_DIR / 'windows-mcp-images'
            parent.mkdir(parents=True, exist_ok=True)
            temporary = Path(tempfile.mkdtemp(prefix='capture-', dir=parent))
            path = temporary / ('capture.' + {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp'}[mime])
            path.write_bytes(raw)
            observed = agent._observe_image_pixels(path, temporary, mime=mime, safe_name=path.name,
                sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), origin='computer-observe')
            text.append(str(observed))
            if isinstance(observed, agent.ToolObservation):
                images.extend(observed.images)
    if not text and result.get('structuredContent') is not None:
        text.append(json.dumps(result['structuredContent'], ensure_ascii=False))
    failed = result.get('isError') or any(s.lstrip().lower().startswith('error:') for s in text)
    outcome = 'in_doubt' if result.get('in_doubt') else 'failed' if failed else 'ok'
    tool_report.note(outcome=outcome, target=str(args.get('path') or args.get('name') or native))
    receipt = '\n'.join(text) or frame_language.text('mcp.empty_result')
    return agent.ToolObservation(receipt, tuple(images)) if images else receipt


def implementations():
    def bind(name):
        def call(**arguments):
            return invoke(name, arguments)
        call.__name__ = name
        return call
    return {tool['name']: bind(tool['name']) for tool in TOOLS}
