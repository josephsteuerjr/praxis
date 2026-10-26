"""Fixed Linux desktop executor embedded in the native body. No agent or supervisor.

X11/EWMH/XTest and AT-SPI are distinct capabilities. Wayland and a service
without a graphical session refuse desktop operations; file/process hands remain
available. Only standard-library Python and declared distro dependencies are used.
"""
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import selectors
import struct
import subprocess
import sys
import time
import uuid
import zlib
try:
    import fcntl
except ImportError:
    fcntl = None  # portable pure contract tests; this executor runs only on Linux
from contextlib import contextmanager

OPERATION_END=None


def integer(value, name, low=0, high=2**31 - 1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(name + " must be an integer in [%s,%s]" % (low, high))
    return value


def handle(value):
    if isinstance(value, str) and re.fullmatch(r"(?:0[xX][0-9a-fA-F]+|[0-9]+)", value):
        value = int(value, 16 if value.lower().startswith("0x") else 10)
    return integer(value, "hwnd", 1, 2**32 - 1)


def session(env=None):
    env = os.environ if env is None else env
    if env.get("WAYLAND_DISPLAY") or env.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland", "Рабочий стол Wayland в этом выпуске недоступен для ввода и снимков. Используй сеанс X11; XWayland не даёт доступ ко всему рабочему столу."
    if not env.get("DISPLAY") or env.get("HELENE_SERVICE") == "1":
        return "headless", "У службы systemd нет графического рабочего стола. Для ввода, окон и снимков открой Hélène в сеансе X11 владельца."
    return "x11", ""


def command(argv, input_data=None, timeout=5, strip=True):
    if input_data is not None: raise ValueError("desktop observation commands do not accept input")
    if OPERATION_END is not None:
        timeout=min(timeout,OPERATION_END-time.monotonic())
        if timeout<=0: raise TimeoutError('Linux desktop operation deadline reached before '+argv[0])
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={**os.environ,'LC_ALL':'C.UTF-8'}) as child:
        try:
            output, errors = bytearray(), bytearray(); deadline = time.monotonic() + timeout
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ, output)
                selector.register(child.stderr, selectors.EVENT_READ, errors)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise subprocess.TimeoutExpired(argv, timeout)
                    for key, _ in selector.select(remaining):
                        chunk = os.read(key.fileobj.fileno(), 16384)
                        if not chunk: selector.unregister(key.fileobj); continue
                        if key.data is output:
                            if len(output) + len(chunk) > 8 * 1024 * 1024: raise RuntimeError("desktop response exceeded its byte limit")
                            output.extend(chunk)
                        elif len(errors) < 2000: errors.extend(chunk[:2000-len(errors)])
            if child.wait(timeout=max(.01, deadline-time.monotonic())):
                raise RuntimeError(errors.decode("utf8", "replace") or "command failed: " + argv[0])
            text=output.decode('utf8','replace')
            return text.strip() if strip else text
        finally:
            if child.poll() is None: child.kill(); child.wait()


def xdo(*args):
    return command(["xdotool"] + [str(a) for a in args])


def property_of(hwnd, name):
    return command(["xprop", "-id", str(hwnd), name])


def window_title(hwnd):
    # xdotool adds one newline; spaces/newlines in the actual title are identity.
    return command(['xdotool','getwindowname',str(hwnd)],strip=False).removesuffix('\n')


def pid_of(hwnd):
    match = re.search(r"=\s*(\d+)", property_of(hwnd, "_NET_WM_PID"))
    return int(match.group(1)) if match else None


def geometry(hwnd):
    values = dict(re.findall(r"^(X|Y|WIDTH|HEIGHT)=(-?\d+)$", xdo("getwindowgeometry", "--shell", hwnd), re.M))
    if len(values) != 4:
        raise RuntimeError("window geometry was not confirmed")
    return dict(x=int(values["X"]), y=int(values["Y"]), width=int(values["WIDTH"]), height=int(values["HEIGHT"]))


def check_stop():
    stop = Path(os.environ.get("HOME", "/nonexistent")) / ".local/state/helene/stop.json"
    try:
        stop.stat()
    except FileNotFoundError:
        pass
    else:
        raise RuntimeError("Owner emergency stop is active; further desktop actions were stopped")


def guard(args):
    check_stop()
    expected = args.get("expected_foreground")
    try: actual = handle(xdo("getactivewindow"))
    except (RuntimeError, ValueError):
        if expected is not None or args.get('expected_pid') is not None: raise RuntimeError('No foreground window; target-bound input stopped')
        actual=None  # a root-desktop mouse action need not have an active window
    if expected is not None and actual != handle(expected):
        raise RuntimeError("foreground window changed; input stopped")
    pid = args.get("expected_pid")
    if pid is not None and pid_of(actual) != integer(pid, "expected_pid", 1, 2**32 - 1):
        raise RuntimeError("foreground process changed; input stopped")
    return actual


KEYS = {"ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L",
        "win": "Super_L", "super": "Super_L", "meta": "Super_L", "enter": "Return", "return": "Return",
        "escape": "Escape", "esc": "Escape", "tab": "Tab", "space": "space", "backspace": "BackSpace",
        "delete": "Delete", "home": "Home", "end": "End", "left": "Left", "right": "Right",
        "up": "Up", "down": "Down", "pageup": "Prior", "pagedown": "Next"}
KEYS.update({'left_ctrl':'Control_L','left_control':'Control_L','right_ctrl':'Control_R','right_control':'Control_R',
             'left_shift':'Shift_L','right_shift':'Shift_R','left_alt':'Alt_L','right_alt':'Alt_R',
             'left_win':'Super_L','right_win':'Super_R','page_up':'Prior','page_down':'Next',
             'insert':'Insert','del':'Delete','caps_lock':'Caps_Lock','capslock':'Caps_Lock',
             'printscreen':'Print','print_screen':'Print','pause':'Pause'})
BUTTONS = {"left": 1, "middle": 2, "right": 3}


def key_name(value):
    if not isinstance(value, str) or not value or len(value) > 24:
        raise ValueError("key must name an X11 key; Windows numeric virtual-key codes are unavailable")
    name = KEYS.get(value.lower())
    if name:
        return name
    if re.fullmatch(r"[a-zA-Z0-9]|[fF](?:[1-9]|1[0-9]|2[0-4])", value):
        return value.upper() if value.lower().startswith("f") and len(value) > 1 else value
    raise ValueError("unknown X11 key: " + value)


def input_plan(args):
    allowed = {"events", "expected_foreground", "expected_pid", "inter_event_delay_ms"}
    if set(args) - allowed:
        raise ValueError("unknown input fields")
    if args.get("expected_foreground") is not None:
        handle(args["expected_foreground"])
    if args.get("expected_pid") is not None:
        integer(args["expected_pid"], "expected_pid", 1, 2**32 - 1)
    events = args.get("events")
    if not isinstance(events, list) or not 1 <= len(events) <= 500:
        raise ValueError("events must contain 1..500 actions")
    delay = integer(args.get("inter_event_delay_ms", 0), "inter_event_delay_ms", 0, 30000)
    budget = delay * max(0, len(events) - 1)
    text_count = 0
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("each input action must be an object")
        kind = event.get("type")
        fields = {"key": {"type", "key", "action"}, "text": {"type", "text"}, 'hotkey':{'type','keys'},
                  "mouse": {"type", "x", "y", "relative", "button", "action"}, "scroll": {"type", "delta", "axis"},
                  'click':{'type','button','x','y','count'}, 'wheel':{'type','delta','horizontal','x','y'},
                  'mouse_down':{'type','button','x','y'}, 'mouse_up':{'type','button','x','y'},
                  'drag':{'type','button','x','y','to_x','to_y','steps','hold_ms','step_delay_ms','settle_ms'},
                  "pause": {"type", "ms"}}
        if kind not in fields or set(event) - fields[kind]:
            raise ValueError("unknown input action or fields")
        if kind == "key":
            key_name(event.get("key"))
            if event.get("action", "press") not in ("down", "up", "press"):
                raise ValueError("key action must be down/up/press")
        elif kind == 'hotkey':
            keys=event.get('keys')
            if not isinstance(keys,list) or not 1<=len(keys)<=16: raise ValueError('hotkey requires 1..16 named keys')
            for key in keys: key_name(key)
        elif kind == "text":
            if not isinstance(event.get("text"), str) or "\0" in event["text"]:
                raise ValueError("text must be a string without NUL")
            try: event['text'].encode('utf8')
            except UnicodeEncodeError: raise ValueError('text must contain valid Unicode scalar values') from None
            if any((ord(char)<32 and char not in '\r\n\t') or ord(char)==127 for char in event['text']):
                raise ValueError('text control characters require explicit named key events')
            text_count += len(event["text"])
        elif kind in ('mouse','click','wheel','mouse_down','mouse_up','drag'):
            for field in ("x", "y"):
                if field in event:
                    integer(event[field], field, -32768, 32767)
            if ("x" in event) != ("y" in event):
                raise ValueError("mouse coordinates require x and y")
            if "button" in event and event["button"] not in BUTTONS:
                raise ValueError("unknown mouse button")
            if kind=='mouse' and ('x' not in event or 'y' not in event): raise ValueError('mouse requires x and y')
            if kind=='mouse' and type(event.get('relative',False)) is not bool: raise ValueError('relative must be boolean')
            if kind=='mouse' and event.get("action", "click") not in ("down", "up", "click", "double_click"):
                raise ValueError("unknown mouse action")
            if kind=='click': integer(event.get('count',1),'click.count',1,20)
            if kind=='wheel':
                integer(event.get('delta'),'wheel.delta',-12000,12000)
                if type(event.get('horizontal',False)) is not bool: raise ValueError('horizontal must be boolean')
            if kind=='drag':
                for coordinate in ('x','y','to_x','to_y'): integer(event.get(coordinate),coordinate,-32768,32767)
                steps=integer(event.get('steps',20),'drag.steps',1,200)
                hold=integer(event.get('hold_ms',100),'drag.hold_ms',0,5000)
                step_delay=integer(event.get('step_delay_ms',10),'drag.step_delay_ms',0,5000)
                settle=integer(event.get('settle_ms',100),'drag.settle_ms',0,5000)
                budget+=hold+steps*step_delay+settle
        elif kind == "scroll":
            integer(event.get("delta"), "delta", -12000, 12000)
            if event.get("axis", "vertical") not in ("vertical", "horizontal"):
                raise ValueError("scroll axis must be vertical/horizontal")
        else:
            budget += integer(event.get("ms"), "pause.ms", 0, 30000)
    if text_count > 2048 or budget > 30000:
        raise ValueError("input exceeds text or delay budget")
    return events, delay


