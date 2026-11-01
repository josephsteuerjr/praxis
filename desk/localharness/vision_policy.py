"""Explicit, recoverable deploy migration of Hélène's image-helper policy.

Called under the tree lock, before importing the agent. The model's absent setting is
legacy-off; only this deployment transaction may elect a new default in llm.json.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import uuid


class MigrationError(Exception):
    """Do not proceed to a model call with an ambiguous policy migration."""


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationError(f"не читается конфигурация мозга {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise MigrationError(f"конфигурация мозга {path} не объект JSON")
    return value


def _protect_private_temp(name: str) -> None:
    """Protect an EMPTY temp file before sensitive llm.json bytes enter it.

    chmod on Windows changes read-only, not the inherited ACL. A best-effort
    post-replace fix leaves both a disclosure window and a possibly public
    replacement; fail closed before writing instead. The same-volume replace
    carries this already-restricted file's descriptor into the target.
    """
    if os.name != "nt":
        os.chmod(name, 0o600)
        if stat.S_IMODE(os.stat(name).st_mode) & 0o077:
            raise MigrationError("временный конфиг сохранил чужие права")
        return
    user = os.environ.get("USERNAME") or ""
    if not user:
        raise MigrationError("USERNAME отсутствует — нельзя сузить права конфига")
    try:
        result = subprocess.run(
            ["icacls", name, "/inheritance:r", "/grant:r", f"{user}:F",
             "/grant:r", "*S-1-5-18:F", "/grant:r", "*S-1-5-32-544:F", "/Q"],
            capture_output=True, text=True, encoding="cp866", errors="replace",
            creationflags=0x08000000, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MigrationError(f"права временного конфига не подтверждены: {type(exc).__name__}") from exc
    if result.returncode:
        raise MigrationError("icacls не подтвердил защиту временного конфига")


def _write(path: Path, data: bytes, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        # An empty name can inherit liberal directory ACLs for milliseconds;
        # no credential bytes are present there. Close the handle so icacls on
        # Windows can change its ACL, then reopen only after success.
        os.close(fd)
        if private:
            _protect_private_temp(name)
        with open(name, "wb") as dst:
            dst.write(data)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def _document(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _marker(tree: Path) -> Path:
    return tree / "memory" / ".state" / "vision-policy-change.json"


def _notice(tree: Path) -> Path:
    return tree / "memory" / ".state" / "vision-policy-notice.json"


def preflight(tree: Path) -> None:
    """Prevent boot.project_brain from replacing a corrupt existing profile."""
    target = Path(tree) / "memory" / "llm.json"
    if target.exists():
        # boot.project_brain reads this exact file as strict UTF-8. Its older
        # reader treats a BOM decode/JSON refusal as an empty merge source and
        # may overwrite authored settings. Do not accept a wider encoding here.
        try:
            if target.read_bytes().startswith(b"\xef\xbb\xbf"):
                raise MigrationError("llm.json с BOM не читается прежним проектором — профиль не меняю")
        except OSError as exc:
            raise MigrationError(f"не читается профиль llm.json: {exc}") from exc
        data = _json(target)
        roles = data.get("roles")
        # An old partial profile may omit voice: project_brain will fill it
        # before migration. Reject malformed present fields, not valid absence.
        if roles is None and "roles" not in data:
            return
        if not isinstance(roles, dict):
            raise MigrationError("roles повреждён — проекцию конфигурации блокирую")
        if "voice" not in roles:
            return
        if not isinstance(roles["voice"], dict):
            raise MigrationError("roles.voice повреждён — проекцию конфигурации блокирую")
        value = roles["voice"].get("vision_prepass")
        if "vision_prepass" in roles["voice"] and type(value) is not bool:
            raise MigrationError("roles.voice.vision_prepass не bool — профиль не меняю")


def _value(*, code_dir: Path | None = None, read_dotenv: bool = False) -> bool:
    """Choose the policy from the SAME effective lever the agent import will read.

    In the opt-in legacy dotenv mode, agent.py imports `.env` with override=True
    after this migration. Parse its value now with python-dotenv itself, without
    mutating the process environment or exposing any other `.env` entries.
    """
    lever = os.environ.get("PRAXIS_VISION_PREPASS", "").strip().lower()
    if read_dotenv:
        if code_dir is None:
            raise MigrationError("read_dotenv включён без адреса исходного дерева")
        import boot
        dotenv_path = boot.chosen_dotenv(Path(code_dir))
        if dotenv_path is not None:
            try:
                from dotenv import dotenv_values
                values = dotenv_values(dotenv_path)
            except (ImportError, OSError, UnicodeError, ValueError) as exc:
                raise MigrationError("не удалось прочесть выбранный .env до миграции зрения") from exc
            # A key WITHOUT '=' is ignored by load_dotenv too; an explicitly
            # empty value overrides process env to '' and leaves config in charge.
            if values.get("PRAXIS_VISION_PREPASS") is not None:
                lever = str(values["PRAXIS_VISION_PREPASS"]).strip().lower()
    if lever in {"0", "false", "no", "off"}:
        return False
    if lever in {"", "1", "true", "yes", "on"}:
        return True
    raise MigrationError("PRAXIS_VISION_PREPASS не распознан — выбор зрения не меняю")


def _valid_id(value) -> bool:
    return (isinstance(value, str) and len(value) == 32
            and all(char in "0123456789abcdef" for char in value))


def _read_notice(path: Path) -> dict:
    pending = _json(path)
    if (not _valid_id(pending.get("id"))
        or not isinstance(pending.get("text"), str) or not pending["text"]
        or type(pending.get("done")) is not bool):
        raise MigrationError("расписка зрения повреждена — не притворяюсь, что показал изменение")
    return pending


def _finish(tree: Path, event: dict) -> None:
    if not _valid_id(event.get("id")):
        raise MigrationError("идентификатор миграции зрения повреждён")
    notice = _notice(tree)
    if notice.exists():
        existing = _read_notice(notice)
        if existing.get("id") != event["id"]:
            archived = notice.with_name("vision-policy-notice-" + str(existing["id"]) + ".json")
            # Startup has not reached notice() yet. Queue an undelivered receipt
            # instead of preventing the very startup that could deliver it.
            # The harness tree lock serializes this rename; preserve collisions.
            while archived.exists():
                archived = notice.with_name("vision-policy-notice-" + existing["id"]
                                            + "-queued-" + uuid.uuid4().hex + ".json")
            notice.rename(archived)
    if not notice.exists():
        enabled = event["after"]
        text = ("Зрение Hélène: теперь снимки сначала описывает отдельная зрячая модель, "
                "а основной мозг получает её текст. " if enabled else
                "Зрение Hélène: сохранён явный запрет вспомогательной зрячей модели "
                "из PRAXIS_VISION_PREPASS=0. ")
        text += ("Это изменение версии, не ваше сообщение. Чтобы отменить, задайте "
                 "roles.voice.vision_prepass=false в memory/llm.json "
                 "(или PRAXIS_VISION_PREPASS=0 в окружении) и перезапустите Hélène. "
                 "Переменная окружения имеет приоритет: если там 1, сначала снимите её "
                 "или замените на 0. Поставленное вручную значение не переписывается "
                 "при следующем запуске.")
        _write(notice, _document({"id": event["id"], "done": False, "text": text}))
    journal = tree / "memory" / ".state" / "vision-policy.events.jsonl"
    try:
        rows = journal.read_text(encoding="utf-8").splitlines() if journal.exists() else []
        if not any(isinstance(row := json.loads(line), dict) and row.get("id") == event["id"] for line in rows):
            # Atomic rewrite avoids half an event under power loss; bounded by deploy
            # migration count, and never includes llm.json's API keys or endpoint.
            _write(journal, ("\n".join(rows + [json.dumps(event, ensure_ascii=False)]) + "\n").encode("utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise MigrationError(f"журнал политики зрения не читается: {exc}") from exc
    _marker(tree).unlink(missing_ok=True)


def migrate(tree: Path, *, code_dir: Path | None = None, read_dotenv: bool = False) -> bool:
    """After claim_tree and project_brain, persist one explicit choice, or refuse.

    A transaction is written *before* llm.json: on restart its after-hash proves
    whether our atomic replacement landed. No pending transaction is discarded when
    the observed bytes disagree; a human decision is never overwritten on replay.
    Returns True only when this invocation physically changes the profile.
    """
    tree = Path(tree)
    target = tree / "memory" / "llm.json"
    config = _json(target)  # missing/unreadable/invalid is not permission to initialize
    original = target.read_bytes()
    roles = config.get("roles")
    voice = roles.get("voice") if isinstance(roles, dict) else None
    if not isinstance(voice, dict):
        raise MigrationError("roles.voice отсутствует или повреждён — профиль не меняю")
    transaction = _marker(tree)
    if transaction.exists():
        event = _json(transaction)
        if (event.get("kind") != "vision_prepass_migration"
            or not isinstance(event.get("after_sha256"), str)
            or not isinstance(event.get("before_sha256"), str)
            or not _valid_id(event.get("id"))
            or type(event.get("after")) is not bool):
            raise MigrationError("незавершённая миграция зрения повреждена — ручная сверка")
        observed = hashlib.sha256(original).hexdigest()
        if observed == event["before_sha256"] and "vision_prepass" not in voice:
            voice["vision_prepass"] = event["after"]
            if hashlib.sha256(_document(config)).hexdigest() != event["after_sha256"]:
                raise MigrationError("повтор миграции не совпал с приготовленным профилем")
            _write(target, _document(config), private=True)
            import boot
            boot._own_only(target)
        elif observed != event["after_sha256"]:
            raise MigrationError("профиль изменился после незавершённой миграции зрения — ручная сверка")
        _finish(tree, event)
        return observed == event["before_sha256"]
    if "vision_prepass" in voice:
        if type(voice["vision_prepass"]) is not bool:
            raise MigrationError("roles.voice.vision_prepass не bool — явный выбор не переписываю")
        return False
    enabled = _value(code_dir=code_dir, read_dotenv=read_dotenv)
    voice["vision_prepass"] = enabled
    content = _document(config)
    source_path = Path(__file__)
    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    event = {"id": uuid.uuid4().hex, "kind": "vision_prepass_migration",
             "author": f"deploy:{source_sha}", "source": str(source_path.name),
             "reason": "1.7.0: helper-first image path, with explicit owner off override",
             "at": dt.datetime.now(dt.timezone.utc).isoformat(),
             "before": "absent", "after": enabled,
             "before_sha256": hashlib.sha256(original).hexdigest(),
             "after_sha256": hashlib.sha256(content).hexdigest()}
    try:
        _write(transaction, _document(event))
        _write(target, content, private=True)
        # Windows ACLs on a replaced private config are not inherited from its old
        # inode: reapply the existing boot security policy before returning.
        import boot
        boot._own_only(target)
        _finish(tree, event)
    except (OSError, UnicodeError, ValueError) as exc:
        raise MigrationError(f"миграция зрения не завершена (повтор после восстановления): {exc}") from exc
    return True


def _deliver_notice(tree: Path, path: Path, pending: dict, desk, now) -> None:
    source_id = "vision-policy-" + str(pending["id"])
    # Read the complete stream: a crash after archive but before the marker may
    # be followed by more than 2000 messages before restart.
    archive = tree / "memory" / "groups" / (desk.stream + ".jsonl")
    already = False
    if archive.exists():
        with archive.open("r", encoding="utf-8") as rows:
            for line in rows:
                try:
                    if json.loads(line).get("source_id") == source_id:
                        already = True
                        break
                except (ValueError, AttributeError):
                    continue
    if not already:
        desk.archive(pending["text"], outgoing=False, now=now,
                     sender="Hélène", source_id=source_id)
    desk.life(pending["text"], direction="in", actor="Hélène",
              source_id=source_id, now=now)
    pending["done"] = True
    pending["at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    _write(path, _document(pending))


def notice(tree: Path, desk, now) -> None:
    """Deliver queued and current receipts before the first owner turn.

    A crash between queueing an older receipt and completing a newer transaction
    leaves both recoverable. Duplicate queue copies share one delivery identity.
    """
    tree = Path(tree)
    current = _notice(tree)
    paths = sorted(current.parent.glob("vision-policy-notice-*.json"))
    if current.exists():
        paths.append(current)
    records = [(path, _read_notice(path)) for path in paths]
    texts = {}
    for _, pending in records:
        previous = texts.setdefault(pending["id"], pending["text"])
        if previous != pending["text"]:
            raise MigrationError("один идентификатор уведомления зрения содержит разный текст")
    delivered = {pending["id"] for _, pending in records if pending["done"]}
    for path, pending in records:
        if pending["done"]:
            continue
        if pending["id"] not in delivered:
            _deliver_notice(tree, path, pending, desk, now)
            delivered.add(pending["id"])
        else:
            # A previous copy has an actual completion receipt, including life().
            pending["done"] = True
            pending["at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            _write(path, _document(pending))
