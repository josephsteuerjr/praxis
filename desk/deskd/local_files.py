"""Local owner's paper file dialog. No device/server access or SYSTEM file rights."""
import base64
import binascii
from contextlib import contextmanager
import mimetypes
import os
from pathlib import Path
import uuid
import re

MAX_FILE = 64 * 1024 * 1024


class FileOutcomeUnknown(OSError):
    """The commit may have happened; never treat this as a safe retry."""


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


def _known_folder(guid):
    """Ask Windows for the owner's redirected location, not ~/Documents.

    This runs inside owner_files() for the HTTP service and directly under the
    interactive user's token for the native-window CLI.
    """
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [("data1", w.DWORD), ("data2", w.WORD), ("data3", w.WORD),
                    ("data4", ctypes.c_ubyte * 8)]

    value = uuid.UUID(guid)
    raw = GUID.from_buffer_copy(value.bytes_le)
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), w.DWORD, w.HANDLE,
                                          ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = w.LONG
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    result = ctypes.c_void_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(result)) != 0:
        return None
    try:
        return Path(ctypes.wstring_at(result))
    finally:
        ole.CoTaskMemFree(result)


def locations(home):
    result = []
    folders = [("Загрузки", "374DE290-123F-4565-9164-39C4925E467B", "Downloads"),
               ("Документы", "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents"),
               ("Рабочий стол", "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop")]
    for label, guid, fallback in folders:
        # Under a SYSTEM channel owner_files() impersonates an interactive
        # user, but SHGetKnownFolderPath(NULL) may use the process token on
        # older hosts. Never substitute a SYSTEM known folder for that user's
        # supplied profile; the native/ordinary user path can use the API.
        path = (_known_folder(guid) if home == Path.home() else None) or home / fallback
        if path.is_dir():
            result.append({"name": label, "path": str(path)})
    if home.is_dir():
        result.append({"name": "Домашняя папка", "path": str(home)})
    roots = [Path(f"{chr(c)}:/") for c in range(65, 91)] if os.name == "nt" else [Path("/")]
    result.extend({"name": str(p), "path": str(p)} for p in roots if p.is_dir())
    return result


def listing(said="", *, native=False):
    with owner_files() as home:
        return _listing(said, home, native=native)


def _listing(said, home, *, native=False):
    places = locations(home)
    if said:
        path = home / said[2:] if said.startswith("~/") else Path(said)
    elif native:
        # The former native dialog selected its first available location,
        # including Desktop when Downloads and Documents were absent.
        path = Path(places[0]["path"]) if places else home
    else:
        # The HTTP dialog historically preferred home over Desktop.
        path = next((Path(place["path"]) for place in places
                     if place["name"] in ("Загрузки", "Документы")), home)
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


def read_file(said, *, native=False):
    with owner_files():
        return _read_file(said, native=native)


def _read_file(said, *, native=False):
    path = Path(said)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Выбери существующий файл")
    with path.open("rb") as file:
        data = file.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("Файл больше 64 МБ")
    # The native window used a fixed set before the Python handoff; keep its
    # stable preview types even when Windows' MIME registry differs by host.
    known = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "webp": "image/webp", "gif": "image/gif", "mp3": "audio/mpeg",
             "wav": "audio/wav", "ogg": "audio/ogg", "webm": "audio/webm",
             "m4a": "audio/mp4", "txt": "text/plain", "md": "text/plain"}
    # Existing HTTP reads used the OS MIME registry, while the native window
    # used this fixed table and octet-stream for everything else. The bytes,
    # owner and size guards are shared; presentation keeps each old contract.
    mime = (known.get(path.suffix.removeprefix(".").lower()) if native else
            mimetypes.guess_type(path.name)[0])
    return {"name": path.name, "mime": mime or "application/octet-stream",
            "size": len(data), "data": base64.b64encode(data).decode()}


def save_file(tree, rel, folder, name, overwrite=False, data=None):
    from deskd import artifacts
    with owner_files():
        source, why = artifacts.resolve(tree, rel)
        if source is None:
            raise ValueError(why)
        return _save(folder, name, overwrite, data=data, source=source)


def save_preview(folder, name, data, overwrite=False):
    """Native owner-window entry: exact bytes, never an agent artifact path."""
    with owner_files():
        return _save(folder, name, overwrite, data=data)


def _save(folder, name, overwrite=False, *, data=None, source=None):
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
        except (ValueError, binascii.Error):
            raise ValueError("Не удалось прочитать содержимое файла") from None
        if len(admitted) > MAX_FILE:
            raise ValueError("Файл больше 64 МБ")
    parent = Path(folder)
    if not parent.is_absolute() or not parent.is_dir():
        raise ValueError("Выбери существующую папку")
    if admitted is None and source is None:
        raise ValueError("Не удалось прочитать содержимое файла")
    if source is not None and source.stat().st_size > MAX_FILE:
        raise ValueError("Файл больше 64 МБ")
    target = parent.resolve() / name
    if (target.exists() or target.is_symlink()) and not overwrite:
        raise FileExistsError("Файл с этим именем уже существует")
    temporary = target.with_name(".helene-save-" + uuid.uuid4().hex)
    created = False
    count = 0
    try:
        # All IO, including source read, occurs under the caller's owner token.
        with temporary.open("xb") as sink:
            created = True
            if admitted is not None:
                sink.write(admitted)
                count = len(admitted)
            else:
                with source.open("rb") as incoming:
                    remaining = MAX_FILE
                    while chunk := incoming.read(min(1024 * 1024, remaining + 1)):
                        remaining -= len(chunk)
                        if remaining < 0:
                            raise ValueError("Файл больше 64 МБ")
                        sink.write(chunk)
                        count += len(chunk)
            sink.flush()
            os.fsync(sink.fileno())
        try:
            if overwrite:
                os.replace(temporary, target)
            else:
                # Windows rename fails if destination exists; POSIX link also does.
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
                    temporary.unlink()
        except OSError as exc:
            if isinstance(exc, FileExistsError) and not overwrite and target.exists():
                raise
            raise FileOutcomeUnknown("Исход сохранения неизвестен; прочитай файл перед повтором") from exc
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise FileOutcomeUnknown("Очистка временного файла не подтверждена; проверь папку и файл перед повтором") from exc
    # Do not stat after the commit: a post-commit read failure is not a failed
    # save, and the count was measured from bytes written to the staged file.
    return {"saved": True, "name": name, "path": str(target), "bytes": count}
