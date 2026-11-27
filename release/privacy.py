"""Reject state files and credential material at the source archive boundary."""
from pathlib import PurePosixPath
import re

KEY = re.compile(rb"(?m)^-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----\r?\n[A-Za-z0-9/+]{40,}={0,2}\r?$")
TOKEN = re.compile(rb"\b(?:github_pat_[a-zA-Z0-9_]{65,}|gh[pousr]_[a-zA-Z0-9]{36,}|AKIA[0-9A-Z]{16})\b")
LOCAL = {".ssh", ".codex", ".claude", ".gnupg", ".aws", "__pycache__", "node_modules"}
SECRETS = {"id_rsa", "id_ed25519", "id_ecdsa", "credentials.json", "auth.json",
           ".env", ".deploy.env", "secret-strings.txt", "local-build.json"}


def check(name, raw):
    parts = PurePosixPath(name.lower()).parts
    if (parts[0] in {"data", "workspace", "memory", "agents", "_state"}
            or LOCAL.intersection(parts) or parts[-1] in SECRETS or parts[-1].endswith((".session", ".pfx", ".p12"))
            or parts[-1].startswith(".env.") and not parts[-1].endswith((".example", ".sample", ".template"))):
        raise ValueError("Private state or machine configuration in release tree: " + name)
    if KEY.search(raw) or TOKEN.search(raw):
        raise ValueError("Credential-shaped material in release tree: " + name)
