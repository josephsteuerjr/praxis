"""Select the supported X11 view and a safe VMware rendering default."""
from pathlib import Path
import os


def configure(environ=None, *, virtual=None):
    env=os.environ if environ is None else environ
    env.setdefault('QT_NO_GLIB','1')
    if env.get('DISPLAY'):env.setdefault('QT_QPA_PLATFORM','xcb')
    if virtual is None:
        try:virtual='vmware' in Path('/sys/class/dmi/id/sys_vendor').read_text().lower()
        except OSError:virtual=False
    if virtual:
        # VMware's Wayland/EGL path may abort instead of falling back. Keep
        # Chromium's sandbox enabled; rendering does not require GPU access.
        env.setdefault('QT_QUICK_BACKEND','software')
        env.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
