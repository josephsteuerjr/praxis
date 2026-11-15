"""
Praxis — почта (SMTP send + IMAP read). Без новых зависимостей (stdlib smtplib/imaplib).

Креды из env (держать в .deploy.env, gitignored):
  PRAXIS_EMAIL_ADDR, PRAXIS_EMAIL_PASS
Хосты (дефолты Mailfence):
  PRAXIS_SMTP_HOST=smtp.mailfence.com  PRAXIS_SMTP_PORT=465 (SSL)
  PRAXIS_IMAP_HOST=imap.mailfence.com  PRAXIS_IMAP_PORT=993 (SSL)

Почта — отдельный КАНАЛ. Исходящее по умолчанию owner-направлено (она шлёт, когда Егор просит);
автономная отправка — за env PRAXIS_EMAIL_AUTONOMOUS. Входящее от незнакомца не должно тянуть
приватку (scope как у unknown) — это гейтит вызывающий код в agent.py.
"""
from __future__ import annotations

import email as _email
import imaplib
import logging
import os
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from pathlib import Path
import frame_language
from mail_delivery import DeliveryResult, as_delivery_result, reason_parts, reason_text

# Суммарный кап на вложения (байты, до base64-накрутки ~33%+). Mailfence реально
# принимает сильно больше, но необъявленный гигантский файл в исходящем — это не
# «отправка письма», а сюрприз адресату; выше кап — отказ ДО соединения, целиком.
_ATTACH_TOTAL_MAX = 20 * 1024 * 1024

log = logging.getLogger("praxis-mail")

# модульные ссылки — чтобы легко мокать в тестах
SMTP_SSL = smtplib.SMTP_SSL
IMAP4_SSL = imaplib.IMAP4_SSL


def _cfg() -> dict:
    return {
        "addr": os.getenv("PRAXIS_EMAIL_ADDR", "").strip(),
        "pw": os.getenv("PRAXIS_EMAIL_PASS", ""),
        "smtp_host": os.getenv("PRAXIS_SMTP_HOST", "smtp.mailfence.com"),
        "smtp_port": int(os.getenv("PRAXIS_SMTP_PORT", "465") or 465),
        "imap_host": os.getenv("PRAXIS_IMAP_HOST", "imap.mailfence.com"),
        "imap_port": int(os.getenv("PRAXIS_IMAP_PORT", "993") or 993),
    }


def configured() -> bool:
    c = _cfg()
    return bool(c["addr"] and c["pw"])


def _decode(s: str | None) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:
        return s or ""


# Кап причины в ответе. Обрезка сама по себе законна, молчаливая обрезка — нет: полная
# причина всегда лежит в логе praxis-mail (`log.warning(..., exc_info=True)`), и ответ
# обязан сказать, что хвост там. Та же форма, что `agent._clip_reason`.
_REASON_CHARS = 400

# Ошибки, при которых сервер ЯВНО отказался принять письмо: адресат/отправитель отвергнут,
# команда не поддержана, DATA отбита кодом. Тут «не отправилось» — правда. Ошибки логина и
# соединения сюда НЕ входят: их и так ловит стадия, а слова «сервер отказался принять
# письмо» про неверный пароль были бы такой же неточностью, только с другой стороны.
_REFUSED = (
    smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
    smtplib.SMTPNotSupportedError, smtplib.SMTPDataError,
)


def _clip_reason(text: object, limit: int = _REASON_CHARS) -> str:
    return reason_text(**reason_parts(text, limit))


