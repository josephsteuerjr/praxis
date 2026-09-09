# -*- coding: utf-8 -*-
"""Вход агента в Telegram своим аккаунтом (MTProto), двумя шагами, без
интерактивного ввода — его зовёт окно настроек через оболочку.

  python mtproto_login.py --session <путь без расширения> status
  python mtproto_login.py … send
  python mtproto_login.py … code
  python mtproto_login.py … logout

Секреты приезжают ОКРУЖЕНИЕМ, а не аргументами: `HELENE_TG_API_ID`,
`HELENE_TG_API_HASH`, `HELENE_TG_PHONE`, `HELENE_TG_CODE`, `HELENE_TG_PASSWORD`
(на Windows командную строку чужого процесса читает любой процесс того же
пользователя — см. `_from_env`). Те же значения принимаются и аргументами
(`--api-id`, `--api-hash`, `--phone`, `--code`, `--password`) — как запасной
путь для ручного разбора.

Ответ — одна строка JSON в stdout: {"ok": bool, "state": "authorized" |
"code_sent" | "unauthorized", "username": …, "id": …, "error": …}.
Хэш кода между шагами лежит рядом с сессией в файле .codehash.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _out(**payload) -> None:
    # Оболочка читает эту строку как UTF-8 (String::from_utf8_lossy). Пока
    # сообщения были английскими, кодировка ничего не решала; теперь они русские,
    # и полагаться на PYTHONUTF8 из среды нельзя — говорим прямо.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ⚠ Строка ошибки отсюда печатается в окне настроек КАК ЕСТЬ (settings.ts:
# `accOut.textContent = r.error`). Раньше туда уезжали английские имена классов
# Python: «ValueError: invalid literal for int() with base 10: ''» — это владелец
# видел, забыв заполнить api_id. Английский трейс в русском окне — не ошибка
# пользователя, а наша: он не знает ни Python, ни Telethon.
_HUMAN = {
    "ApiIdInvalidError": "api_id и api_hash не подошли — проверь их на my.telegram.org",
    "ApiIdPublishedFloodError": "эти api_id/api_hash заблокированы Telegram — заведи свои "
                                "на my.telegram.org",
    "PhoneNumberInvalidError": "номер не подошёл — впиши его в международном виде, "
                               "например +79990000000",
    "PhoneNumberBannedError": "этот номер заблокирован в Telegram",
    "PhoneNumberFloodError": "с этого номера сегодня слишком много попыток входа — "
                             "попробуй завтра",
    "PhoneCodeInvalidError": "код не подошёл — проверь цифры",
    "PhoneCodeExpiredError": "код просрочен — запроси новый",
    "PhoneCodeEmptyError": "код не введён",
    "SessionPasswordNeededError": "у аккаунта включён облачный пароль — введи его",
    "PasswordHashInvalidError": "облачный пароль не подошёл",
    "AuthKeyDuplicatedError": "этот аккаунт вошёл с другого устройства — выйди там и "
                              "попробуй снова",
    "AuthKeyUnregisteredError": "сессия больше не действует — войди заново",
    "ConnectionError": "нет связи с Telegram — проверь интернет",
    "TimeoutError": "Telegram не ответил вовремя — попробуй ещё раз",
    "ValueError": "не заполнено обязательное поле (api_id, api_hash или телефон)",
    "ImportError": "библиотека Telethon не установлена в этой сборке",
    "ModuleNotFoundError": "библиотека Telethon не установлена в этой сборке",
}


def _say(exc: BaseException) -> tuple[str, str]:
    """(человеческая строка, сырая) — сырая едет отдельным полем, для лога."""
    name = exc.__class__.__name__
    raw = f"{name}: {exc}"
    return _HUMAN.get(name, "Telegram отказал во входе"), raw


def _fail(exc: BaseException, **extra) -> None:
    human, raw = _say(exc)
    _out(ok=False, error=human, detail=raw, **extra)


def _check(args) -> str:
    """Пустые поля ловим ДО Telethon: `int('')` роняло ValueError мимо всего."""
    if not str(args.api_id or "").strip().isdigit():
        return "не заполнен api_id — он числом, со страницы my.telegram.org"
    if not str(args.api_hash or "").strip():
        return "не заполнен api_hash — он рядом с api_id на my.telegram.org"
    if args.step in ("send", "code") and not str(args.phone or "").strip():
        return "не заполнен телефон — в международном виде, например +79990000000"
    if args.step == "code" and not str(args.code or "").strip():
        return "не введён код из Telegram"
    return ""


async def main(args) -> None:
    from telethon import TelegramClient
    from telethon.errors import (FloodWaitError, PhoneCodeExpiredError,
                                 PhoneCodeInvalidError, SessionPasswordNeededError)

    session = Path(args.session)
    session.parent.mkdir(parents=True, exist_ok=True)
    codehash = session.with_suffix(".codehash")
    client = TelegramClient(str(session), int(args.api_id), args.api_hash)
    await client.connect()
    try:
        if args.step == "logout":
            # ⚠ Раньше здесь было три беды подряд: `log_out()` под голым
            # `except: pass`, `unlink()` ПОКА Telethon держит .session открытым
            # (на Windows это PermissionError, тоже проглоченный) — и после всего
            # безусловное ok=True. Владелец видел зелёное «вышли», а сессия была
            # цела и на диске, и на стороне Telegram: следующий старт руннера
            # снова входил в аккаунт, из которого владелец вышел.
            told, gone = "", []
            try:
                await client.log_out()
            except Exception as exc:
                told = _say(exc)[1]
            await client.disconnect()          # ДО удаления файла, иначе WinError 32
            for p in (session.with_suffix(".session"), codehash):
                try:
                    p.unlink()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    gone.append(f"{p.name}: {exc}")
            if gone:
                _out(ok=False, state="authorized",
                     error="сессия осталась на диске — закрой Hélène и попробуй снова",
                     detail="; ".join(gone + ([told] if told else [])))
                return
            if told:
                _out(ok=True, state="unauthorized",
                     error="файлы сессии удалены, но Telegram о выходе не узнал "
                           "(не было связи) — отзови сессию в приложении Telegram",
                     detail=told)
                return
            _out(ok=True, state="unauthorized")
            return
        if await client.is_user_authorized():
            me = await client.get_me()
            _out(ok=True, state="authorized", username=me.username or "",
                 id=me.id, name=" ".join(x for x in (me.first_name, me.last_name) if x))
            return
        if args.step == "status":
            _out(ok=True, state="unauthorized")
            return
        if args.step == "send":
            sent = await client.send_code_request(args.phone)
            codehash.write_text(sent.phone_code_hash, encoding="utf-8")
            _out(ok=True, state="code_sent")
            return
        if args.step == "code":
            phash = codehash.read_text(encoding="utf-8").strip() if codehash.exists() else ""
            try:
                await client.sign_in(args.phone, args.code, phone_code_hash=phash)
            except SessionPasswordNeededError:
                if not args.password:
                    _out(ok=False, state="password_needed",
                         error="у аккаунта включён облачный пароль — введи его")
                    return
                await client.sign_in(password=args.password)
            me = await client.get_me()
            try:
                codehash.unlink()
            except OSError:
                pass
            _out(ok=True, state="authorized", username=me.username or "", id=me.id,
                 name=" ".join(x for x in (me.first_name, me.last_name) if x))
            return
        _out(ok=False, error=f"неизвестный шаг {args.step}")
    except (PhoneCodeInvalidError, PhoneCodeExpiredError) as exc:
        _fail(exc, state="code_sent")
    except FloodWaitError as exc:
        _out(ok=False, error=f"Telegram просит подождать {exc.seconds} с",
             detail=f"FloodWaitError: {exc}")
    except Exception as exc:
        _fail(exc)
    finally:
        await client.disconnect()


def _from_env(args) -> None:
    """Секреты — из ОКРУЖЕНИЯ, а не из командной строки.

    ⚠ На Windows командную строку чужого процесса читает любой процесс того же
    пользователя (`Win32_Process.CommandLine`, «wmic process get commandline»),
    и она же попадает в аудит запусков (Sysmon Event 1, 4688 с включённой
    политикой). Пароль двухфакторки Telegram и api_hash — секреты долгоживущие:
    пароль владелец не меняет годами, api_hash выдаётся один раз на приложение.
    Держать их в argv значило раздавать их всему, что смотрит на процессы.

    Окружение читает ребёнок и его дети, но не соседи; оболочка кладёт их
    `cmd.env` (shell/src/main.rs, `telegram_account`). Аргументы остаются
    запасным путём — чтобы помощник можно было позвать руками при разборе.
    """
    import os
    for field, name in (("api_hash", "HELENE_TG_API_HASH"),
                        ("code", "HELENE_TG_CODE"),
                        ("password", "HELENE_TG_PASSWORD"),
                        ("api_id", "HELENE_TG_API_ID"),
                        ("phone", "HELENE_TG_PHONE")):
        value = os.environ.get(name)
        if value is not None and not str(getattr(args, field, "") or "").strip():
            setattr(args, field, value)
            os.environ.pop(name, None)     # дальше по дереву процессов не едет


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--api-id", default="")
    ap.add_argument("--api-hash", default="")
    ap.add_argument("--phone", default="")
    ap.add_argument("--code", default="")
    ap.add_argument("--password", default="")
    ap.add_argument("step", choices=["status", "send", "code", "logout"])
    parsed = ap.parse_args()
    _from_env(parsed)
    problem = _check(parsed)
    if problem:
        _out(ok=False, error=problem)
        raise SystemExit(0)
    try:
        asyncio.run(main(parsed))
    except Exception as exc:  # даже импорт Telethon — честной строкой, не трейсом
        _fail(exc)
