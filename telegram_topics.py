"""Small, dependency-free routing contract for Telegram forum topics.

Telegram authorization and room policy belong to the root peer.  Conversation state,
however, belongs to a concrete forum topic (``top_msg_id``).  Keeping those identities
separate prevents one topic from replacing another topic's wake/buffer while still
using the real peer id for Telethon delivery and access checks.
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_STATE_SEP = "__topic__"
_SELECTOR_RE = re.compile(r"^(?P<peer>.+?)#(?:topic|thread):(?P<topic>[1-9]\d*)$", re.I)

# General — полноценная тема форума, и её id всегда 1: это номер первого сообщения
# комнаты, а нумерация в комнате одна, сквозная.  Хребтом General при этом служит голый
# ключ комнаты (так эта память жила всегда), поэтому маркер темы 1 нормализуется в
# комнату — иначе у General выросло бы два параллельных хвоста.
GENERAL_TOPIC_ID = 1


@dataclass(frozen=True, slots=True)
class TopicRoute:
    """Root Telegram peer plus an optional forum/comment-thread root message id."""

    peer_id: str
    topic_id: int | None = None

    def __post_init__(self) -> None:
        peer = str(self.peer_id).strip()
        if not peer:
            raise ValueError("peer_id is empty")
        topic = None if self.topic_id is None else int(self.topic_id)
        if topic is not None and topic <= 0:
            raise ValueError("topic_id must be positive")
        object.__setattr__(self, "peer_id", peer)
        object.__setattr__(self, "topic_id", topic)

    @property
    def conversation_id(self) -> str:
        """Filesystem-safe, restart-stable key for all per-conversation state."""

        if self.topic_id is None:
            return self.peer_id
        return f"{self.peer_id}{_STATE_SEP}{self.topic_id}"

    @property
    def selector(self) -> str:
        """Human/tool-facing explicit route accepted by read/send helpers."""

        if self.topic_id is None:
            return self.peer_id
        return f"{self.peer_id}#topic:{self.topic_id}"


def is_topic_opener(message) -> bool:
    return type(getattr(message, "action", None)).__name__ == "MessageActionTopicCreate"


def topic_opener_title(message) -> str:
    """Return the title of a ``MessageActionTopicCreate`` service message.

    Kept structural so persisted routing and hermetic tests do not require a live
    Telethon import.  Telegram uses the service message's own id as the forum root.
    """

    action = getattr(message, "action", None)
    if not is_topic_opener(message):
        return ""
    return str(getattr(action, "title", "") or "").strip()


def thread_root_for_message(message) -> int | None:
    """Reply-chain root of one message — a *behavioural* identity, never a storage key.

    Conversation state belongs to the room (see ``route_for_message``); pacing and
    addressing still belong to the branch, so cooldown, supersede and reply targeting
    can stay per-thread while she reads one continuous room.  Telegram gives the root
    as ``reply_to_top_id`` from the second level down, and only ``reply_to_msg_id`` on
    the first reply; a message that answers nothing opens its own chain.
    """

    header = getattr(message, "reply_to", None)
    for owner, name in ((header, "reply_to_top_id"), (message, "reply_to_top_id"),
                        (header, "reply_to_msg_id"), (message, "reply_to_msg_id"),
                        (message, "id")):
        if owner is None:
            continue
        value = getattr(owner, name, None)
        if value is None or isinstance(value, bool):
            continue
        try:
            root = int(value)
        except (TypeError, ValueError):
            continue
        if root > 0:
            return root
    return None


def route_for_message(peer_id: str | int, message, *, is_private: bool = False,
                      is_forum: bool | None = None,
                      confirmed_topics=None) -> TopicRoute:
    """Storage route of one message: the room, or a REAL forum topic of that room.

    МЕСТО — это комната либо настоящая тема из каталога Telegram.  АДРЕС — кому это
    сообщение отвечает и в какой ветке идёт ход — считается отдельно
    (``thread_root_for_message``) и ключом хранения не становится никогда.

    ``is_forum`` is the *chat's* own nature, not the message's:

    * ``False`` — ordinary supergroup or a channel's discussion chat: ONE flat room.
      Reply chains and comment threads are addresses inside it, never places.
    * ``None`` — nature not known.  For the STORAGE KEY this equals «not a forum»:
      the room (кроме опенера темы — он прямое свидетельство сам по себе).  «Не
      знаю» раньше оставляло старое поведение по заголовку — так новая комната
      минтила фантомные «темы» весь месяц до вердикта (AbstractDL: 552 выдуманные
      темы, 59% сообщений; замер 21.08.2026 по канону).
    * ``True`` — a real Telegram forum.  A topic becomes a storage key only when the
      room's own topic catalogue (``confirmed_topics`` — ids Telegram itself returned
      via a PROVEN-complete GetForumTopics sweep, plus openers) confirms its id.
      Everything the catalogue does not confirm is a reply chain of General, i.e. the
      room.  ``confirmed_topics=None`` («каталог ещё не наблюдался») для ключа
      хранения ТОЖЕ значит комнату — её решение 22.08, отменившее переходное
      «верим заголовку»: первый же ход успевал навсегда оставить фантомный хвост.
      Чтобы не потерять первую живую реплику настоящей темы, вызывающий добывает
      каталог single-flight ДО фиксации маршрута; при недоступности каталога
      fail-safe — комната, не заголовок.

    A topic opener mints its topic with or without the catalogue: the service message
    IS Telegram's own confirmation, the catalogue may simply not have refreshed yet.
    Opener of General (id 1) still normalises to the room — второй хвост General не
    рождается ни одним путём.  For an ordinary reply inside a topic,
    ``reply_to_msg_id`` is the immediate target and ``reply_to_top_id`` is the topic
    root.  For a reply to the topic opener itself, Telegram may omit
    ``reply_to_top_id``; then the immediate id is the root.  Private chats
    deliberately stay byte-compatible even if a synthetic test object happens to
    carry a similar header.
    """

    peer = str(peer_id)
    if is_private:
        return TopicRoute(peer)
    if is_forum is False:
        # An ordinary supergroup is one room.  Reply chains stay a *behavioural*
        # identity (wake, cooldown, reply addressing) computed per message; they never
        # become a storage key, so buffers, life spine, notes and promises keep one
        # tail per room.
        return TopicRoute(peer)
    if is_topic_opener(message):
        # Служебное «создана тема» минтит и при «не знаю»: оно само — прямое
        # свидетельство Telegram о настоящей теме, реестр мог просто не успеть.
        # General (id 1) нормализуется в комнату И здесь: её блокер 6 от 22.08 —
        # ранний return до нормализации рождал второй хвост General.
        try:
            opener_id = int(getattr(message, "id"))
        except (TypeError, ValueError):
            opener_id = 0
        if opener_id == GENERAL_TOPIC_ID:
            return TopicRoute(peer)
        if opener_id > 0:
            return TopicRoute(peer, opener_id)
    if is_forum is not True:
        # «Не знаю» для КЛЮЧА ХРАНЕНИЯ значит «не форум»: комната. Раньше здесь
        # оставалось поведение по заголовку — так новая комната минтила фантомные
        # «темы» весь срок до вердикта. Разница между False и None живёт в реестре
        # маршрутов и в словах ориентации, не в ключе хранения.
        return TopicRoute(peer)
    header = getattr(message, "reply_to", None)
    # Telethon exposes the header on ``message.reply_to``.  Small adapters and a
    # few older Message builds expose the projected fields directly instead, so
    # accept both shapes.  This is deliberately structural: importing Telethon
    # here would make persisted-route recovery depend on a live client package.
    top = getattr(header, "reply_to_top_id", None) if header is not None else None
    if top is None:
        top = getattr(message, "reply_to_top_id", None)
    forum = bool(getattr(header, "forum_topic", False)) if header is not None else False
    forum = forum or bool(getattr(message, "forum_topic", False))
    try:
        parsed_top = 0 if isinstance(top, bool) else int(top)
    except (TypeError, ValueError):
        parsed_top = 0
    if parsed_top <= 0 and forum:
        top = (getattr(header, "reply_to_msg_id", None) if header is not None
               else getattr(message, "reply_to_msg_id", None))
    if top is None:
        return TopicRoute(peer)
    try:
        topic = 0 if isinstance(top, bool) else int(top)
    except (TypeError, ValueError):
        return TopicRoute(peer)
    if topic <= 0:
        return TopicRoute(peer)
    if topic == GENERAL_TOPIC_ID:
        return TopicRoute(peer)
    if confirmed_topics is None:
        # Каталог не наблюдался — для ключа хранения это комната (её слово 22.08:
        # отсутствие полного знания означает комнату; fail-safe — комната, не
        # заголовок). Первую реплику настоящей темы спасает preflight вызывающего.
        return TopicRoute(peer)
    confirmed = set()
    for item in confirmed_topics:
        # Нечитаемая запись пропускается по одной: реестр отдаёт только числа, а
        # карать настоящие темы комнаты за чужой мусор — худшая из двух ошибок.
        try:
            confirmed.add(int(item))
        except (TypeError, ValueError):
            continue
    return TopicRoute(peer, topic) if topic in confirmed else TopicRoute(peer)


def _header_route(peer_id: str | int, message) -> TopicRoute:
    """Маршрут ПО ЗАГОЛОВКУ — поведение до починки 22.08. ТОЛЬКО для датчика.

    Живой код так больше не ходит нигде: это измерительная реплика старого
    извлечения, чтобы `measure_split` мог показывать разрыв истории (`keys_legacy`)
    после того, как живой маршрут перестал расщеплять.
    """
    peer = str(peer_id)
    if is_topic_opener(message):
        try:
            opener_id = int(getattr(message, "id"))
        except (TypeError, ValueError):
            opener_id = 0
        if opener_id > 0:
            return TopicRoute(peer, opener_id)
    header = getattr(message, "reply_to", None)
    top = getattr(header, "reply_to_top_id", None) if header is not None else None
    if top is None:
        top = getattr(message, "reply_to_top_id", None)
    forum = bool(getattr(header, "forum_topic", False)) if header is not None else False
    forum = forum or bool(getattr(message, "forum_topic", False))
    try:
        parsed_top = 0 if isinstance(top, bool) else int(top)
    except (TypeError, ValueError):
        parsed_top = 0
    if parsed_top <= 0 and forum:
        top = (getattr(header, "reply_to_msg_id", None) if header is not None
               else getattr(message, "reply_to_msg_id", None))
    if top is None:
        return TopicRoute(peer)
    try:
        topic = 0 if isinstance(top, bool) else int(top)
    except (TypeError, ValueError):
        return TopicRoute(peer)
    return TopicRoute(peer, topic) if topic > 0 else TopicRoute(peer)


def parse_selector(value: str | int) -> tuple[str, int | None]:
    """Parse ``<peer>#topic:<top_msg_id>`` without changing ordinary references."""

    raw = str(value).strip()
    match = _SELECTOR_RE.fullmatch(raw)
    if not match:
        return raw, None
    return match.group("peer").strip(), int(match.group("topic"))


