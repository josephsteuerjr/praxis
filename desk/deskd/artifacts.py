"""Immutable files offered in chat, scoped to the agent's data tree."""
import hashlib
import mimetypes
import os
import re
import shutil
import uuid
from pathlib import Path

ROOTS = ("media/artifacts", "workspace/media", "memory/.control/desk_inbox/attachments")

def display_name(path):
    name = Path(path).name
    while re.match(r"^msg-[0-9a-f]{10}-[0-9a-f]{12}-", name):
        name = re.sub(r"^msg-[0-9a-f]{10}-[0-9a-f]{12}-", "", name)
    name = re.sub(r"^\d{4}-[0-9a-f]{12}-(generated-image\.[a-z]+)$", r"\1", name)
    return name

def allowed(rel):
    return any(rel.startswith(top + "/") for top in ROOTS) or bool(
        re.fullmatch(r"memory/runs/\d{4}-\d{2}/run-[A-Za-z0-9-]+/files/.+", rel))

def resolve(tree, rel):
    said = str(rel or "").replace("\\", "/").strip()
    if not said or said.startswith("/") or ":" in said or ".." in said.split("/") or not allowed(said):
        return None, "вложение вне разрешённой папки"
    root = Path(tree).resolve()
    path = (root / said).resolve()
    try:
        actual = path.relative_to(root).as_posix()
    except ValueError:
        return None, "вложение вне дерева"
    if not allowed(actual) or not path.is_file():
        return None, "вложение не найдено в разрешённой папке"
    return path, ""

def stage(tree, source):
    """Snapshot a tool's selected file; never expose arbitrary disk paths by URL."""
    root, source = Path(tree).resolve(), Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source.name)
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    name = re.sub(r"[^\w.\- ]+", "_", display_name(source)).strip(". ")[:180] or "file.bin"
    target = root / "media/artifacts" / digest[:24] / name
    real = target.resolve()
    if not real.is_relative_to(root) or not real.relative_to(root).as_posix().startswith("media/artifacts/"):
        raise ValueError("папка артефактов выходит из дерева")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("rb") if target.is_file() else open(os.devnull, "rb") as file:
        intact = target.is_file() and hashlib.file_digest(file, "sha256").hexdigest() == digest
    if not intact:
        temporary = target.with_name(".part-" + uuid.uuid4().hex)
        try:
            shutil.copyfile(source, temporary)
            with temporary.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != digest:
                    raise ValueError("файл изменился во время отправки")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return target.relative_to(root).as_posix()

def metadata(tree, rel):
    path, _why = resolve(tree, rel)
    if path is None:
        return {}
    name = display_name(path)
    return {"media_name": name, "media_size": path.stat().st_size,
            "media_mime": mimetypes.guess_type(name)[0] or "application/octet-stream"}
