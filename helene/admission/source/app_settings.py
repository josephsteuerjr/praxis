"""Agent-facing Hélène configuration through the same server configuration owner.

Runs only as an owner tool. No HTTP impersonation, second config writer, or
hardcoded server path: HELENE_CONFIG is stamped by the current runner owner.
"""
from __future__ import annotations
import os
from pathlib import Path


def configure(action="read", patch=None, seen=""):
    from deskd import agentcfg, readers
    selected = os.environ.get("HELENE_CONFIG", "")
    if not selected or not Path(selected).is_file():
        return {"ok":False,"error":"This runner has no server-editable Hélène configuration"}
    actual = readers.config_path()
    if actual is None or actual.resolve() != Path(selected).resolve():
        return {"ok":False,"error":"Server and runner configuration paths differ"}
    if action == "read":
        return agentcfg.state()
    if action != "save":
        return {"ok":False,"error":"action: read | save"}
    if not seen:
        return {"ok":False,"error":"Read first and supply its mtime_ns as seen; save does not silently overwrite concurrent changes"}
    return agentcfg.save(patch, seen)
