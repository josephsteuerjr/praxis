"""Independent interface and model-context language settings.

Missing frame_language means Russian for an existing agent. Creation flows
choose their initial frame language explicitly; changing the interface never
changes it. This module does not rewrite personal Markdown or conversation data.
"""
from __future__ import annotations

LANGUAGES = ("ru", "en", "fr", "zh")


def from_tag(tag: str) -> str:
    tag = str(tag or "").strip().lower().replace("_", "-")
    parts = tag.split("-")
    if parts[0] == "zh":
        if any(part in {"hant", "tw", "hk", "mo"} for part in parts[1:]):
            return ""
        return "zh" if len(parts) == 1 or any(part in {"hans", "cn", "sg"} for part in parts[1:]) else ""
    return parts[0] if parts[0] in {"ru", "en", "fr"} else ""


def interface_language(config: dict, system_tags=()) -> str:
    selected = config.get("lang")
    if selected in LANGUAGES:
        return selected
    return next((found for tag in system_tags if (found := from_tag(tag))), "en")


def frame_language(config: dict) -> str:
    agent = config.get("agent")
    value = agent.get("frame_language", "ru") if isinstance(agent, dict) else "ru"
    return value if value in LANGUAGES else "ru"


def living_prose(config: dict) -> bool:
    agent = config.get("agent")
    return not isinstance(agent, dict) or agent.get("living_prose", True) is not False


def validate(config: dict) -> None:
    if "lang" in config and config["lang"] not in ("", *LANGUAGES):
        raise ValueError("lang must be automatic (empty), ru, en, fr or zh")
    agent = config.get("agent")
    if agent is not None and not isinstance(agent, dict):
        raise ValueError("agent must be an object")
    if not isinstance(agent, dict):
        return
    if "frame_language" in agent and agent["frame_language"] not in LANGUAGES:
        raise ValueError("agent.frame_language must be ru, en, fr or zh")
    if "living_prose" in agent and type(agent["living_prose"]) is not bool:
        raise ValueError("agent.living_prose must be true or false")
