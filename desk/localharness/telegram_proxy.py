"""Compatibility import for the runner; connection helpers are shared with the channel."""
from pathlib import Path
import sys
# Direct script entrypoints start with localharness/, before boot adds app/.
app=str(Path(__file__).resolve().parents[1])
if app not in sys.path:sys.path.insert(0,app)
from deskd.telegram_proxy import settings, bot_request, file_request, public_address, connection_type
