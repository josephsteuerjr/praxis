"""App-owned configuration, upstream server and upstream control consent."""
import importlib.util
import os
import sys
from pathlib import Path

state = Path(os.environ['HELENE_WINDOWS_MCP_STATE'])
state.mkdir(parents=True, exist_ok=True)
from windows_mcp import infrastructure
from windows_mcp.infrastructure import config, analytics
config.CONFIG_DIR = state
config.CONFIG_FILE = state / 'config.toml'
infrastructure.CONFIG_DIR = state
infrastructure.CONFIG_FILE = config.CONFIG_FILE
analytics.CONFIG_DIR = state
analytics.PostHogAnalytics.USER_DATA_DIR = state
# Upstream's consent prompt needs tkinter, which the embeddable runtime lacks; keep the
# prompt, show it as a native window (windows_mcp_consent.py, loaded by path).
_spec = importlib.util.spec_from_file_location(
    'helene_windows_mcp_consent', Path(__file__).with_name('windows_mcp_consent.py'))
consent = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(consent)
consent.install()
from windows_mcp.__main__ import main

if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        import fastmcp, win32api
        from windows_mcp.desktop import control_consent
        if control_consent.ask is not consent.ask:
            raise SystemExit('native consent prompt not installed')
        consent._box()  # the native prompt must exist before control can ever be granted
        print('ready')
    else:
        main()
