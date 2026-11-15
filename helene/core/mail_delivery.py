"""Coded mail outcome with owned presentation; raw mail and diagnostics stay literal."""
from __future__ import annotations

import copy
import json
import frame_language

_REASON_FIELDS = frozenset({'reason', 'reason_clipped', 'reason_limit'})
_MESSAGE_FIELDS = frozenset({'to', 'subject', 'attached', 'refused'})
_FIELDS = {
    'not_configured': frozenset(),
    'bad_address': frozenset({'to'}),
    'attachment_unreadable': frozenset({'path'}) | _REASON_FIELDS,
    'attachment_cap': frozenset({'total', 'cap', 'paths'}),
    'accepted': _MESSAGE_FIELDS,
    'accepted_close': _MESSAGE_FIELDS | _REASON_FIELDS,
    'refused': frozenset({'stage'}) | _REASON_FIELDS,
    'pre_send': frozenset({'stage'}) | _REASON_FIELDS,
    'unknown': _MESSAGE_FIELDS | _REASON_FIELDS,
    'missing': frozenset({'hash'}),
    'no_draft': frozenset(),
    'already_sent': frozenset(),
    'bad_reply': frozenset({'to'}),
    'untyped': frozenset({'response'}),
}
_OUTCOMES = {kind: ('accepted' if kind in {'accepted', 'accepted_close'} else
                   'unknown' if kind in {'unknown', 'untyped'} else 'not_sent')
             for kind in _FIELDS}


def reason_parts(text: object, limit: int) -> dict:
    """Same word-boundary excerpt as the old sender, with a language-neutral cut flag."""
    value = ' '.join(str(text or '').split())
    clipped = len(value) > limit
    if clipped:
        head = value[:limit]
        space = head.rfind(' ')
        if space > limit // 2:
            head = head[:space]
        value = head.rstrip(' ,;:.—-')
    return {'reason': value, 'reason_clipped': clipped, 'reason_limit': limit}


def reason_text(reason: str, reason_clipped: bool, reason_limit: int) -> str:
    if not reason_clipped:
        return reason
    return reason + frame_language.text('mail.result.reason_clipped', cap=reason_limit)


def _t(key, **values):
    return frame_language.text('mail.result.' + key, **values)


def _render(kind: str, data: dict) -> str:
    reason = reason_text(**{key: data[key] for key in _REASON_FIELDS}) if 'reason' in data else ''
    if kind in {'accepted', 'accepted_close', 'unknown'}:
        attached = data['attached']
        attachments = _t('attachments', items=', '.join(
            _t('attachment', name=name, size=size) for name, size in attached)) if attached else ''
        refused = _t('refused_recipients', data=data['refused']) if data['refused'] else ''
        values = {'to': data['to'], 'subject': data['subject'],
                  'attachments': attachments, 'refused': refused}
        if kind != 'accepted':
            values['reason'] = reason
        return _t(kind, **values)
    if kind in {'refused', 'pre_send'}:
        stage = data['stage']
        if stage not in {'connect', 'login', 'transfer'}:
            raise ValueError('unknown mail stage')
        return _t(kind, stage=_t('stage_' + stage), reason=reason)
    if kind == 'attachment_unreadable':
        return _t(kind, path=data['path'], reason=reason)
    if kind == 'attachment_cap':
        return _t(kind, total=data['total'], cap=data['cap'], paths=', '.join(data['paths']))
    if kind in {'bad_address', 'bad_reply'}:
        return _t(kind, to=repr(data['to']))
    return _t(kind, **data)


def _validate(kind, data):
    if kind not in _FIELDS or set(data) != _FIELDS[kind]:
        raise ValueError('mail delivery kind/fields differ')


def _restore(kind: str, data: dict, text: str):
    """Worker IPC/copy preserves the original presentation and outcome, without sending."""
    _validate(kind, data)
    result = str.__new__(DeliveryResult, text)
    result._kind = kind
    result._data = copy.deepcopy(data)
    return result


class DeliveryResult(str):
    """str-compatible receipt; acceptance is never inferred from a translated sentence."""
    def __new__(cls, kind: str, **data):
        _validate(kind, data)
        data = copy.deepcopy(data)
        try:
            with frame_language.pin():
                text = _render(kind, data)
        except Exception as error:
            # A presentation fault after DATA acceptance must not falsify the outcome.
            # The emergency view is protocol data, with no second SMTP attempt.
            text = json.dumps({'mail_delivery': _OUTCOMES[kind], 'kind': kind,
                               'caption_error': type(error).__name__, 'data': data},
                              ensure_ascii=False)
        return _restore(kind, data, text)

    @property
    def outcome(self) -> str:
        return _OUTCOMES[self._kind]

    def for_language(self, language: str):
        """Render trusted owned parts for a UI; mail content/effect remains the same."""
        with frame_language.use(language):
            return DeliveryResult(self._kind, **self._data)

    def __reduce_ex__(self, protocol):
        return _restore, (self._kind, self._data, str(self))


def as_delivery_result(value) -> DeliveryResult:
    if isinstance(value, DeliveryResult):
        return value
    return DeliveryResult('untyped', response=str(value))