@frame_language.pinned
def send(to: str, subject: str, body: str, attachments: list[str] | None = None) -> str:
    """One SMTP attempt; coded outcome plus a str-compatible localized receipt."""
    c = _cfg()
    if not configured():
        return DeliveryResult('not_configured')
    to = (to or '').strip()
    if '@' not in to:
        return DeliveryResult('bad_address', to=to)
    msg = EmailMessage()
    msg['From'] = c['addr']
    msg['To'] = to
    msg['Subject'] = subject or frame_language.text('mail.result.no_subject')
    msg.set_content(body or '')
    attached: list[tuple[str, int]] = []
    total = 0
    for path in (attachments or []):
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError as e:
            return DeliveryResult('attachment_unreadable', path=str(p),
                                  **reason_parts(f'{type(e).__name__}: {e}', _REASON_CHARS))
        total += len(data)
        if total > _ATTACH_TOTAL_MAX:
            return DeliveryResult('attachment_cap', total=total, cap=_ATTACH_TOTAL_MAX,
                                  paths=tuple(str(Path(a)) for a in attachments or []))
        import mimetypes
        mime, _enc = mimetypes.guess_type(p.name)
        main, sub = (mime or 'application/octet-stream').split('/', 1)
        msg.add_attachment(data, maintype=main, subtype=sub, filename=p.name)
        attached.append((p.name, len(data)))
    subject_sent = str(msg['Subject'])
    refusals = {}
    def message_fields():
        return dict(to=to, subject=subject_sent, attached=tuple(attached),
                    refused=repr(refusals) if isinstance(refusals, dict) and refusals else '')
    stage = 'connect'
    try:
        ctx = ssl.create_default_context()
        with SMTP_SSL(c['smtp_host'], c['smtp_port'], context=ctx, timeout=30) as s:
            stage = 'login'
            s.login(c['addr'], c['pw'])
            stage = 'transfer'
            refusals = s.send_message(msg) or {}
            stage = 'accepted'
    except Exception as e:
        log.warning('send failed at stage=%s', stage, exc_info=True)
        reason = reason_parts(f'{type(e).__name__}: {e}', _REASON_CHARS)
        if stage == 'accepted':
            return DeliveryResult('accepted_close', **message_fields(), **reason)
        if stage != 'transfer' or isinstance(e, _REFUSED):
            kind = 'refused' if isinstance(e, _REFUSED) else 'pre_send'
            return DeliveryResult(kind, stage=stage, **reason)
        return DeliveryResult('unknown', **message_fields(), **reason)
    return DeliveryResult('accepted', **message_fields())


def _extract_body(m) -> str:
    if m.is_multipart():
        for part in m.walk():
            disp = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                payload = part.get_payload(decode=True) or b""
                return payload.decode(part.get_content_charset() or "utf-8", "ignore")
        return ""
    payload = m.get_payload(decode=True) or b""
    return payload.decode(m.get_content_charset() or "utf-8", "ignore")


def fetch(limit: int = 5, unseen_only: bool = False) -> list[dict]:
    """Последние письма из INBOX. -> [{from, subject, date, body, msg_id}], новые сверху.

    msg_id (Message-ID) нужен mailroom для дедупа входящих между поллами."""
    c = _cfg()
    if not configured():
        return []
    out: list[dict] = []
    M = None
    try:
        ctx = ssl.create_default_context()
        M = IMAP4_SSL(c["imap_host"], c["imap_port"], ssl_context=ctx)
        M.login(c["addr"], c["pw"])
        M.select("INBOX")
        typ, data = M.search(None, "UNSEEN" if unseen_only else "ALL")
        ids = (data[0].split() if (data and data[0]) else [])[-int(limit):]
        for i in reversed(ids):
            typ, msgdata = M.fetch(i, "(RFC822)")
            if typ != "OK" or not msgdata or not msgdata[0]:
                continue
            m = _email.message_from_bytes(msgdata[0][1])
            out.append({
                "from": _decode(m.get("From")),
                "subject": _decode(m.get("Subject")),
                "date": m.get("Date", ""),
                "body": (_extract_body(m) or "").strip()[:1500],
                "msg_id": (m.get("Message-ID") or m.get("Message-Id") or "").strip(),
            })
    except Exception:
        log.warning("fetch упал", exc_info=True)
    finally:
        if M is not None:
            try:
                M.logout()
            except Exception:
                pass
    return out
