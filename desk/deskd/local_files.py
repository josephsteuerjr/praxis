"""Local owner's paper file dialog. No device/server access or SYSTEM file rights."""
import base64
from contextlib import contextmanager
import mimetypes
import os
from pathlib import Path
import uuid
import re

MAX_FILE = 64 * 1024 * 1024


@contextmanager
def owner_files():
    """A Windows service performs file operations as its interactive owner."""
    if os.name != "nt":
        yield Path.home()
        return
    import ctypes
    from ctypes import wintypes as w
    adv, kernel, wts, userenv = (ctypes.WinDLL(n, use_last_error=True) for n in
                                ("advapi32", "kernel32", "wtsapi32", "userenv"))
    adv.GetUserNameW.argtypes = [w.LPWSTR, ctypes.POINTER(w.DWORD)]
    adv.GetUserNameW.restype = w.BOOL
    kernel.WTSGetActiveConsoleSessionId.restype = w.DWORD
    username = ctypes.create_unicode_buffer(256)
    size = w.DWORD(256)
    if not adv.GetUserNameW(username, ctypes.byref(size)):
        raise OSError("Не удалось определить пользователя окна")
    if username.value.casefold() not in ("system", "система"):
        yield Path.home()
        return
    wts.WTSQueryUserToken.argtypes = [w.ULONG, ctypes.POINTER(w.HANDLE)]
    wts.WTSQueryUserToken.restype = w.BOOL
    adv.ImpersonateLoggedOnUser.argtypes = [w.HANDLE]
    adv.ImpersonateLoggedOnUser.restype = w.BOOL
    adv.RevertToSelf.argtypes = []
    adv.RevertToSelf.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    userenv.GetUserProfileDirectoryW.argtypes = [w.HANDLE, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    userenv.GetUserProfileDirectoryW.restype = w.BOOL
    token = w.HANDLE()
    session = kernel.WTSGetActiveConsoleSessionId()
    if session == 0xFFFFFFFF:
        raise OSError("Открой Hélène в своём сеансе, чтобы выбирать файлы")
    if not wts.WTSQueryUserToken(session, ctypes.byref(token)):
        raise OSError("Файловый диалог не получил пользователя активного сеанса")
    entered = False
    try:
        profile = ctypes.create_unicode_buffer(32768)
        count = w.DWORD(len(profile))
        if not userenv.GetUserProfileDirectoryW(token, profile, ctypes.byref(count)):
            raise OSError("Не удалось определить папку владельца")
        if not adv.ImpersonateLoggedOnUser(token):
            raise OSError("Файловый диалог не получил права владельца")
        entered = True
        yield Path(profile.value)
    finally:
        if entered and not adv.RevertToSelf():
            # A worker must never return to the pool under the owner's token.
            os._exit(1)
        kernel.CloseHandle(token)


def locations(home):
    result = []
    for label, path in [("Загрузки", home / "Downloads"), ("Документы", home / "Documents"),
                        ("Рабочий стол", home / "Desktop"), ("Домашняя папка", home)]:
        if path.is_dir():
            result.append({"name": label, "path": str(path)})
    roots = [Path(f"{chr(c)}:/") for c in range(65, 91)] if os.name == "nt" else [Path("/")]
    result.extend({"name": str(p), "path": str(p)} for p in roots if p.is_dir())
    return result


def listing(said=""):
    with owner_files() as home:
        places = locations(home)
        path = (home / said[2:] if said.startswith("~/") else Path(said)) if said else next((home / n for n in ("Downloads", "Documents") if (home / n).is_dir()), home)
        if not path.is_absolute() or not path.is_dir():
            raise ValueError("Выбери существующую папку")
        path = path.resolve()
        rows = []
        with os.scandir(path) as entries:
            for item in entries:
                try:
                    folder = item.is_dir()
                    if not folder and not item.is_file():
                        continue
                    stat = item.stat()
                    rows.append({"name": item.name, "path": item.path, "folder": folder,
                                 "size": 0 if folder else stat.st_size, "mtime": stat.st_mtime})
                except OSError:
                    continue
        rows.sort(key=lambda r: (not r["folder"], r["name"].casefold()))
        return {"path": str(path), "parent": str(path.parent) if path.parent != path else "",
                "locations": places, "entries": rows, "total": len(rows)}


def read_file(said):
    with owner_files():
        path = Path(said)
        if not path.is_absolute() or not path.is_file():
            raise ValueError("Выбери существующий файл")
        with path.open("rb") as file:
            data = file.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError("Файл больше 64 МБ")
        return {"name": path.name, "mime": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "size": len(data), "data": base64.b64encode(data).decode()}


def save_file(tree, rel, folder, name, overwrite=False, data=None):
    from deskd import artifacts
    if (not isinstance(name, str) or name in ("", ".", "..") or name.endswith((".", " "))
            or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in name)
            or re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name)):
        raise ValueError("Имя файла не должно содержать путь или служебные символы")
    admitted = None
    if data is not None:
        if not isinstance(data, str) or len(data) > ((MAX_FILE + 2) // 3) * 4:
            raise ValueError("Файл больше 64 МБ")
        try:
            admitted = base64.b64decode(data, validate=True)
        except ValueError:
            raise ValueError("Не удалось прочитать содержимое файла") from None
        if len(admitted) > MAX_FILE:
            raise ValueError("Файл больше 64 МБ")
    with owner_files():
        parent = Path(folder)
        if not parent.is_absolute() or not parent.is_dir():
            raise ValueError("Выбери существующую папку")
        source, why = artifacts.resolve(tree, rel)
        if source is None:
            raise ValueError(why)
        if source.stat().st_size > MAX_FILE:
            raise ValueError("Файл больше 64 МБ")
        target = parent.resolve() / name
        if (target.exists() or target.is_symlink()) and not overwrite:
            raise FileExistsError("Файл с этим именем уже существует")
        temporary = target.with_name(".helene-save-" + uuid.uuid4().hex)
        created = False
        try:
            # All IO, including source read, occurs under the owner's token.
            with temporary.open("xb") as sink:
                created = True
                if admitted is not None:
                    sink.write(admitted)
                else:
                    with source.open("rb") as incoming:
                        remaining = MAX_FILE
                        while chunk := incoming.read(min(1024 * 1024, remaining + 1)):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ValueError("Файл больше 64 МБ")
                            sink.write(chunk)
                sink.flush()
                os.fsync(sink.fileno())
            if overwrite:
                os.replace(temporary, target)
            else:
                # Windows rename fails if destination exists; POSIX link also does.
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                    temporary.unlink()
        finally:
            if created:
                temporary.unlink(missing_ok=True)
        return {"saved": True, "name": name, "path": str(target), "bytes": target.stat().st_size}
