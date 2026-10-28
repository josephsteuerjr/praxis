"""Opt-in, local spelling advice. No rewrite, persistence, transport or retry policy."""
import json
import os
import re
import subprocess
import time

BUDGET_SECONDS = 0.1
MAX_CHARS = 8000
MAX_WORDS = 400
MAX_FINDINGS = 8
ADVICE_RU = ("Не отправлено: исправь текст и повтори либо оставь авторский текст, "
             "передав spelling_confirm_text, в точности равный аргументу text. "
             "Это совет, не транспортная ошибка; не повторяй доставку с неизвестным исходом.")
ADVICE_EN = ("Not sent: edit and call again, or keep your authored text by setting "
             "spelling_confirm_text exactly equal to text. This is advice, not a transport "
             "error; do not retry delivery with an unknown outcome.")
CONFIRM_RU = "Осознанно оставить авторский текст: точная копия аргумента text, не флаг."
CONFIRM_EN = "Explicitly keep authored text: an exact copy of the text argument, not a flag."


def _words(text):
    # Limited prose mode deliberately ignores markup/code, URLs, handles, paths,
    # mixed-script identifiers, capitals (names) and digit/underscore tokens.
    text = re.sub(r'```.*?```|`[^`]*`|https?://\S+|@\S+', ' ', text, flags=re.S)
    tokens = re.findall(r'[^\s]+', text)
    result = {'ru': [], 'en_US': []}
    for token in tokens:
        if any(c in token for c in '/\\_0123456789='):
            continue
        token = token.strip('.,!?;:()[]{}«»"\'—–-')
        if len(token) < 3 or not token.islower():
            continue
        if re.fullmatch('[а-яё]+', token):
            result['ru'].append(token)
        elif re.fullmatch('[a-z]+', token):
            result['en_US'].append(token)
    return result


def preflight(text, spelling_confirm_text=None):
    """Return (structured refusal JSON or None, optional fail-open receipt note)."""
    if spelling_confirm_text is not None:
        if isinstance(spelling_confirm_text, str) and spelling_confirm_text == text:
            return None, 'spelling: authored exact text explicitly confirmed'
        return json.dumps({'status': 'not_sent', 'reason': 'review_required',
                           'detail': 'confirmation_text_mismatch',
                           'advice_ru': ADVICE_RU, 'advice_en': ADVICE_EN},
                          ensure_ascii=False), ''
    mode = os.environ.get('PRAXIS_OUTGOING_SPELLING', 'off')
    if mode == 'off':
        return None, ''
    if mode != 'limited':
        return None, 'spelling: skipped (invalid mode; use off or limited)'
    if len(text) > MAX_CHARS:
        return None, 'spelling: skipped (limited-mode text budget)'
    words = _words(text)
    if sum(map(len, words.values())) > MAX_WORDS:
        return None, 'spelling: skipped (limited-mode word budget)'
    deadline = time.monotonic() + BUDGET_SECONDS
    findings = []
    try:
        for lang, tokens in words.items():
            if not tokens:
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('aspell', BUDGET_SECONDS)
            proc = subprocess.run(['aspell', '--encoding=utf-8', '--lang=' + lang, 'list'],
                                  input='\n'.join(tokens) + '\n', text=True,
                                  capture_output=True, timeout=remaining, check=True)
            # Never trust external output as suggestions or authored replacement.
            findings.extend(w for w in proc.stdout.splitlines() if w in tokens)
    except (OSError, subprocess.SubprocessError, UnicodeError):
        return None, 'spelling: unavailable or timed out; fail-open, original text retained'
    findings = list(dict.fromkeys(findings))
    if not findings:
        return None, ''
    return json.dumps({'status': 'not_sent', 'reason': 'review_required',
                       'findings': findings[:MAX_FINDINGS],
                       'truncated': len(findings) > MAX_FINDINGS,
                       'mode': 'limited', 'advice_ru': ADVICE_RU, 'advice_en': ADVICE_EN},
                      ensure_ascii=False), ''


def receipt(out, note):
    """Keep str subclasses (notably DirectSendRefusal) and existing delivery semantics."""
    return type(out)(str(out) + '\n\n' + note) if note and isinstance(out, str) else out
