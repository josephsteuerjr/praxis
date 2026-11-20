"""Model-visible functions for the owner's Telegram, with one published schema each."""


def _param(kind, description, *, enum=None):
    field = {"type": kind, "description": description}
    if enum is not None:
        field["enum"] = enum
    return field


def _tool(name, description, required, **properties):
    return {"name": name, "description": description, "input_schema": {
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }}


CHAT = _param("string", "Telegram chat ID, @username, t.me link, or me for Saved Messages.")
TOPIC = _param("integer", "Forum topic ID, if applicable.")
MESSAGE = _param("integer", "Numeric Telegram message ID in this chat.")
LIMIT = _param("integer", "Maximum number of results, from 1 to 100.")

TOOLS = [
    _tool("yegor_read", "Read messages from Yegor's Telegram account.", ["chat"],
          chat=CHAT, topic=TOPIC, limit=LIMIT, message=MESSAGE,
          before=_param("integer", "Return messages older than this message ID.")),
    _tool("yegor_search", "Search messages or find a dialog in Yegor's Telegram account.", ["query"],
          query=_param("string", "Text to search for."), chat=CHAT, topic=TOPIC, limit=LIMIT,
          dialogs=_param("boolean", "Search dialog names and usernames instead of message text.")),
    _tool("yegor_send", "Send text as Yegor and return Telegram's delivery receipt.", ["chat", "text"],
          chat=CHAT, text=_param("string", "Text to send."), topic=TOPIC,
          reply_to=_param("integer", "Reply to this message ID.")),
    _tool("yegor_media", "Send or download an attachment through Yegor's Telegram account.", ["action", "chat"],
          action=_param("string", "Operation.", enum=["send", "download"]), chat=CHAT,
          path=_param("string", "Local file path for send."), message=MESSAGE,
          caption=_param("string", "Caption for an uploaded attachment."), topic=TOPIC,
          reply_to=_param("integer", "Reply to this message ID when sending."),
          kind=_param("string", "How to present an attachment.", enum=["document", "photo", "audio"]),
          voice=_param("boolean", "Send audio as a Telegram voice note.")),
    _tool("yegor_mtproto", "Discover and call Telegram API methods as Yegor. Includes membership, "
          "forums, moderation, forwarding, dialogs, profile, and account management.", ["action"],
          action=_param("string", "Operation.", enum=["list", "search", "describe", "call"]),
          request=_param("string", "Exact request constructor from search or describe."),
          query=_param("string", "Search method names."), params=_param("object", "Parameters from describe."),
          limit=LIMIT, offset=_param("integer", "Offset in the method catalogue.")),
    _tool("yegor_wake_rules", "Read, replace or preview the rules that let Yegor's Telegram start a turn. "
          "An empty rules list leaves the account quiet.", ["action"],
          action=_param("string", "Operation.", enum=["get", "set", "preview"]),
          names={**_param("array", "Text names or nicknames to match in addition to Telegram mentions."),
                 "items": {"type": "string"}},
          revision=_param("integer", "Replace only if the current policy still has this revision."),
          rules={**_param("array", "Each entry has a name, all and/or any signals, and optional chat/topic. "
                          "Rules are alternatives. Use get for available signals."),
                 "items": {"type": "object", "required": ["name"], "additionalProperties": False,
                           "properties": {"name": {"type": "string"},
                                          "all": {"type": "array", "items": {"type": "string"}},
                                          "any": {"type": "array", "items": {"type": "string"}},
                                          "chat": {"type": "string"}, "topic": {"type": "integer"}}}}),
]

NAMES = frozenset(tool["name"] for tool in TOOLS)
