"""Provider-neutral validation for persisted mixed tool offerings.

Praxis stores the exact model tool list in durable checkpoints.  That list can
contain both locally dispatched function tools and a provider-executed hosted
web-search descriptor.  Recovery must validate the same shape as the live loop
without ever treating the hosted tool as a locally replayable function.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable


class ToolOfferingError(ValueError):
    """One offered descriptor is malformed or aliases another tool identity."""


def local_function_names(tools: Iterable[object]) -> frozenset[str]:
    """Return locally dispatchable names after strict mixed-list validation."""

    names: set[str] = set()
    identities: set[str] = set()
    for index, schema in enumerate(tools):
        if not isinstance(schema, dict):
            raise ToolOfferingError(f"offered tool[{index}] is malformed or duplicated")
        server_type = str(schema.get("type") or "")
        if server_type in {"web_search", "web_search_20250305"}:
            allowed_keys = ({"type", "search_context_size", "external_web_access", "max_uses"}
                            if server_type == "web_search"
                            else {"type", "name", "max_uses"})
            malformed = bool(set(schema) - allowed_keys)
            if server_type == "web_search":
                malformed = malformed or "name" in schema or "input_schema" in schema
                context_size = schema.get("search_context_size")
                malformed = malformed or (
                    context_size is not None
                    and (not isinstance(context_size, str)
                         or context_size not in {"low", "medium", "high"})
                )
                external = schema.get("external_web_access")
                malformed = malformed or (
                    external is not None and not isinstance(external, bool)
                )
            else:
                malformed = (malformed or schema.get("name") != "web_search"
                             or "input_schema" in schema)
            max_uses = schema.get("max_uses")
            malformed = malformed or (
                max_uses is not None
                and (isinstance(max_uses, bool) or not isinstance(max_uses, int)
                     or not 1 <= max_uses <= 10)
            )
            if malformed:
                raise ToolOfferingError(
                    f"offered tool[{index}] is malformed or duplicated")
            identity = "web_search"
            if identity in identities:
                raise ToolOfferingError(
                    f"offered tool[{index}] is malformed or duplicated")
            identities.add(identity)
            continue
        if "type" in schema:
            raise ToolOfferingError(f"offered tool[{index}] is malformed or duplicated")
        name = schema.get("name")
        if (not isinstance(name, str) or not name or name != name.strip()
                or name in identities):
            raise ToolOfferingError(f"offered tool[{index}] is malformed or duplicated")
        identities.add(name)
        names.add(name)
    return frozenset(names)


def offered_names(tools: Iterable[object] | None) -> tuple[str, ...]:
    """Имена предложенных рук В ПОРЯДКЕ ВЫДАЧИ — ровно то, что видит провайдер.

    Порядок сохраняется, потому что он стоит денег: у Anthropic ``cache_control``
    breakpoint висит на ПОСЛЕДНЕМ описании (её слово 18.08 — ``end_turn`` последний),
    у OpenAI-совместимых схемы едут ВЫШЕ system. Реордер обходится ровно как смена
    состава, и обе беды обязаны быть видны одним и тем же прибором.

    Hosted-поиск в OpenAI-форме приезжает без ``name`` (только ``type``) — он
    называется типом в квадратных скобках. Слить две формы одной способности в одно
    имя значило бы соврать: у них разные байты. Ни один элемент не исчезает молча —
    невнятный описатель называется ``[?]``.
    """

    names: list[str] = []
    for schema in tools or ():
        if not isinstance(schema, dict):
            names.append("[?]")
            continue
        name = str(schema.get("name") or "").strip()
        if not name:
            name = f"[{str(schema.get('type') or '?').strip() or '?'}]"
        names.append(name)
    return tuple(names)


def fingerprint(tools: Iterable[object] | None) -> str:
    """sha256 набора рук в порядке выдачи — отпечаток, по которому эпоха видит смену.

    Схемы рук едут выше system и в ``prompt_cache_key`` не входят: без отпечатка
    смена набора убивает байтовый префикс молча.
    """

    joined = "\n".join(offered_names(tools))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


__all__ = ["ToolOfferingError", "local_function_names", "offered_names",
           "fingerprint"]
