"""Windows CU read executor for the opt-in Hélène body, not a second controller.

One bounded JSON request arrives on private stdin; one JSON receipt leaves on stdout.
The body owns praxis.body.v1 admission, identity, journal and child lifetime. This
process has no device/controller credential and never selects a native fallback.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import json
import os
import sys

MAX_REQUEST = 65536
DEFAULT_PAGE = 2048
MAX_PAGE = 20000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
PER_MONITOR_V2 = ctypes.c_void_p(-4)


class FILETIME(ctypes.Structure):
    _fields_ = [("low", wt.DWORD), ("high", wt.DWORD)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", wt.LONG),
                ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]


def _api():
    if os.name != "nt":
        raise OSError("Windows CU read executor requires Windows")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)

    def bind(dll, name, restype, *args):
        fn = getattr(dll, name)
        fn.restype = restype
        fn.argtypes = list(args)
        return fn

    ptr = wt.HANDLE
    bind(user, "SetProcessDpiAwarenessContext", wt.BOOL, ptr)
    bind(user, "GetThreadDpiAwarenessContext", ptr)
    bind(user, "AreDpiAwarenessContextsEqual", wt.BOOL, ptr, ptr)
    bind(user, "GetForegroundWindow", ptr)
    bind(user, "GetCursorPos", wt.BOOL, ctypes.POINTER(wt.POINT))
    bind(user, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
    bind(user, "GetClipboardSequenceNumber", wt.DWORD)
    bind(user, "IsWindow", wt.BOOL, ptr)
    bind(user, "IsWindowVisible", wt.BOOL, ptr)
    bind(user, "IsIconic", wt.BOOL, ptr)
    bind(user, "GetWindowThreadProcessId", wt.DWORD, ptr, ctypes.POINTER(wt.DWORD))
    bind(user, "GetWindowRect", wt.BOOL, ptr, ctypes.POINTER(wt.RECT))
    bind(user, "GetWindowTextLengthW", ctypes.c_int, ptr)
    bind(user, "GetWindowTextW", ctypes.c_int, ptr, wt.LPWSTR, ctypes.c_int)
    bind(user, "GetClassNameW", ctypes.c_int, ptr, wt.LPWSTR, ctypes.c_int)
    bind(user, "EnumWindows", wt.BOOL, ctypes.c_void_p, wt.LPARAM)
    bind(kernel, "GetCurrentProcessId", wt.DWORD)
    bind(kernel, "ProcessIdToSessionId", wt.BOOL, wt.DWORD, ctypes.POINTER(wt.DWORD))
    bind(kernel, "OpenProcess", ptr, wt.DWORD, wt.BOOL, wt.DWORD)
    bind(kernel, "QueryFullProcessImageNameW", wt.BOOL, ptr, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD))
    bind(kernel, "GetProcessTimes", wt.BOOL, ptr, ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME),
         ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME))
    bind(kernel, "CloseHandle", wt.BOOL, ptr)
    bind(kernel, "CreateToolhelp32Snapshot", ptr, wt.DWORD, wt.DWORD)
    bind(kernel, "Process32FirstW", wt.BOOL, ptr, ctypes.POINTER(PROCESSENTRY32W))
    bind(kernel, "Process32NextW", wt.BOOL, ptr, ctypes.POINTER(PROCESSENTRY32W))
    return kernel, user


def _dpi(user):
    # The native body is per-monitor-v2 before any screen call. A fresh Python
    # process must make the SAME declaration; inheriting the parent is not enough.
    user.SetProcessDpiAwarenessContext(PER_MONITOR_V2)
    current = user.GetThreadDpiAwarenessContext()
    if not current or not user.AreDpiAwarenessContextsEqual(current, PER_MONITOR_V2):
        raise OSError("per-monitor-v2 DPI awareness unavailable; no coordinates returned")


def _session(kernel, pid):
    value = wt.DWORD()
    return value.value if kernel.ProcessIdToSessionId(pid, ctypes.byref(value)) else None


def _process(kernel, pid):
    session = _session(kernel, pid)
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return session, None, None
    try:
        size = wt.DWORD(32768)
        text = ctypes.create_unicode_buffer(size.value)
        path = text.value if kernel.QueryFullProcessImageNameW(handle, 0, text, ctypes.byref(size)) else None
        created, exited, sys_time, user_time = FILETIME(), FILETIME(), FILETIME(), FILETIME()
        stamp = ((created.high << 32) | created.low) if kernel.GetProcessTimes(
            handle, ctypes.byref(created), ctypes.byref(exited),
            ctypes.byref(sys_time), ctypes.byref(user_time)) else None
        return session, path, stamp
    finally:
        kernel.CloseHandle(handle)


def _hex(hwnd):
    return f"0x{int(hwnd or 0):X}"


def _window_row(kernel, user, hwnd, z_order):
    if not hwnd or not user.IsWindow(hwnd):
        return None
    pid = wt.DWORD()
    thread = user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    session, path, stamp = _process(kernel, pid.value)
    rect = wt.RECT()
    bounds = None
    if user.GetWindowRect(hwnd, ctypes.byref(rect)):
        bounds = {"left": rect.left, "top": rect.top, "right": rect.right,
                  "bottom": rect.bottom, "width": rect.right - rect.left,
                  "height": rect.bottom - rect.top}
    length = max(2, user.GetWindowTextLengthW(hwnd) + 1)
    title = ctypes.create_unicode_buffer(length)
    user.GetWindowTextW(hwnd, title, length)
    klass = ctypes.create_unicode_buffer(512)
    user.GetClassNameW(hwnd, klass, len(klass))
    return {"hwnd": _hex(hwnd), "fingerprint": f"{_hex(hwnd)}:{pid.value}:{stamp or 0}",
            "pid": pid.value, "thread_id": thread, "session_id": session,
            "process_path": path, "process_created_filetime": stamp,
            "title": title.value, "class": klass.value, "rect": bounds,
            "visible": bool(user.IsWindowVisible(hwnd)), "minimized": bool(user.IsIconic(hwnd)),
            "z_order": z_order}


def _page(rows, args):
    offset = args.get("offset", 0)
    limit = args.get("limit", DEFAULT_PAGE)
    if type(offset) is not int or offset < 0 or type(limit) is not int or limit < 0:
        raise ValueError("offset and limit must be nonnegative integers")
    limit = min(MAX_PAGE, max(1, limit))
    total = len(rows)
    items = rows[offset:offset + limit]
    return {"ok": True, "total": total, "offset": offset, "limit": limit,
            "returned": len(items), "next_offset": offset + len(items) if offset + len(items) < total else None,
            "items": items}


def dispatch(capability, args, *, kernel=None, user=None):
    if capability not in ("desktop.status", "desktop.window.list", "os.process.list"):
        raise ValueError("unsupported Python CU read capability")
    # Native status ignores Invoke args, including the protocol's default null;
    # only the two filtered list verbs parse their arguments.
    if capability != "desktop.status" and not isinstance(args, dict):
        raise ValueError("CU read arguments must be an object")
    if kernel is None or user is None:
        kernel, user = _api()
    _dpi(user)
    if capability == "desktop.status":
        fg = user.GetForegroundWindow()
        point = wt.POINT()
        cursor = {"x": point.x, "y": point.y} if user.GetCursorPos(ctypes.byref(point)) else None
        left, top, width, height = (user.GetSystemMetrics(n) for n in (76, 77, 78, 79))
        session = _session(kernel, kernel.GetCurrentProcessId())
        return {"ok": True, "interactive": session is not None and session != 0,
                "session_id": session, "foreground": _window_row(kernel, user, fg, 0),
                "cursor": cursor, "virtual_screen": {"left": left, "top": top,
                    "width": width, "height": height, "right": left + width, "bottom": top + height},
                "clipboard_sequence": user.GetClipboardSequenceNumber()}
    if capability == "desktop.window.list":
        fg = user.GetForegroundWindow()
        pid_filter = args.get("pid")
        if pid_filter is not None and (type(pid_filter) is not int or not 0 <= pid_filter <= 0xffffffff):
            raise ValueError("pid must be a Windows process id")
        visible_only = args.get("visible_only", True)
        if type(visible_only) is not bool or not isinstance(args.get("title_contains", ""), str):
            raise ValueError("invalid window filter")
        needle = args.get("title_contains", "").lower()
        rows = []
        callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def collect(hwnd, unused):
            if visible_only and not user.IsWindowVisible(hwnd):
                return True
            pid = wt.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid_filter is not None and pid.value != pid_filter:
                return True
            length = max(2, user.GetWindowTextLengthW(hwnd) + 1)
            title = ctypes.create_unicode_buffer(length)
            user.GetWindowTextW(hwnd, title, length)
            if needle and needle not in title.value.lower():
                return True
            row = _window_row(kernel, user, hwnd, len(rows))
            if row is not None:
                row.update(visible=bool(user.IsWindowVisible(hwnd)), foreground=hwnd == fg)
                rows.append(row)
            return True
        callback = callback_type(collect)
        if not user.EnumWindows(callback, 0):
            raise OSError(ctypes.get_last_error(), "EnumWindows failed")
        result = _page(rows, args)
        result["foreground_hwnd"] = _hex(fg)
        return result
    session_filter = args.get("session_id")
    if session_filter is not None and (type(session_filter) is not int or not 0 <= session_filter <= 0xffffffff):
        raise ValueError("session_id must be a Windows session id")
    if not isinstance(args.get("name_contains", ""), str):
        raise ValueError("invalid process filter")
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(), "process snapshot failed")
    rows = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snap, ctypes.byref(entry))
        needle = args.get("name_contains", "").lower()
        while more:
            session, path, stamp = _process(kernel, entry.th32ProcessID)
            name = entry.szExeFile
            if (not needle or needle in name.lower() or (path and needle in path.lower())) and (
                    session_filter is None or session == session_filter):
                rows.append({"pid": entry.th32ProcessID, "parent_pid": entry.th32ParentProcessID,
                             "threads": entry.cntThreads, "name": name, "path": path,
                             "session_id": session, "created_filetime": stamp})
            more = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    rows.sort(key=lambda row: row["pid"])
    return _page(rows, args)


def main():
    try:
        data = sys.stdin.buffer.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise ValueError("CU read request too large")
        request = json.loads(data)
        result = dispatch(request["capability"], request["args"])
        response = {"ok": True, "result": result}
    except (ValueError, KeyError, TypeError, OSError) as error:
        response = {"ok": False, "error": str(error)}
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main()