class ClientData(C.Union):
    _fields_=[('b',C.c_char*20),('s',C.c_short*10),('l',C.c_long*5)]


class ClientMessage(C.Structure):
    _fields_=[('type',C.c_int),('serial',C.c_ulong),('send_event',C.c_int),('display',C.c_void_p),
              ('window',C.c_ulong),('message_type',C.c_ulong),('format',C.c_int),('data',ClientData)]


class XEvent(C.Union):
    _fields_=[('type',C.c_int),('client',ClientMessage),('padding',C.c_long*24)]


class UnicodeKeys:
    """Keep temporary X11 Unicode mappings alive for the whole input batch.

    Restoring a mapping after each character can race GDK's MappingNotify
    processing under load. Only unused, unpressed keycodes are borrowed, and
    a concurrent owner layout change is never overwritten during restoration.
    """
    def __init__(self, events):
        self.lib=C.CDLL('libX11.so.6'); self.xtest=C.CDLL('libXtst.so.6')
        self.display=function(self.lib,'XOpenDisplay',C.c_void_p,C.c_char_p)(None)
        if not self.display: raise RuntimeError('X11 display unavailable before input')
        p=C.c_void_p; ul=C.c_ulong
        self.get=function(self.lib,'XGetKeyboardMapping',C.POINTER(ul),p,C.c_ubyte,C.c_int,C.POINTER(C.c_int))
        self.change=function(self.lib,'XChangeKeyboardMapping',C.c_int,p,C.c_int,C.c_int,C.POINTER(ul),C.c_int)
        self.free=function(self.lib,'XFree',C.c_int,p)
        self.sync=function(self.lib,'XSync',C.c_int,p,C.c_int)
        self.fake=function(self.xtest,'XTestFakeKeyEvent',C.c_int,p,C.c_uint,C.c_int,ul)
        self.close_display=function(self.lib,'XCloseDisplay',C.c_int,p)
        self.borrowed={}; self.codes={}; self.held={}; self.buttons=set()
        self.property='_HELENE_UNICODE_%s' % os.geteuid()
        self.journal=Path(os.environ.get('HOME','/nonexistent'))/'.local/state/helene/unicode-mapping.json'
        self.nonce=None
        try:
            extension=[C.c_int() for _ in range(4)]
            if not function(self.xtest,'XTestQueryExtension',C.c_int,p,*([C.POINTER(C.c_int)]*4))(self.display,*[C.byref(value) for value in extension]):
                raise RuntimeError('XTest extension is unavailable; no input performed')
            self.recover()
            low,high=C.c_int(),C.c_int()
            function(self.lib,'XDisplayKeycodes',C.c_int,p,C.POINTER(C.c_int),C.POINTER(C.c_int))(self.display,C.byref(low),C.byref(high))
            pressed=C.create_string_buffer(32)
            function(self.lib,'XQueryKeymap',C.c_int,p,C.c_void_p)(self.display,pressed)
            columns=C.c_int(); mapping=self.get(self.display,low.value,high.value-low.value+1,C.byref(columns))
            if not mapping or not 1 <= columns.value <= 64: raise RuntimeError('X11 keyboard map unavailable')
            try:
                free=[]
                for code in range(low.value,high.value+1):
                    row=tuple(mapping[(code-low.value)*columns.value+i] for i in range(columns.value))
                    if not any(row) and not (pressed.raw[code//8] & (1 << (code%8))): free.append((code,row))
            finally: self.free(mapping)
            # Prefer contiguous unused slots: changing one code at a time
            # makes every desktop client rebuild its keyboard map repeatedly.
            blocks=[]
            for item in free:
                if blocks and item[0]==blocks[-1][-1][0]+1: blocks[-1].append(item)
                else: blocks.append([item])
            free=[item for block in sorted(blocks,key=lambda block:(-len(block),block[0][0])) for item in block]
            characters=sorted(set(''.join(event['text'] for event in events if event['type']=='text')))
            if len(characters)>len(free): raise ValueError('X11 has only %s unused Unicode key slots; split this text into smaller distinct-character batches. No input performed.' % len(free))
            for char,(code,old) in zip(characters,free):
                keysym={'\n':0xFF0D,'\r':0xFF0D,'\t':0xFF09}.get(char,ord(char) if ord(char)<256 else 0x01000000|ord(char))
                # Two identical levels prevent XKB from inventing a lower/upper
                # pair for a one-symbol alphabetic key. Fill each group too.
                new=(keysym,)*columns.value
                self.borrowed[code]=(old,new); self.codes[char]=code
            if self.borrowed:
                self.save()
            self.change_rows({code:new for code,(_old,new) in self.borrowed.items()})
            self.sync(self.display,False)
            if characters: time.sleep(.04)
        except BaseException:
            self.close(); raise

    def change_rows(self,rows):
        groups=[]
        for code,row in sorted(rows.items()):
            if groups and code==groups[-1][-1][0]+1 and len(row)==len(groups[-1][0][1]):
                groups[-1].append((code,row))
            else: groups.append([(code,row)])
        for group in groups:
            values=[value for _code,row in group for value in row]
            self.change(self.display,group[0][0],len(group[0][1]),(C.c_ulong*len(values))(*values),len(group))

    def wait_window_manager(self):
        # XSync confirms the server's map change, not the WM's processing of
        # MappingNotify. Ask it to answer an EWMH request on our own unmapped
        # window before returning from text input. No owner window is changed.
        if '_NET_REQUEST_FRAME_EXTENTS' not in command(['xprop','-root','_NET_SUPPORTED']): return
        p=C.c_void_p; ul=C.c_ulong
        root=function(self.lib,'XDefaultRootWindow',ul,p)(self.display)
        window=function(self.lib,'XCreateSimpleWindow',ul,p,ul,C.c_int,C.c_int,C.c_uint,C.c_uint,C.c_uint,ul,ul)(
            self.display,root,-1,-1,1,1,0,0,0)
        if not window: raise RuntimeError('Input may have occurred; cannot check window-manager readiness')
        destroy=function(self.lib,'XDestroyWindow',C.c_int,p,ul)
        atom=function(self.lib,'XInternAtom',ul,p,C.c_char_p,C.c_int)
        request=atom(self.display,b'_NET_REQUEST_FRAME_EXTENTS',False)
        reply=atom(self.display,b'_NET_FRAME_EXTENTS',False)
        event=XEvent();event.client.type=33;event.client.send_event=True;event.client.display=self.display
        event.client.window=window;event.client.message_type=request;event.client.format=32
        try:
            function(self.lib,'XSelectInput',C.c_int,p,ul,C.c_long)(self.display,window,1<<22)
            send=function(self.lib,'XSendEvent',C.c_int,p,ul,C.c_int,C.c_long,C.POINTER(XEvent))
            if not send(self.display,root,False,(1<<19)|(1<<20),C.byref(event)):
                raise RuntimeError('Input may have occurred; window-manager readiness request failed')
            self.sync(self.display,False)
            end=min(time.monotonic()+30,OPERATION_END or float('inf'))
            get=function(self.lib,'XGetWindowProperty',C.c_int,p,ul,ul,C.c_long,C.c_long,C.c_int,ul,
                         C.POINTER(ul),C.POINTER(C.c_int),C.POINTER(ul),C.POINTER(ul),C.POINTER(p))
            pending=function(self.lib,'XPending',C.c_int,p)
            next_event=function(self.lib,'XNextEvent',C.c_int,p,C.POINTER(XEvent))
            while True:
                check_stop()
                changed=False
                for _ in range(64):
                    if not pending(self.display): break
                    notification=XEvent();next_event(self.display,C.byref(notification))
                    if notification.type==28: changed=True
                actual=ul();fmt=C.c_int();count=ul();remaining=ul();data=p()
                try:
                    status=get(self.display,window,reply,0,4,False,0,C.byref(actual),C.byref(fmt),C.byref(count),C.byref(remaining),C.byref(data))
                    if changed and status==0 and actual.value==6 and fmt.value==32 and count.value==4 and remaining.value==0: return
                finally:
                    if data: self.free(data)
                if time.monotonic()>=end:
                    raise RuntimeError('Input may have occurred; window manager did not finish the keyboard-map update before its deadline')
                time.sleep(.025)
        finally: destroy(self.display,window);self.sync(self.display,False)

    def restore_slots(self,slots):
        if not isinstance(slots,dict) or len(slots)>256: raise RuntimeError('Invalid saved Unicode mapping')
        # Validate the whole journal before restoring even one slot.
        for raw_code,values in slots.items():
            if not isinstance(values,(list,tuple)) or len(values)!=2: raise RuntimeError('Invalid saved Unicode slot')
            old,new=values; code=int(raw_code)
            if not 8<=code<=255 or not isinstance(old,(list,tuple)) or not isinstance(new,(list,tuple)) or not 1<=len(old)==len(new)<=64:
                raise RuntimeError('Invalid saved Unicode keycode')
            if any(type(value) is not int or value!=0 for value in old) or not all(type(value) is int and 0<=value<2**32 for value in new):
                raise RuntimeError('Invalid saved Unicode key')
        restore={}
        for raw_code,(old,new) in slots.items():
            code=int(raw_code)
            if not 8 <= code <= 255 or not 1 <= len(old) == len(new) <= 64 or any(old): raise RuntimeError('Invalid saved Unicode mapping; no restoration performed')
            if not all(type(value) is int and 0 <= value < 2**32 for value in new): raise RuntimeError('Invalid saved Unicode key')
            columns=C.c_int(); current=self.get(self.display,code,1,C.byref(columns))
            try:
                if current and self.matches(tuple(current[i] for i in range(columns.value)),new):
                    # Also release our borrowed code after an executor was killed
                    # between XTest down/up. It was unpressed when borrowed.
                    self.fake(self.display,code,False,0)
                    restore[code]=old
            finally:
                if current: self.free(current)
        self.change_rows(restore)
        self.sync(self.display,False)

    def recover(self):
        if not self.journal.exists(): return
        if self.journal.stat().st_size>128*1024: raise RuntimeError('Saved Unicode mapping exceeds its size limit')
        saved=json.loads(self.journal.read_text(encoding='utf8'))
        if saved.get('schema')!='helene.x11.unicode.v1' or not isinstance(saved.get('nonce'),str) or not re.fullmatch('[a-f0-9]{32}',saved['nonce']) or not isinstance(saved.get('slots'),dict):
            raise RuntimeError('Saved Unicode mapping is damaged; owner inspection required')
        if saved.get('display')!=os.environ.get('DISPLAY'):
            raise RuntimeError('Unfinished input belongs to another X11 display; recover it in that session before using this one. Its journal was preserved.')
        property_value=command(['xprop','-root',self.property])
        if saved.get('display')==os.environ.get('DISPLAY') and ('"'+saved['nonce']+'"') in property_value:
            held=saved.get('held_keys',{}); buttons=saved.get('held_buttons',[])
            if not isinstance(held,dict) or len(held)>256 or not isinstance(buttons,list) or any(type(b) is not int or b not in (1,2,3) for b in buttons):
                raise RuntimeError('Saved pressed input is damaged; owner inspection required')
            for code,row in held.items():
                if not str(code).isdigit() or not 8<=int(code)<=255 or not isinstance(row,list) or not 1<=len(row)<=64 or any(type(v) is not int or not 0<=v<2**32 for v in row):
                    raise RuntimeError('Saved pressed key is damaged; owner inspection required')
            self.restore_slots(saved['slots'])
            self.release_held(held,buttons)
            command(['xprop','-root','-remove',self.property])
        self.journal.unlink()

    def save(self):
        if self.nonce is None:
            self.nonce=uuid.uuid4().hex
            # A nonce belongs to this X server, never just its DISPLAY number.
            command(['xprop','-root','-f',self.property,'8s','-set',self.property,self.nonce])
        self.journal.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.journal.with_suffix('.tmp')
        with temporary.open('w',encoding='utf8') as out:
            json.dump({'schema':'helene.x11.unicode.v1','display':os.environ.get('DISPLAY'),'nonce':self.nonce,
                       'slots':self.borrowed,'held_keys':self.held,'held_buttons':sorted(self.buttons)},out)
            out.flush(); os.fsync(out.fileno())
        os.replace(str(temporary),str(self.journal))

    def key_code(self,key):
        symbol=function(self.lib,'XStringToKeysym',C.c_ulong,C.c_char_p)(key.encode('ascii'))
        code=function(self.lib,'XKeysymToKeycode',C.c_ubyte,C.c_void_p,C.c_ulong)(self.display,symbol)
        if not code: raise RuntimeError('Key is unavailable in the current X11 layout: '+key)
        return code

    def record_key(self,key):
        code=self.key_code(key)
        if code in self.held: return True
        pressed=C.create_string_buffer(32)
        function(self.lib,'XQueryKeymap',C.c_int,C.c_void_p,C.c_void_p)(self.display,pressed)
        if pressed.raw[code//8] & (1<<(code%8)): return False
        columns=C.c_int(); current=self.get(self.display,code,1,C.byref(columns))
        try:
            if not current or not 1<=columns.value<=64: raise RuntimeError('Cannot record pressed-key recovery before input')
            self.held[code]=[current[i] for i in range(columns.value)]
        finally:
            if current: self.free(current)
        self.save(); return True

    def record_button(self,button):
        if button in self.buttons: return True
        p=C.c_void_p; ul=C.c_ulong
        root=function(self.lib,'XDefaultRootWindow',ul,p)(self.display)
        root_return,child_return=ul(),ul(); coordinates=[C.c_int() for _ in range(4)]; mask=C.c_uint()
        ok=function(self.lib,'XQueryPointer',C.c_int,p,ul,C.POINTER(ul),C.POINTER(ul),*[C.POINTER(C.c_int)]*4,C.POINTER(C.c_uint))(
            self.display,root,C.byref(root_return),C.byref(child_return),*[C.byref(v) for v in coordinates],C.byref(mask))
        if not ok: raise RuntimeError('Cannot record mouse recovery before input')
        if mask.value & (1<<(7+button)): return False
        self.buttons.add(button); self.save(); return True

    def release_held(self,held,buttons):
        def unpad(row):
            row=list(row)
            while row and row[-1]==0: row.pop()
            return row
        for raw_code,row in held.items():
            code=int(raw_code); columns=C.c_int(); current=self.get(self.display,code,1,C.byref(columns))
            try:
                if current and unpad([current[i] for i in range(columns.value)])==unpad(row): self.fake(self.display,code,False,0)
            finally:
                if current: self.free(current)
        button_event=function(self.xtest,'XTestFakeButtonEvent',C.c_int,C.c_void_p,C.c_uint,C.c_int,C.c_ulong)
        for button in buttons: button_event(self.display,button,False,0)
        self.sync(self.display,False)

    def send(self,char):
        code=self.codes[char]; columns=C.c_int(); current=self.get(self.display,code,1,C.byref(columns))
        try:
            if not current or not self.matches(tuple(current[i] for i in range(columns.value)),self.borrowed[code][1]):
                raise RuntimeError('Owner keyboard layout changed; input stopped')
        finally:
            if current: self.free(current)
        if not self.fake(self.display,code,True,0): raise RuntimeError('XTest did not accept key down')
        if not self.fake(self.display,code,False,0): raise RuntimeError('XTest did not accept key up')
        self.sync(self.display,False); time.sleep(.012)

    def check_focus(self,expected):
        if expected is None: raise RuntimeError('No active X11 window for keyboard input')
        p=C.c_void_p; ul=C.c_ulong; focus=ul(); revert=C.c_int()
        function(self.lib,'XGetInputFocus',C.c_int,p,C.POINTER(ul),C.POINTER(C.c_int))(self.display,C.byref(focus),C.byref(revert))
        current=focus.value
        query=function(self.lib,'XQueryTree',C.c_int,p,ul,C.POINTER(ul),C.POINTER(ul),C.POINTER(C.POINTER(ul)),C.POINTER(C.c_uint))
        for _ in range(64):
            if current==expected: return
            if current in (0,1): break
            root,parent=ul(),ul(); children=C.POINTER(ul)(); count=C.c_uint()
            ok=query(self.display,current,C.byref(root),C.byref(parent),C.byref(children),C.byref(count))
            if children: self.free(children)
            if not ok or parent.value==current: break
            current=parent.value
        raise RuntimeError('Keyboard focus left the foreground target; input stopped. Focus the desired element before typing.')

    @staticmethod
    def matches(current,expected):
        # XKB can expand/pad groups in XGetKeyboardMapping. No distinct symbol
        # is admitted: an owner replacement still aborts before the next key.
        return bool(current and expected and current[0]==expected[0]
                    and all(value in (0,expected[0]) for value in current))

    def close(self):
        if not self.display: return
        # Give the client time to consume the last event before changing its map.
        if self.borrowed: self.sync(self.display,False); time.sleep(.08)
        try:
            self.restore_slots(self.borrowed)
            self.release_held(self.held,self.buttons)
            if self.nonce:
                property_value=command(['xprop','-root',self.property])
                if ('"'+self.nonce+'"') in property_value: command(['xprop','-root','-remove',self.property])
                self.journal.unlink(missing_ok=True)
            if self.borrowed: self.wait_window_manager()
        finally:
            self.close_display(self.display); self.display=None


def perform(args):
    events, delay = input_plan(args)  # the entire batch is validated before the first action
    guard(args)
    unicode_keys=UnicodeKeys(events)
    held_keys, held_buttons, released = {}, set(), []
    completed = 0
    try:
        for index, event in enumerate(events):
            target=guard(args)
            kind = event["type"]
            if kind == "text":
                # Every character rechecks focus/PID, including long Cyrillic text.
                for char in event["text"]:
                    unicode_keys.check_focus(guard(args))
                    unicode_keys.send(char)
            elif kind == "key":
                unicode_keys.check_focus(target)
                key, action = key_name(event["key"]), event.get("action", "press")
                owned=unicode_keys.record_key(key) if action in ('down','press') else False
                xdo({"down": "keydown", "up": "keyup", "press": "key"}[action], key)
                if action == "down" and owned: held_keys[key] = event["key"]
                if action == "up": held_keys.pop(key, None)
                if action in ('up','press'):
                    unicode_keys.held.pop(unicode_keys.key_code(key),None)
                    if unicode_keys.nonce: unicode_keys.save()
            elif kind == 'hotkey':
                chord=[key_name(key) for key in event['keys']]
                try:
                    for key,original in zip(chord,event['keys']):
                        unicode_keys.check_focus(guard(args))
                        owned=unicode_keys.record_key(key); xdo('keydown',key)
                        if owned: held_keys[key]=original
                finally:
                    for key in reversed(chord):
                        if key in held_keys:
                            xdo('keyup',key); held_keys.pop(key,None)
                            unicode_keys.held.pop(unicode_keys.key_code(key),None); unicode_keys.save()
            elif kind == "mouse":
                if "x" in event: xdo('mousemove_relative' if event.get('relative') else 'mousemove', "--", event["x"], event["y"])
                if "button" in event:
                    guard(args)
                    button, action = BUTTONS[event["button"]], event.get("action", "click")
                    owned=unicode_keys.record_button(button) if action in ('down','click','double_click') else False
                    if action == "double_click": xdo("click", "--repeat", "2", "--delay", "100", button)
                    else: xdo({"down": "mousedown", "up": "mouseup", "click": "click"}[action], button)
                    if action == "down" and owned: held_buttons.add(button)
                    if action == "up": held_buttons.discard(button)
                    if action!='down':
                        unicode_keys.buttons.discard(button)
                        if unicode_keys.nonce: unicode_keys.save()
            elif kind in ('click','mouse_down','mouse_up','drag'):
                button=BUTTONS[event.get('button','left')]
                if 'x' in event: xdo('mousemove','--',event['x'],event['y'])
                guard(args)
                owned=unicode_keys.record_button(button) if kind!='mouse_up' else False
                if kind=='click':
                    for _ in range(event.get('count',1)): guard(args); xdo('click',button)
                elif kind=='mouse_down':
                    xdo('mousedown',button)
                    if owned: held_buttons.add(button)
                elif kind=='mouse_up': xdo('mouseup',button); held_buttons.discard(button)
                else:
                    xdo('mousedown',button)
                    if owned: held_buttons.add(button)
                    time.sleep(event.get('hold_ms',100)/1000)
                    steps=event.get('steps',20)
                    for step in range(1,steps+1):
                        guard(args)
                        x=round(event['x']+(event['to_x']-event['x'])*step/steps)
                        y=round(event['y']+(event['to_y']-event['y'])*step/steps)
                        xdo('mousemove','--',x,y); time.sleep(event.get('step_delay_ms',10)/1000)
                    guard(args); xdo('mouseup',button); held_buttons.discard(button)
                    time.sleep(event.get('settle_ms',100)/1000)
                if kind!='mouse_down':
                    unicode_keys.buttons.discard(button)
                    if unicode_keys.nonce: unicode_keys.save()
            elif kind in ('scroll','wheel'):
                delta = event["delta"]
                if 'x' in event: xdo('mousemove','--',event['x'],event['y'])
                horizontal=event.get('horizontal',False) if kind=='wheel' else event.get('axis','vertical')=='horizontal'
                buttons = (6,7) if horizontal else (4,5)
                for _ in range(min(100, (abs(delta) + 119) // 120)):
                    guard(args); xdo("click", buttons[0 if delta > 0 else 1])
            else: time.sleep(event["ms"] / 1000.)
            completed += 1
            if delay and index + 1 < len(events): time.sleep(delay / 1000.)
    except Exception as error:
        raise RuntimeError(str(error)+'; input may have partially occurred; completed_events='+str(completed)+
                           '. Do not repeat this input with a new request key.') from error
    finally:
        for key, original in held_keys.items():
            try: xdo("keyup", key); released.append(original)
            except (RuntimeError, subprocess.TimeoutExpired): pass
        for button in held_buttons:
            try: xdo("mouseup", button)
            except (RuntimeError, subprocess.TimeoutExpired): pass
        unicode_keys.close()
    return {"ok": True, "platform": "linux", "events_completed": completed, "modifiers_auto_released": released,
            'limits':{'max_events':500,'max_text_chars':2048,'delay_budget_ms':30000,'operation_deadline_ms':45000}}


def windows(args):
    if set(args)-{'offset','limit','visible_only','pid','title_contains','all_layers','timeout_ms'}: raise ValueError('unknown window list fields')
    if 'timeout_ms' in args: integer(args['timeout_ms'],'timeout_ms',1,30000)
    offset=integer(args.get('offset',0),'offset',0,100000); limit=integer(args.get('limit',2048),'limit',1,10000)
    if args.get('pid') is not None: integer(args['pid'],'pid',1,2**32-1)
    if not isinstance(args.get('title_contains',''),str): raise ValueError('title_contains must be a string')
    for name in ('visible_only','all_layers'):
        if name in args and type(args[name]) is not bool: raise ValueError(name+' must be boolean')
    raw = command(["xprop", "-root", "_NET_CLIENT_LIST_STACKING"])
    handles = re.findall(r"0x[0-9a-fA-F]+", raw)
    try: foreground=handle(xdo('getactivewindow'))
    except (RuntimeError,ValueError): foreground=None
    items = []
    for value in handles[:10000]:
        hwnd = handle(value)
        try:
            pid = pid_of(hwnd)
            if args.get('pid') is not None and pid!=args['pid']: continue
            title = window_title(hwnd)[:4096]
            if args.get("title_contains") and str(args["title_contains"]).casefold() not in title.casefold(): continue
            minimized='_NET_WM_STATE_HIDDEN' in property_of(hwnd,'_NET_WM_STATE')
            if args.get('visible_only',True) and minimized: continue
            executable = ""
            if pid:
                try: executable = os.readlink("/proc/%s/exe" % pid)
                except OSError: pass
            items.append({"hwnd": "0x%X" % hwnd, "title": title, "pid": pid, "rect": geometry(hwnd),
                          "process_path": executable, "class": property_of(hwnd, "WM_CLASS")[:1024],
                          "window_type": property_of(hwnd, "_NET_WM_WINDOW_TYPE")[:512],
                          "minimized": minimized, 'foreground':hwnd==foreground,
                          "fingerprint": hashlib.sha256((str(hwnd) + ":" + str(pid) + ":" + executable).encode()).hexdigest()})
        except (RuntimeError, subprocess.TimeoutExpired): continue  # a closed window is omitted
    page=items[offset:offset+limit]
    return {"ok": True, "platform": "linux", "items":page, 'offset':offset,'limit':limit,'returned':len(page),
            'has_more':len(items)>offset+limit or len(handles)>10000, 'total_known':len(handles)<=10000, 'total':len(items),
            'notes':['EWMH-managed windows only; compositor overlays are unavailable even with all_layers=true.']}


class XImage(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("width", "height", "xoffset", "format")] + [("data", C.c_void_p)] + [
        (name, C.c_int) for name in ("byte_order", "bitmap_unit", "bitmap_bit_order", "bitmap_pad", "depth", "bytes_per_line", "bits_per_pixel")] + [
        (name, C.c_ulong) for name in ("red_mask", "green_mask", "blue_mask")]


def function(lib, name, result, *args):
    fn = getattr(lib, name); fn.restype = result; fn.argtypes = list(args); return fn


def capture_rectangle(rectangle, width, height, notes):
    x = integer(rectangle.get('x'), 'rect.x', -(2**31), 2**31-1)
    y = integer(rectangle.get('y'), 'rect.y', -(2**31), 2**31-1)
    w = integer(rectangle.get('width'), 'width', 1, 2**31-1)
    h = integer(rectangle.get('height'), 'height', 1, 2**31-1)
    left, top = max(0, x), max(0, y)
    right, bottom = min(width, x+w), min(height, y+h)
    if right <= left or bottom <= top:
        raise ValueError('capture rectangle has no visible area on the X11 screen')
    result = {'x':left, 'y':top, 'width':right-left, 'height':bottom-top}
    if result != rectangle:
        notes.append('Only the visible intersection of the requested rectangle and the X11 screen was captured.')
    return result


def capture(args, state):
    if set(args)-{'target','hwnd','x','y','width','height','rect','name'}: raise ValueError('unknown capture fields')
    name=args.get('name','capture.png')
    if not isinstance(name,str) or not name.strip() or len(name)>128 or any(char in name for char in '/\\\0') or any(ord(char)<32 for char in name) or name in ('.','..'):
        raise ValueError('capture name must be a plain filename of at most 128 characters')
    if not name.lower().endswith('.png'): name+='.png'
    notes=[]
    lib = C.CDLL("libX11.so.6")
    display = function(lib, "XOpenDisplay", C.c_void_p, C.c_char_p)(None)
    if not display: raise RuntimeError("X11 display connection failed")
    close = function(lib, "XCloseDisplay", C.c_int, C.c_void_p)
    image = None
    try:
        root = function(lib, "XDefaultRootWindow", C.c_ulong, C.c_void_p)(display)
        screen = function(lib, "XDefaultScreen", C.c_int, C.c_void_p)(display)
        width = function(lib, "XDisplayWidth", C.c_int, C.c_void_p, C.c_int)(display, screen)
        height = function(lib, "XDisplayHeight", C.c_int, C.c_void_p, C.c_int)(display, screen)
        rectangle = geometry(handle(args["hwnd"])) if args.get("hwnd") else {"x": 0, "y": 0, "width": width, "height": height}
        target_kind = args.get('target', 'desktop')
        if target_kind not in ('desktop', 'window', 'region'): raise ValueError('capture target must be desktop/window/region')
        if target_kind == 'window' and not args.get('hwnd'): raise ValueError('window capture requires hwnd')
        if target_kind == 'region': rectangle = {name: args.get(name) for name in ('x', 'y', 'width', 'height')}
        if 'rect' in args:
            if not isinstance(args['rect'],dict) or set(args['rect'])!={'x','y','width','height'}: raise ValueError('capture rect requires x/y/width/height')
            rectangle=args['rect']
        rectangle = capture_rectangle(rectangle, width, height, notes)
        if target_kind=='window' and 'rect' not in args:
            notes.append('Window capture reads its visible screen rectangle; other windows may cover it.')
        x, y = integer(rectangle.get("x"), "rect.x", 0, width-1), integer(rectangle.get("y"), "rect.y", 0, height-1)
        w, h = integer(rectangle.get("width"), "width", 1, width-x), integer(rectangle.get("height"), "height", 1, height-y)
        if w * h > 16_000_000: raise ValueError("capture exceeds 16 million pixels")
        image = function(lib, "XGetImage", C.POINTER(XImage), C.c_void_p, C.c_ulong,
                         C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_ulong, C.c_int)(display, root, x, y, w, h, C.c_ulong(-1).value, 2)
        if not image: raise RuntimeError("X11 capture failed")
        info = image.contents
        if info.bits_per_pixel not in (16, 24, 32) or not 0 < info.bytes_per_line * h <= 128 * 1024 * 1024:
            raise RuntimeError("unsupported or oversized X11 pixel layout")
        data = C.string_at(info.data, info.bytes_per_line * h)
        masks = [info.red_mask, info.green_mask, info.blue_mask]
        shifts = [(mask & -mask).bit_length()-1 for mask in masks]
        maxima = [mask >> shift for mask, shift in zip(masks, shifts)]
        if not all(maxima): raise RuntimeError("unsupported X11 color masks")
        compressor = zlib.compressobj(3); compressed = bytearray()
        bpp = info.bits_per_pixel // 8
        for row in range(h):
            pixels = bytearray(b"\0")
            for column in range(w):
                start = row * info.bytes_per_line + column * bpp
                pixel = int.from_bytes(data[start:start+bpp], "little" if info.byte_order == 0 else "big")
                pixels.extend(((pixel & mask) >> shift) * 255 // maximum for mask, shift, maximum in zip(masks, shifts, maxima))
            compressed.extend(compressor.compress(pixels))
        compressed.extend(compressor.flush())
        def chunk(kind, content): return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xffffffff)
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
        target = Path(state) / "captures" / uuid.uuid4().hex / name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(png)
        return {"ok": True, "platform": "linux", "path": str(target.resolve()), "mime": "image/png", "width": w, "height": h,
                "rect": {"x": x, "y": y, "width": w, "height": h}, 'notes':notes}
    finally:
        if image: function(lib, "XDestroyImage", C.c_int, C.POINTER(XImage))(image)
        close(display)


class Rect(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "width", "height")]


class GError(C.Structure):
    _fields_=[('domain',C.c_uint),('code',C.c_int),('message',C.c_char_p)]


class Atspi:
    def __init__(self, timeout):
        self.lib = C.CDLL("libatspi.so.0"); self.glib = C.CDLL("libglib-2.0.so.0"); self.gobject = C.CDLL("libgobject-2.0.so.0")
        self.free = function(self.glib, "g_free", None, C.c_void_p)
        self.unref = function(self.gobject, "g_object_unref", None, C.c_void_p)
        self.fn = {}
        p, err = C.c_void_p, C.POINTER(C.c_void_p)
        error_free=function(self.glib,'g_error_free',None,C.c_void_p)
        self.deadline=time.monotonic()+timeout/1000
        signatures = {
            "init": (C.c_int, []), "get_desktop": (p, [C.c_int]), "set_timeout": (None, [C.c_int, C.c_int]),
            "accessible_get_child_count": (C.c_int, [p, err]), "accessible_get_child_at_index": (p, [p, C.c_int, err]),
            "accessible_get_process_id": (C.c_uint, [p, err]), "accessible_get_name": (p, [p, err]),
            "accessible_get_role_name": (p, [p, err]), "accessible_get_state_set": (p, [p]),
            "state_set_contains": (C.c_int, [p, C.c_int]), "component_get_extents": (C.POINTER(Rect), [p, C.c_int, err]),
            "component_grab_focus": (C.c_int, [p, err]), "text_get_text": (p, [p, C.c_int, C.c_int, err]),
            "text_get_character_count": (C.c_int, [p, err]),
            "editable_text_set_text_contents": (C.c_int, [p, C.c_char_p, err]),
            "action_get_n_actions": (C.c_int, [p, err]), "action_get_action_name": (p, [p, C.c_int, err]),
            "action_do_action": (C.c_int, [p, C.c_int, err]), 'accessible_clear_cache':(None,[p])}
        for iface in ("component", "text", "editable_text", "action"):
            signatures["accessible_get_" + iface + "_iface"] = (p, [p])
        for name, (result, arguments) in signatures.items():
            native=function(self.lib, 'atspi_'+name,result,*arguments)
            if arguments and arguments[-1]==err:
                def checked(*values, native=native, name=name):
                    remaining=round((self.deadline-time.monotonic())*1000)
                    if remaining<=0: raise TimeoutError('AT-SPI read deadline reached before '+name)
                    self.fn['set_timeout'](min(remaining,1500),min(remaining,1500))
                    error=C.c_void_p()
                    value=native(*values[:-1],C.byref(error))
                    if error.value:
                        try:
                            detail=C.cast(error,C.POINTER(GError)).contents.message
                            raise RuntimeError('AT-SPI '+name+' failed: '+(detail.decode('utf8','replace') if detail else 'native error'))
                        finally: error_free(error)
                    return value
                self.fn[name]=checked
            else: self.fn[name]=native
        self.fn["set_timeout"](min(timeout, 1500), min(timeout, 1500))
        if self.fn["init"]() not in (0, 1): raise RuntimeError("AT-SPI accessibility bus is unavailable")
        self.owned = []

    def own(self, pointer):
        if pointer: self.owned.append(pointer)
        return pointer

    def close(self):
        for pointer in reversed(self.owned): self.unref(pointer)

    def text(self, name, *arguments):
        pointer = self.fn[name](*arguments)
        if not pointer: return ""
        try: return C.string_at(pointer).decode("utf8", "replace")
        finally: self.free(pointer)

    def iface(self, pointer, name):
        return self.own(self.fn["accessible_get_" + name + "_iface"](pointer))

    def rect(self, pointer):
        component = self.iface(pointer, "component")
        if not component: return None
        bounds = self.fn["component_get_extents"](component, 0, None)
        if not bounds: return None
        try: return {name: getattr(bounds.contents, name) for name in ("x", "y", "width", "height")}
        finally: self.free(bounds)


def read_limits(args, resolve=True):
    allowed = {"hwnd", "shape", "max_nodes", "max_depth", "max_children_per_node", "max_text_chars", "timeout_ms", "backend", "text_contains", "visible_only"}
    if set(args) - allowed: raise ValueError("unknown accessibility read fields")
    hwnd = handle(args['hwnd']) if args.get('hwnd') is not None else handle(xdo('getactivewindow')) if resolve else None
    if args.get("shape", "tree") not in ("flat", "tree"): raise ValueError("shape must be flat/tree")
    if args.get("backend", "auto") not in ("auto", "atspi"): raise ValueError("Linux window reader uses AT-SPI; win32 backend is unavailable")
    limits = {}
    for name, default, maximum in (("max_nodes", 600, 10000), ("max_depth", 12, 64), ("max_children_per_node", 100, 1000),
                                    ("max_text_chars", 4000, 100000), ("timeout_ms", 5000, 30000)):
        limits[name] = min(integer(args.get(name, default), name, 0, 2**64 - 1) or default, maximum)
    if "visible_only" in args and type(args["visible_only"]) is not bool: raise ValueError("visible_only must be boolean")
    if "text_contains" in args and not isinstance(args["text_contains"], str): raise ValueError("text_contains must be a string")
    return hwnd, limits


def filter_tree(root, args):
    """Retain matching nodes and the ancestors needed to reach them."""
    if root is None: return None, 0
    children=[]; count=0
    for child in root['children']:
        kept, size=filter_tree(child,args)
        if kept is not None: children.append(kept); count+=size
    matches=(not args.get('visible_only') or root['state']['visible']) and (
        not args.get('text_contains') or args['text_contains'].casefold() in (root['name']+' '+root['value']).casefold())
    if not matches and not children: return None, 0
    return {**root,'children':children}, count+1


def accessibility_window_binding(named, bounds):
    roots = [pointer for pointer, rect in named if not ((rect['x'],rect['y']) == (0,0) and (bounds['x'],bounds['y']) != (0,0)) and abs(rect['x']-bounds['x']) <= 40 and abs(rect['y']-bounds['y']) <= 60]
    if len(roots) == 1:
        return roots[0], False
    if not roots and len(named) == 1:
        pointer, rect = named[0]
        if (rect['x'], rect['y']) == (0, 0) and all(rect[k] == bounds[k] for k in ('width', 'height')):
            return pointer, True
    raise RuntimeError('AT-SPI window binding is absent or ambiguous; no action performed')


def equivalent_x11_windows(pid, title, bounds):
    handles = re.findall(r'0x[0-9a-fA-F]+', command(['xprop','-root','_NET_CLIENT_LIST_STACKING']))
    if len(handles) > 10000:
        raise RuntimeError('X11 peer observation exceeds its supported window limit')
    matches = []
    for value in handles:
        hwnd = handle(value)
        if pid_of(hwnd) == pid and window_title(hwnd) == title:
            rect = geometry(hwnd)
            if all(rect[k] == bounds[k] for k in ('width','height')):
                matches.append(hwnd)
    return matches


def accessible(args, capability):
    # The Linux branch receives raw JSON rather than the Windows serde plan.
    # Validate selection and action fields before even observing the target.
    read_fields={'hwnd','shape','max_nodes','max_depth','max_children_per_node','max_text_chars','timeout_ms','backend','text_contains','visible_only'}
    allowed=read_fields if capability=='desktop.window.read' else read_fields|{'select','expected_pid','limit'}
    if capability=='desktop.element.act': allowed|={'do','text','nth'}
    if set(args)-allowed: raise ValueError('unknown accessibility fields: '+', '.join(sorted(set(args)-allowed)))
    select=args.get('select')
    if capability!='desktop.window.read':
        if not isinstance(select,dict) or not select or set(select)-{'role','name','name_contains','value_contains','enabled','visible','nth','automation_id'}:
            raise ValueError('select requires known AT-SPI selection fields')
        if 'automation_id' in select: raise ValueError('automation_id is unavailable in this Linux adapter; select by role/name/value')
        for name in ('role','name','name_contains','value_contains'):
            if name in select and (not isinstance(select[name],str) or not select[name].strip()): raise ValueError('selector '+name+' is blank or is not text')
        for name in ('enabled','visible'):
            if name in select and type(select[name]) is not bool: raise ValueError('selector '+name+' must be boolean')
        if not any(name in select for name in ('role','name','name_contains','value_contains')): raise ValueError('selection must name a stable element property')
        if select.get('nth') is not None: integer(select['nth'],'select.nth',0,10000)
        if capability=='desktop.element.find' and 'nth' in select: raise ValueError('find returns candidates; nth is only available for an action')
        integer(args.get('limit',50),'limit',1,1000)
    if capability=='desktop.element.act':
        operation=args.get('do')
        if operation not in ('invoke','set_value','focus','toggle'): raise ValueError('unsupported AT-SPI element action; use invoke/set_value/focus/toggle')
        if operation=='set_value':
            if not isinstance(args.get('text'),str) or len(args['text'])>100000 or '\0' in args['text']: raise ValueError('invalid element text')
        elif 'text' in args: raise ValueError('text is only used by set_value')
        if args.get('nth') is not None: integer(args['nth'],'nth',0,10000)
        if args.get('nth') is not None and select.get('nth') is not None: raise ValueError('provide nth only once')
    read_args = {key: value for key, value in args.items() if key not in ("select", "do", "text", "nth", "expected_pid", "limit")}
    hwnd, limits = read_limits(read_args)
    pid = pid_of(hwnd); title = window_title(hwnd); bounds = geometry(hwnd)
    if not pid: raise RuntimeError("window process identity unavailable; accessibility target cannot be bound")
    if args.get("expected_pid") is not None and pid != integer(args["expected_pid"], "expected_pid", 1, 2**32-1):
        raise RuntimeError("window process identity changed")
    started = time.monotonic(); end = started + limits["timeout_ms"] / 1000.
    spi = Atspi(limits["timeout_ms"])
    items, pointers, truncated = [], [], []
    notes=[name+'='+str(read_args[name])+' was limited to '+str(value) for name,value in limits.items()
           if name in read_args and read_args[name]!=value]
    text_bytes=0
    try:
        desktop = spi.own(spi.fn["get_desktop"](0)); named = []
        if not desktop: raise RuntimeError("AT-SPI desktop unavailable")
        applications=spi.fn['accessible_get_child_count'](desktop,None)
        if not 0<=applications<=500: raise RuntimeError('AT-SPI application binding is incomplete; no action performed')
        for index in range(applications):
            if time.monotonic() >= end: raise RuntimeError("AT-SPI window binding timed out")
            app = spi.own(spi.fn["accessible_get_child_at_index"](desktop, index, None))
            if not app or spi.fn["accessible_get_process_id"](app, None) != pid: continue
            app_children=spi.fn['accessible_get_child_count'](app,None)
            if not 0<=app_children<=500: raise RuntimeError('AT-SPI window binding is incomplete; no action performed')
            for child_index in range(app_children):
                window = spi.own(spi.fn["accessible_get_child_at_index"](app, child_index, None))
                if not window: continue
                name, rectangle = spi.text("accessible_get_name", window, None), spi.rect(window)
                if name == title and rectangle:
                    named.append((window, rectangle))
        # GTK4 on X11 may report a window-local origin even for SCREEN extents.
        # Bind a native pattern only when PID+title identify exactly one root
        # and its client size agrees with the independently observed X window.
        # Never reinterpret these reported element positions as screen pixels.
        bound_window, local_coordinates = accessibility_window_binding(named, bounds)
        if local_coordinates:
            equivalent = equivalent_x11_windows(pid, title, bounds)
            if equivalent != [hwnd]:
                raise RuntimeError('X11 window identity is ambiguous for AT-SPI origin-unverified binding; no action performed')
            notes.append('AT-SPI root origin is unverified; element rectangles must not be used as screen coordinates')

        def walk(pointer, depth, path):
            nonlocal text_bytes
            if time.monotonic() >= end or len(items) >= limits["max_nodes"]:
                truncated.append("time_or_nodes"); return None
            role = spi.text("accessible_get_role_name", pointer, None)
            name = spi.text("accessible_get_name", pointer, None)[:limits["max_text_chars"]]
            states = spi.own(spi.fn["accessible_get_state_set"](pointer))
            showing = bool(states and spi.fn["state_set_contains"](states, 25))
            enabled = bool(states and spi.fn["state_set_contains"](states, 8))
            rectangle = spi.rect(pointer)
            if rectangle:
                rectangle.update(left=rectangle['x'], top=rectangle['y'], center={'x':rectangle['x']+rectangle['width']//2,'y':rectangle['y']+rectangle['height']//2}, coordinate_space='reported_origin_unverified' if local_coordinates else 'screen')
            editable, action, text_iface = spi.iface(pointer, "editable_text"), spi.iface(pointer, "action"), spi.iface(pointer, "text")
            patterns = (["set_value"] if editable else []) + (["invoke"] if action else [])
            text_length = max(0, spi.fn['text_get_character_count'](text_iface, None)) if text_iface else 0
            value = "" if "password" in role else (spi.text("text_get_text", text_iface, 0, min(text_length, limits["max_text_chars"]), None) if text_iface else "")
            text_bytes+=len(name.encode('utf8'))+len(value.encode('utf8'))+len(role.encode('utf8'))
            if text_bytes>4*1024*1024:
                truncated.append('text_byte_budget'); return None
            row = {"id": path, "role": {"text": "edit", "entry": "edit", "password text": "edit", "push button": "button", "check box": "checkbox"}.get(role, role),
                   "name": name, "value": value, "patterns": patterns, "rect": rectangle,
                   "state": {"enabled": enabled, "visible": showing}, "depth": depth, "children": []}
            items.append(row); pointers.append(pointer)
            count = spi.fn["accessible_get_child_count"](pointer, None)
            if count<0: raise RuntimeError('AT-SPI child enumeration failed; no complete tree was established')
            if depth >= limits["max_depth"]:
                if count: truncated.append("depth")
                return row
            if count > limits["max_children_per_node"]: truncated.append("children")
            walked=min(count, limits["max_children_per_node"])
            for child_index in range(walked):
                child = spi.own(spi.fn["accessible_get_child_at_index"](pointer, child_index, None))
                if child:
                    result = walk(child, depth+1, path + "." + str(child_index))
                    if result is not None: row["children"].append(result)
                else: truncated.append('unavailable_child')
                if time.monotonic() >= end or len(items) >= limits["max_nodes"] or text_bytes>4*1024*1024:
                    if child_index+1<walked: truncated.append('time_or_nodes')
                    break
            return row

        root = walk(bound_window, 0, "0")
        result = {"ok": True, "platform": "linux", "backend": "atspi", "hwnd": "0x%X" % hwnd, "pid": pid,
                  "shape":args.get('shape','tree'), "partial": bool(truncated), "truncated": bool(truncated),
                  "truncated_by":sorted(set(truncated)), "total_known":not truncated,
                  "nodes_read":len(items), "depth_reached":max((item['depth'] for item in items),default=0),
                  "limits":{**limits,'max_text_bytes':4*1024*1024}, 'notes':notes, "elapsed_ms": round((time.monotonic()-started)*1000),
                  "window":{'hwnd':'0x%X' % hwnd,'title':title,'pid':pid,'rect':{**bounds,'left':bounds['x'],'top':bounds['y']}}}
        if capability == "desktop.window.read":
            if args.get("shape", "tree") == "flat":
                result["items"] = [{k: v for k, v in item.items() if k != "children"} for item in items
                                   if (not args.get("visible_only") or item["state"]["visible"])
                                   and (not args.get("text_contains") or args["text_contains"].casefold() in (item["name"] + " " + item["value"]).casefold())]
                result['nodes_returned']=len(result['items'])
            else:
                result['root'],result['nodes_returned']=filter_tree(root,args)
            return result
        select = args.get("select")
        if not isinstance(select, dict) or not select or set(select) - {"role", "name", "name_contains", "value_contains", "enabled", "visible", "nth", "automation_id"}:
            raise ValueError("select requires known AT-SPI selection fields")
        if 'automation_id' in select: raise ValueError('automation_id is unavailable in this Linux adapter; select by role/name/value')
        if not any(key in select for key in ('role','name','name_contains','value_contains')): raise ValueError('selection must name a stable element property')
        nth = select.get('nth', args.get('nth'))
        conditions = {key:value for key,value in select.items() if key != 'nth'}
        matched = []
        for index, row in enumerate(items):
            if all((isinstance(value,str) and row.get(key,'').casefold() == value.casefold() if key in ("role", "name") else
                    row["state"][key] == value if key in ("enabled", "visible") else
                    isinstance(value, str) and value.casefold() in row["name" if key == "name_contains" else "value"].casefold()) for key, value in conditions.items()):
                matched.append(index)
        shown=integer(args.get('limit',50),'limit',1,1000)
        result.update(matched=len(matched), items=[{k:v for k,v in items[index].items() if k != "children"} for index in matched[:shown]])
        result.update(elements=result['items'], searched_whole_window=not truncated,
                      nodes_scanned=len(items), shown=min(len(matched),args.get('limit',50)))
        if capability == "desktop.element.find": return result
        if truncated: raise RuntimeError("accessibility search was partial; action refused")
        if not matched or (nth is None and len(matched) != 1): raise RuntimeError("element target absent or ambiguous; no action performed")
        selected = matched[integer(nth, "nth", 0, len(matched)-1)] if nth is not None else matched[0]
        pointer = pointers[selected]; operation = args.get("do")
        # Selection and PID are rebound immediately before the actual native action.
        if pid_of(hwnd) != pid: raise RuntimeError("window process changed; element action refused")
        check_stop()
        spi.fn['accessible_clear_cache'](pointer)
        fresh={}
        if 'name' in conditions or 'name_contains' in conditions: fresh['name']=spi.text('accessible_get_name',pointer,None)
        if 'role' in conditions:
            role=spi.text('accessible_get_role_name',pointer,None)
            fresh['role']={'text':'edit','entry':'edit','password text':'edit','push button':'button','check box':'checkbox'}.get(role,role)
        if 'value_contains' in conditions:
            role=spi.text('accessible_get_role_name',pointer,None)
            iface=spi.iface(pointer,'text')
            length=max(0,spi.fn['text_get_character_count'](iface,None)) if iface else 0
            fresh['value']='' if 'password' in role or not iface else spi.text('text_get_text',iface,0,min(length,limits['max_text_chars']),None)
        if 'enabled' in conditions or 'visible' in conditions:
            states=spi.own(spi.fn['accessible_get_state_set'](pointer))
            if not states: raise RuntimeError('Selected element state became unavailable; action refused')
            fresh['enabled']=bool(spi.fn['state_set_contains'](states,8)); fresh['visible']=bool(spi.fn['state_set_contains'](states,25))
        for key,value in conditions.items():
            current=fresh.get({'name_contains':'name','value_contains':'value'}.get(key,key))
            matches=(value.casefold() in current.casefold() if key in ('name_contains','value_contains') else current.casefold()==value.casefold()) if isinstance(value,str) and isinstance(current,str) else current==value
            if not matches: raise RuntimeError('Selected element changed before its native action; no action performed')
        if operation == "set_value":
            text = args.get("text")
            if not isinstance(text, str) or len(text) > 100000 or "\0" in text: raise ValueError("invalid element text")
            iface = spi.iface(pointer, "editable_text")
            ok = bool(iface and spi.fn["editable_text_set_text_contents"](iface, text.encode("utf8"), None))
        elif operation == "focus":
            iface = spi.iface(pointer, "component")
            if not iface: raise RuntimeError('AT-SPI focus interface unavailable; no action performed')
            # GTK widget focus and the X keyboard focus are separate. The owner
            # explicitly requested focus, so bring its bound window into focus too.
            _dispatch('desktop.window.activate',{'hwnd':hwnd,'expected_pid':pid},'.')
            xdo('windowfocus',hwnd)
            ok = bool(spi.fn["component_grab_focus"](iface, None))
        elif operation in ("invoke", "toggle"):
            iface = spi.iface(pointer, "action"); choices = []
            if iface:
                for index in range(min(100, spi.fn["action_get_n_actions"](iface, None))):
                    name = spi.text("action_get_action_name", iface, index, None).lower()
                    if name in (("click", "press", "activate") if operation == "invoke" else ("toggle", "click")): choices.append(index)
            if len(choices) != 1: raise RuntimeError("AT-SPI native action unavailable or ambiguous; no click fallback")
            ok = bool(spi.fn["action_do_action"](iface, choices[0], None))
        else: raise ValueError("unsupported AT-SPI element action; use invoke/set_value/focus/toggle")
        if not ok: raise RuntimeError("AT-SPI did not confirm element action")
        result.update(action=operation, did=operation, element_before=items[selected], element_after=None,
                      waited_ms=result['elapsed_ms'], notes=[*notes, 'Native pattern was called; resulting state is not independently observed.'])
        return result
    finally: spi.close()


def _dispatch(capability, args, state):
    if not isinstance(args, dict): raise ValueError("arguments must be an object")
    kind, reason = session()
    if capability == "os.process.list":
        if args.get('session_id') is not None: raise ValueError('session_id is a Windows filter and is unavailable on Linux')
        if set(args)-{'offset','limit','name_contains','session_id'}: raise ValueError('unknown process list fields')
        offset=integer(args.get('offset',0),'offset',0,100000)
        limit = integer(args.get("limit", 2048), "limit", 1, 20000); items = []; unavailable=0; path_unavailable=0
        if not isinstance(args.get('name_contains',''),str): raise ValueError('name_contains must be text')
        for folder in sorted((p for p in Path('/proc').iterdir() if p.name.isdigit()),key=lambda p:int(p.name)):
            try:
                name = (folder / "comm").read_text(encoding='utf8',errors='replace').removesuffix('\n')
            except FileNotFoundError: continue  # process exited after directory enumeration
            except OSError: name=''; unavailable+=1
            if args.get("name_contains", "").casefold() not in name.casefold(): continue
            try:
                executable=os.readlink(str(folder/'exe'))
                executable.encode('utf8')  # JSON/Rust paths cannot represent POSIX surrogate escapes
            except (OSError,UnicodeError): executable=''; path_unavailable+=1
            items.append({'pid':int(folder.name),'name':name,'path':executable})
        page=items[offset:offset+limit]
        return {"ok": True, "platform": "linux", "items": page,'offset':offset,'limit':limit,'returned':len(page),
                'total':len(items),'total_known':not unavailable,'has_more':len(items)>offset+limit,
                'names_unavailable':unavailable,'paths_unavailable':path_unavailable,
                'notes':['Process names/paths may be unavailable by OS permissions; total is a lower bound when names are unavailable.'] if unavailable or path_unavailable else []}
    if capability == "desktop.status":
        result = {"ok": True, "platform": "linux", "console": kind == "x11", "session": {"kind": kind},
                  "reason": reason, "hints":[reason] if reason else [], "atspi": {"bus": False},
                  "xtest": False, "window_manager": False}
        if kind == "x11":
            try:
                size = [int(value) for value in xdo("getdisplaygeometry").split()]
                result.update(virtual_screen={"x":0,"y":0,"width":size[0],"height":size[1]},
                              window_manager="window id" in command(["xprop", "-root", "_NET_SUPPORTING_WM_CHECK"]),
                              xtest="XTEST" in command(["xdpyinfo", "-queryExtensions"]))
                if not result['window_manager'] or not result['xtest']:
                    result.update(console=False,reason='Сеанс X11 не готов: нужны оконный менеджер EWMH и расширение XTest.')
                if os.geteuid()==0:
                    result.update(console=False,reason='Графическое тело должно работать от имени владельца, а не root.')
                try: result['input_focus']=handle(xdo('getwindowfocus'))
                except (RuntimeError,ValueError): result['input_focus']=None
                if os.environ.get('DBUS_SESSION_BUS_ADDRESS'):
                    try:
                        spi=Atspi(1000)
                        try: result['atspi']['bus']=bool(spi.own(spi.fn['get_desktop'](0)))
                        finally: spi.close()
                    except (OSError, RuntimeError): pass
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                result.update(console=False, reason=str(error))
        result['hints']=[result['reason']] if result['reason'] else []
        return result
    if reason: raise RuntimeError(reason)
    if os.geteuid() == 0: raise RuntimeError("Desktop executor must run as the owner, not root/session0")
    if capability == 'desktop.compose.validate':
        input_plan(args.get('input'))
        read_limits(args.get('read') or {}, resolve=False)
        return {'ok':True,'platform':'linux'}
    if capability == "desktop.window.list": return windows(args)
    if capability == "desktop.window.activate":
        hwnd = handle(args.get("hwnd")); pid = pid_of(hwnd)
        check_stop()
        if args.get("expected_pid") is not None and pid != integer(args["expected_pid"], "expected_pid", 1, 2**32-1): raise RuntimeError("window process changed")
        timeout=integer(args.get('timeout_ms',1500),'timeout_ms',1,15000)
        if type(args.get('restore',True)) is not bool: raise ValueError('restore must be boolean')
        end=time.monotonic()+timeout/1000
        while True:
            check_stop()
            if pid_of(hwnd)!=pid: raise RuntimeError('window process changed before activation')
            wm_state=property_of(hwnd,'WM_STATE')
            # Mapping a just-created GTK window can race its own initial map
            # and an old client's recycled XID. Restore only a confirmed
            # iconic client; let an unmapped new window finish creation.
            if args.get('restore',True) and 'window state: Iconic' in wm_state:
                xdo('windowmap',hwnd)
            if 'window state: Normal' in wm_state and 'Map State: IsViewable' in command(['xwininfo','-id',str(hwnd)]):
                break
            if time.monotonic()>=end: raise RuntimeError('window was not ready for activation before its deadline')
            time.sleep(.04)
        xdo("windowactivate", hwnd)
        while True:
            try: active=handle(xdo('getactivewindow'))
            except (RuntimeError,ValueError): active=None
            # EWMH can name the window before its frame is mapped. XSetInputFocus
            # would raise BadMatch then: wait for the X server's viewable state.
            if active==hwnd and 'Map State: IsViewable' in command(['xwininfo','-id',str(hwnd)]): break
            check_stop()
            if time.monotonic()>=end: raise RuntimeError('window activation was not confirmed before its deadline')
            if pid_of(hwnd)!=pid: raise RuntimeError('window process changed during activation')
            time.sleep(.04)
        return {"ok":True,"platform":"linux","method":"ewmh","foreground_hwnd":"0x%X" % hwnd}
    if capability == "desktop.input.perform": return perform(args)
    if capability == "desktop.screen.capture": return capture(args, state)
    if capability in ("desktop.window.read", "desktop.element.find", "desktop.element.act"): return accessible(args, capability)
    if capability == "desktop.clipboard.read":
        maximum=integer(args.get('limit_chars',1000000),'limit_chars',1,1000000)
        text=command(["xclip", "-selection", "clipboard", "-o"],strip=False)
        return {"ok": True, "platform": "linux", "text":text[:maximum], 'truncated':len(text)>maximum}
    if capability == "desktop.clipboard.write":
        text = args.get("text")
        if not isinstance(text, str) or len(text.encode("utf8")) > 1000000: raise ValueError("clipboard text exceeds its limit")
        check_stop()
        # Xclip remains a selection owner in the body's process group, until replaced.
        done = subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=text.encode("utf8"),
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if done.returncode: raise RuntimeError("clipboard ownership failed")
        return {"ok": True, "platform": "linux", "chars": len(text)}
    raise ValueError("unknown Linux desktop capability: " + capability)


@contextmanager
def input_lock():
    if fcntl is None: raise RuntimeError('Linux desktop locking is unavailable on this platform')
    root=Path(os.environ.get('HOME','/nonexistent'))/'.local/state/helene'
    root.mkdir(parents=True,exist_ok=True)
    with (root/'desktop-input.lock').open('a+b') as lock:
        deadline=time.monotonic()+5
        while True:
            try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB); break
            except BlockingIOError:
                if time.monotonic()>=deadline: raise RuntimeError('Another desktop action is still running; no input performed')
                time.sleep(.03)
        try: yield
        finally: fcntl.flock(lock,fcntl.LOCK_UN)


def dispatch(capability,args,state):
    global OPERATION_END
    if not isinstance(args,dict): raise ValueError('arguments must be an object')
    previous=OPERATION_END
    if capability in ('desktop.window.read','desktop.element.find','desktop.element.act'):
        _,limits=read_limits({k:v for k,v in args.items() if k in {'hwnd','shape','max_nodes','max_depth','max_children_per_node','max_text_chars','timeout_ms','backend','text_contains','visible_only'}},resolve=False)
        budget=limits['timeout_ms']/1000+2
    elif capability=='desktop.window.list': budget=integer(args.get('timeout_ms',20000),'timeout_ms',1,30000)/1000
    else: budget=45
    OPERATION_END=min(previous,time.monotonic()+budget) if previous is not None else time.monotonic()+budget
    try:
        if capability in ('desktop.input.perform','desktop.element.act','desktop.window.activate','desktop.clipboard.write'):
            # Refuse unsupported sessions before even creating a runtime lock.
            if session()[1]: raise RuntimeError(session()[1])
            with input_lock(): return _dispatch(capability,args,state)
        return _dispatch(capability,args,state)
    finally: OPERATION_END=previous


def main():
    def deadline(_signum, _frame): raise TimeoutError("Linux desktop operation exceeded its deadline")
    signal.signal(signal.SIGALRM, deadline); signal.alarm(50)
    def terminate(_signum,_frame):
        # Group TERM and parent-death TERM can both arrive. A second TERM must
        # not interrupt the first handler's finally while it releases input.
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        raise InterruptedError('Linux desktop operation was stopped; input may already have occurred')
    signal.signal(signal.SIGTERM,terminate)
    try:
        text = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
        if len(text) > 4 * 1024 * 1024: raise ValueError("desktop request is too large")
        result = dispatch(sys.argv[1], json.loads(text), sys.argv[2])
    except Exception as error:
        result = {"ok": False, "platform": "linux", "error": str(error)[:2000]}
    output = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    if len(output.encode("utf8")) > 16 * 1024 * 1024:
        output = '{"ok":false,"error":"Linux desktop response exceeds its byte limit"}'
    print(output)


if __name__ == "__main__": main()