def measure_split(peer_id: str | int, messages, *, is_forum: bool | None = None,
                  confirmed_topics=None) -> dict:
    """Датчик расщепления комнаты: во сколько ключей разговора разъезжается одна комната.

    Пункт 5 начинается отсюда, а не с правки маршрутизации. Утверждение «обычная
    супергруппа расщепляется на псевдо-темы» до сих пор было выводом из чтения кода;
    датчик делает его ИЗМЕРЕНИЕМ и, главное, оставляет постоянный регрессионный прибор:
    после починки то же число обязано схлопнуться, иначе починки не было.

    Идея подсмотрена у Арета (`deminded/mycelium-commons`): у них выгрузка нити дала 23
    сообщения против 142 в ленте того же диапазона — независимое полевое подтверждение
    того же поведения Telegram с другой стороны провода. Полноту не заверяет тот же
    орган, который выгружал.

    Считаем три маршрута на одних и тех же сообщениях: живой production (с тем
    знанием о комнате, которое передал вызывающий), контрфактический
    (`is_forum=False`) и legacy — поведение по заголовку, каким оно было до починки
    (измерительная реплика `_header_route`; живой код так больше не ходит). После
    починки датчик работает в обратную сторону: `keys_live` обязан схлопнуться к
    `keys_if_not_forum`, а разрыв истории остаётся виден в `keys_legacy`. Ничего не
    пишет и ничего не меняет.
    """
    peer = str(peer_id)
    live: dict[str, int] = {}
    flat: dict[str, int] = {}
    legacy: dict[str, int] = {}
    total = 0
    for msg in messages or ():
        if getattr(msg, "id", None) is None:
            continue
        total += 1
        a = route_for_message(peer, msg, is_private=False, is_forum=is_forum,
                              confirmed_topics=confirmed_topics).conversation_id
        b = route_for_message(peer, msg, is_private=False, is_forum=False).conversation_id
        c = _header_route(peer, msg).conversation_id
        live[a] = live.get(a, 0) + 1
        flat[b] = flat.get(b, 0) + 1
        legacy[c] = legacy.get(c, 0) + 1
    largest = max(live.values()) if live else 0
    return {
        "peer_id": peer,
        "messages": total,
        "keys_live": len(live),
        "keys_if_not_forum": len(flat),
        "keys_legacy": len(legacy),
        "largest_branch": largest,
        # Доля комнаты, видимая с самого крупного ключа. Это и есть «23 из 142»:
        # столько она читает, просыпаясь на этой ветке.
        "largest_branch_share": (round(largest / total, 3) if total else 0.0),
        "singleton_keys": sum(1 for n in live.values() if n == 1),
        "top_keys": dict(sorted(live.items(), key=lambda kv: kv[1], reverse=True)[:10]),
    }


def route_from_conversation_id(value: str | int) -> TopicRoute:
    """Reverse a persisted state key; malformed suffixes remain ordinary peer ids."""

    raw = str(value).strip()
    peer, sep, suffix = raw.rpartition(_STATE_SEP)
    if sep and peer and suffix.isdigit() and int(suffix) > 0:
        return TopicRoute(peer, int(suffix))
    return TopicRoute(raw)


def route_from_reference(value: str | int) -> TopicRoute:
    """Accept an explicit selector, a persisted state key, or an ordinary peer.

    Human-facing tools use ``<peer>#topic:<id>`` while durable queues and buffers
    use :attr:`TopicRoute.conversation_id`.  Centralising that translation keeps a
    selector from ever reaching Telethon as though it were an entity username.
    """

    peer, topic = parse_selector(value)
    if topic is not None:
        return TopicRoute(peer, topic)
    return route_from_conversation_id(peer)
