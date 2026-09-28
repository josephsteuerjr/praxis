#!/usr/bin/env python3
"""Окно лаборатории на WebKitGTK — тот же движок, что у Tauri на Linux (webkit2gtk-4.1).

Запуск в WSLg или на живом Debian/Ubuntu/Astra:
    python3 webkit.py [--auto] /путь/к/dist/index.html
--auto: прогнать стенд, напечатать BENCH и выйти.
"""
import json
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

auto = "--auto" in sys.argv
page = [a for a in sys.argv[1:] if not a.startswith("--")][0]
version = f"{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}.{WebKit2.get_micro_version()}"

win = Gtk.Window(title="Hélène · лаборатория (WebKitGTK)")
win.set_default_size(1440, 900)
settings = WebKit2.Settings()
settings.set_enable_developer_extras(True)
settings.set_allow_file_access_from_file_urls(True)
settings.set_enable_smooth_scrolling(True)
settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.ALWAYS)
view = WebKit2.WebView.new_with_settings(settings)

manager = view.get_user_content_manager()
manager.register_script_message_handler("bench")


def on_bench(_mgr, result):
    value = result.get_js_value() if hasattr(result, "get_js_value") else result
    text = value.to_string()
    print("BENCH " + text, flush=True)
    if auto:
        GLib.timeout_add(300, Gtk.main_quit)


manager.connect("script-message-received::bench", on_bench)
# Мост для стенда: страница зовёт window.__labBench(json).
manager.add_script(WebKit2.UserScript.new(
    "window.__labBench = (j) => window.webkit.messageHandlers.bench.postMessage(j);",
    WebKit2.UserContentInjectedFrames.TOP_FRAME,
    WebKit2.UserScriptInjectionTime.START,
    None, None,
))

win.add(view)
win.connect("destroy", Gtk.main_quit)
query = f"?host=webkitgtk:{version}" + ("&auto=1" if auto else "")
view.load_uri("file://" + page + query)
win.show_all()
print(json.dumps({"webkitgtk": version}), flush=True)
Gtk.main()
