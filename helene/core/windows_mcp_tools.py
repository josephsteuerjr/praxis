"""Native typed Windows MCP hands. One upstream name, no opaque RPC aliases."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import tempfile

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
            raise ValueError(path + ': wrong type')
    kind = schema.get('type')
    matches = {'object': isinstance(value, dict), 'array': isinstance(value, list),
               'boolean': type(value) is bool, 'integer': type(value) is int,
               'number': type(value) in (int, float), 'string': isinstance(value, str), 'null': value is None}
    if kind and not matches.get(kind, False):
        raise ValueError(path + ': expected ' + kind)
    if 'enum' in schema and value not in schema['enum']:
        raise ValueError(path + ': choose ' + repr(schema['enum']))
    if kind == 'object':
        properties = schema.get('properties', {})
        if set(schema.get('required', [])) - set(value):
            raise ValueError(path + ': missing required fields')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            raise ValueError(path + ': unknown fields')
        for key, item in value.items():
            if key in properties:
                validate(item, properties[key], path + '.' + key)
    if kind == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', 100000):
            raise ValueError(path + ': wrong array length')
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


def invoke(name, arguments):
    import agent
    import tool_report
    from catalog_families import PreflightRefusal
    if name not in SCHEMAS:
        raise PreflightRefusal('Unknown Windows MCP tool')
    try:
        validate(arguments, SCHEMAS[name])
    except ValueError as exc:
        raise PreflightRefusal(str(exc)) from None
    if _call is None or _allowed is None:
        raise PreflightRefusal('Windows MCP не подключён. Установи компонент в приложении на Windows x64.')
    native = name.removeprefix('windows_mcp_')
    refusal = _allowed(native, arguments)
    if refusal:
        raise PreflightRefusal(refusal)
    args = dict(arguments)
    if native == 'Scrape':
        args['use_sampling'] = False
    result = _call(native, args)
    text, images = [], []
    for block in result.get('content', []):
        if block.get('type') == 'text':
            text.append(str(block.get('text', '')))
        elif block.get('type') == 'image':
            mime = block.get('mimeType')
            if mime not in ('image/png', 'image/jpeg', 'image/webp'):
                text.append('Unsupported Windows MCP image type: ' + str(mime))
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
    receipt = '\n'.join(text) or 'Windows MCP returned an empty result.'
    return agent.ToolObservation(receipt, tuple(images)) if images else receipt


def implementations():
    def bind(name):
        def call(**arguments):
            return invoke(name, arguments)
        call.__name__ = name
        return call
    return {tool['name']: bind(tool['name']) for tool in TOOLS}
