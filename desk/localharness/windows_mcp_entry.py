"""App-owned configuration, upstream server and upstream control consent."""
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
from windows_mcp.__main__ import main

if __name__ == '__main__':
    if sys.argv[1:] == ['--check']:
        import fastmcp, win32api
        print('ready')
    else:
        main()
