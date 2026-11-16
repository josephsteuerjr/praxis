"""Windows-MCP's control consent prompt as a native Windows message box.

Upstream asks the person at the desk through a Tk child process before each new AI control
period (`windows_mcp.desktop.control_consent.ask`). Hélène's component runs the python.org
embeddable runtime, which has no tkinter: the child exited 1, upstream read that as "error",
and EVERY first direct-control call (Move, Click, Type…) failed CONTROL_UNAVAILABLE while the
status still said "ready" (owner's agent, 16.11.2026). The prompt itself stays — only its
window changes. Same contract: allow, deny, timeout or error; a failure never grants control.

Loaded by path from windows_mcp_entry.py (the embeddable ._pth runtime does not put this
directory on sys.path) and importable by tests without the upstream package.
"""
import ctypes
import os

ANSWERS = {6: "allow", 7: "deny", 32000: "timeout"}  # IDYES, IDNO, MB_TIMEDOUT
# MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST.
# "No" is the default button: a key still held from typing denies instead of granting.
FLAGS = 0x4 | 0x30 | 0x100 | 0x10000 | 0x40000
TITLE = "Hélène · Windows-MCP"
TEXT = ("AI is about to take control of this computer.\n\nAllow now? "
        "If you keep using the computer without answering, you stay in control. "
        "This window closes by itself in {seconds} s.")


def _box():
    box = ctypes.WinDLL("user32", use_last_error=True).MessageBoxTimeoutW
    box.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                    ctypes.c_uint, ctypes.c_ushort, ctypes.c_ulong)
    box.restype = ctypes.c_int
    return box


def ask(seconds, *, box=None, environ=None):
    """Show the prompt for `seconds` and return allow, deny, timeout or error."""
    environ = os.environ if environ is None else environ
    try:
        seconds = max(1, min(600, int(seconds)))
        box = box or _box()
        text = (environ.get("HELENE_WINDOWS_MCP_CONSENT_TEXT") or TEXT).replace("{seconds}", str(seconds))
        title = environ.get("HELENE_WINDOWS_MCP_CONSENT_TITLE") or TITLE
        code = box(None, text, title, FLAGS, 0, seconds * 1000)
    except Exception:
        return "error"
    return ANSWERS.get(code, "error")


def install():
    """Replace upstream's Tk prompt; the upstream gate looks the function up per call."""
    from windows_mcp.desktop import control_consent
    control_consent.ask = ask
    return control_consent
