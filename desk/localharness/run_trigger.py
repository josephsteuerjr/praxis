"""Compatibility import for the runner's product-owned wake provenance."""
from pathlib import Path
import sys
app=str(Path(__file__).resolve().parents[1])
if app not in sys.path:sys.path.insert(0,app)
from deskd.run_trigger import record_wake, wake_source
