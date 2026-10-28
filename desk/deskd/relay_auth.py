"""Owner-only relay credential queue; root supervisor alone owns auth.json.

Tokens never enter a receipt, log, agent frame or HTTP response. The queue
is below .channel (0700, desk), protected by the root-owned sticky data root.
"""
import json
import os
import time
import uuid
from pathlib import Path

MAX_BYTES = 128 * 1024
PUBLIC = "relay/auth-status.json"


def validate(auth):
    if not isinstance(auth, dict) or auth.get("OPENAI_API_KEY"):
        raise ValueError("Нужен вход ChatGPT по подписке, а не ключ API")
    tokens = auth.get("tokens")
    if not isinstance(tokens, dict) or not all(
            isinstance(tokens.get(k), str) and tokens[k].strip()
            for k in ("access_token", "refresh_token", "id_token")):
        raise ValueError("Файл входа ChatGPT неполон")
    if len(json.dumps(auth).encode()) > MAX_BYTES:
        raise ValueError("Файл входа слишком велик")
    return auth


def private_write(path, value, uid=None, gid=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".part-" + uuid.uuid4().hex)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf8") as file:
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        if uid is not None:
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def queue_dir(tree):
    path = Path(tree) / ".channel/relay-auth-imports"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("Небезопасная папка приёма входа")
    return path


def prepare(tree, uid=None, gid=None):
    folder = queue_dir(tree)
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    if uid is not None:
        os.chown(folder, uid, gid)


def enqueue(tree, auth, replace=False):
    validate(auth)
    folder = queue_dir(tree)
    if not folder.is_dir():
        raise ValueError("Серверный надзор не подготовил приём входа; обнови сервер")
    if any(folder.glob("*.pending")):
        raise ValueError("Предыдущий вход ещё ожидает применения")
    ident = uuid.uuid4().hex
    private_write(folder / (ident + ".pending"), {
        "id": ident, "created": time.time(), "replace": replace is True, "auth": auth})
    return {"id": ident, "phase": "queued", "note": "Вход ждёт свободного реле на сервере"}


def receipt(tree, ident):
    if not isinstance(ident, str) or len(ident) != 32 or any(c not in "0123456789abcdef" for c in ident):
        raise ValueError("Неверный номер запроса")
    folder = queue_dir(tree)
    path = folder / (ident + ".receipt")
    if path.is_file():
        return json.loads(path.read_text(encoding="utf8"))
    if (folder / (ident + ".pending")).is_file():
        return {"id": ident, "phase": "queued", "note": "Ждём окончания текущего хода"}
    raise ValueError("Запрос не найден")


def persistence(tree):
    """Mount evidence, not a claim that an operator will keep a Docker volume."""
    try:
        root = str(Path(tree).resolve())
        matches = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            mount = left.split()[4].replace("\\040", " ")
            if root == mount or root.startswith(mount.rstrip("/") + "/"):
                matches.append((len(mount), right.split()[0]))
        fs = max(matches)[1]
        return "container-layer" if fs == "overlay" else "mounted:" + fs
    except (OSError, ValueError, IndexError):
        return "unknown"


def auth_paths(tree):
    home = Path(tree) / "relay/local_auth"
    primary = home / "accounts/primary/auth.json"
    # Relay0.8.4 prefers primary over the legacy file. Login and serve must agree.
    return [home / "auth.json"] + ([primary] if primary.exists() else [])


def publish(tree, value):
    public = Path(tree) / PUBLIC
    private_write(public, value)
    os.chmod(public, 0o644)


def apply_pending(tree, *, idle, activate, uid=None, gid=None, running=None):
    folder = queue_dir(tree)
    paths = auth_paths(tree)
    public = {"login_present": any(p.is_file() for p in paths),
              "auth_path": "relay/local_auth/auth.json", "persistence": persistence(tree)}
    for packet in sorted(folder.glob("*.pending")):
        ident = packet.stem
        result = {"id": ident, "phase": "failed", "note": "Файл входа не применён"}
        try:
            if packet.is_symlink() or packet.stat().st_size > MAX_BYTES + 1024:
                raise ValueError("Некорректная записка входа")
            data = json.loads(packet.read_text(encoding="utf8"))
            auth = validate(data.get("auth"))
            if not isinstance(data.get("created"), (int, float)):
                raise ValueError("Некорректная дата запроса входа")
            if data.get("id") != ident or time.time() - data["created"] > 600:
                raise ValueError("Запрос входа устарел; повтори отправку")
            if not idle:
                public.update(phase="queued", note="Вход ждёт окончания текущего хода")
                continue
            existing = next((p for p in reversed(paths) if p.is_file()), None)
            if existing is not None and not data.get("replace"):
                # Comparing complete auth also covers old files without account_id.
                old = json.loads(existing.read_text(encoding="utf8"))
                old_id = (old.get("tokens") or {}).get("account_id")
                new_id = auth["tokens"].get("account_id")
                if not (old_id and old_id == new_id) and old != auth:
                    result.update(phase="conflict", note="На сервере уже есть другой вход. Подтверди замену в приложении")
                else:
                    result["phase"] = "ready"
            else:
                result["phase"] = "ready"
            if result["phase"] == "ready":
                # activate owns stop/write/spawn, preventing refresh from racing a write.
                def save():
                    for path in paths:
                        if path.is_symlink() or path.parent.is_symlink():
                            raise ValueError("Небезопасный путь входа")
                        private_write(path, auth)
                    private_write(Path(tree) / "relay/local_auth/login-generation",
                                  time.time_ns())
                applied_running = activate(save)
                result.update(phase="active" if applied_running else "saved",
                              note="Вход сохранён; реле поднято" if applied_running else "Вход сохранён; реле ещё не поднялось")
                public["login_present"] = True
        except ValueError as exc:
            # Our validation errors never include token data or JSON fragments.
            result["note"] = str(exc) if not isinstance(exc, json.JSONDecodeError) else "Записка входа повреждена"
        except (OSError, TypeError, KeyError):
            result["note"] = "Не удалось сохранить вход: проверь права и серверный надзор"
        private_write(folder / (ident + ".receipt"), result, uid, gid)
        packet.unlink(missing_ok=True)
        public.update(phase=result["phase"], note=result["note"])
    # Keep the last application result, while refreshing mount/presence facts.
    try:
        previous = json.loads((Path(tree) / PUBLIC).read_text(encoding="utf8"))
        public = {**previous, **public}
    except (OSError, ValueError):
        pass
    if running is not None:
        public["relay_running"] = bool(running())
    publish(tree, public)
    # Receipts contain no credentials; bound their retention too.
    for path in folder.glob("*.receipt"):
        if time.time() - path.stat().st_mtime > 86400:
            path.unlink(missing_ok=True)
