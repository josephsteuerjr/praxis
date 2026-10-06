"""Переводные вклейки CJK (договор с участником комнаты, 27.09).

Прежний санитайзер (23–26.09) вырезал обособленные кластеры иероглифов из
не-CJK речи — дефект подстановки по эмбеддингу у glm-5.3. Договорённость
с участником комнаты (27.09): не вырезать, а тихо заменять переводом в скобках
«⟨вклейка: перевод⟩». Двухступенчато:

1. табличный словарь (набранные вручную переводы частых вклеек);
2. при промахе — дешёвый запрос перевода «переведи на русский: <кластер>»,
   переводы кэшируются на диск (memory/cjk-vkladki-cache.json);
3. при падении запроса — fallback на исходный кластер в тех же скобках.

Модуль детерминирован от лица санитайзера: один и тот же текст + один кэш
дают один и тот же результат (требование леджера согласованности outbox).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading

log = logging.getLogger("praxis.cjk-vkladki")

# Разделители вклейки. Угольные скобки ⟨…⟩ не встречаются в обычной речи
# и уже используются разметкой кадра — читатель их знает.
L, R = "⟨вклейка: ", "⟩"

# Ручной словарь частых вклеек (по наблюдениям 23–26.09 в AbstractDL).
TABLE: dict[str, str] = {
    "同步": "синхронизация",
    "汇总": "сводка",
    "包括": "включая",
    "半": "половина",
    "我": "я",
    "记录": "запись",
    "更新": "обновление",
    "检查": "проверка",
    "完成": "готово",
    "问题": "вопрос",
    # 01.10: расширение за пределы CJK — живые экспонаты AbstractDL
    "الآن": "сейчас",
    "مشروع": "проект",
}

_CACHE_LOCK = threading.Lock()


def cache_path():
    import agent as _agent
    return _agent.MEM_DIR / "cjk-vkladki-cache.json"


def _load_cache() -> dict:
    try:
        with open(cache_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache: dict) -> None:
    p = cache_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)
    os.replace(tmp, p)


def _cheap_translate(cluster: str) -> str | None:
    """Дешёвый запрос перевода. None = не смогли (fallback на кластер)."""
    if os.environ.get("PRAXIS_CJK_VKLADKI_LLM", "1") != "1":
        return None
    try:
        import llm
        resp = llm.chat(
            "evaluator",
            system="Ты переводчик. Отвечай ТОЛЬКО переводом, без комментариев.",
            messages=[{"role": "user",
                       "content": f"переведи на русский: {cluster}"}],
            max_tokens=200,
        )
        text = re.sub(r"\s+", " ", (resp.text or "")).strip()
        # отказ модели/мусор — не перевод
        if not text or len(text) > 120 or "\n" in (resp.text or ""):
            return None
        return text
    except Exception as exc:  # noqa: BLE001 — любая беда канала = fallback
        log.warning("cjk-vkladki: перевод не удался (%s): %s", cluster, exc)
        return None


def translate_cluster(cluster: str) -> str:
    """Перевод кластера: словарь → кэш → дешёвый запрос → исходный кластер."""
    if cluster in TABLE:
        return TABLE[cluster]
    with _CACHE_LOCK:
        cache = _load_cache()
        if cluster in cache:
            hit = cache[cluster]
            return hit if hit else cluster   # пусто = прошлый промах, не дёргаем LLM
        got = _cheap_translate(cluster)
        cache[cluster] = got or ""
        try:
            _save_cache(cache)
        except OSError as exc:
            log.warning("cjk-vkladki: кэш не записан: %s", exc)
        return got if got else cluster


def _cheap_translate_ctx(cluster: str, left: str, right: str) -> str | None:
    """Контекстный перевод внутрисловной вклейки (28.09): кластер вклеен
    ВНУТРЬ русского текста и заменяет собой русское слово. Слепой перевод
    без контекста даёт ломаную грамматику («这两» → «Эти два» вместо
    «эти две»). Просим форму, встающую на место кластера как есть."""
    if os.environ.get("PRAXIS_CJK_VKLADKI_LLM", "1") != "1":
        return None
    try:
        import llm
        resp = llm.chat(
            "evaluator",
            system=(
                "Ты редактор. В русском тексте на месте 【…】 стоит сбойная "
                "иероглифская подстановка, заменившая собой русское слово. "
                "Отвечай ТОЛЬКО русским фрагментом (1–4 слова) в нужной форме "
                "и регистре, который встаёт вместо 【…】 без правки окружения. "
                "Без кавычек и комментариев."
            ),
            messages=[{"role": "user",
                       "content": f"…{left}【{cluster}】{right}…"}],
            max_tokens=200,
        )
        text = re.sub(r"\s+", " ", (resp.text or "")).strip()
        if not text or len(text) > 120 or "\n" in (resp.text or ""):
            return None
        return text
    except Exception as exc:  # noqa: BLE001
        log.warning("cjk-vkladki: контекстный перевод не удался (%s): %s", cluster, exc)
        return None


def _ctx_key(cluster: str, left: str, right: str) -> str:
    return f"ctx::{cluster}::{left[-12:]}::{right[:12]}"


def translate_cluster_ctx(cluster: str, left: str, right: str) -> str:
    """Контекстный перевод: кэш по (кластер+окно) → LLM → слепой → кластер.

    Слепой fallback вызывается ВНЕ _CACHE_LOCK: translate_cluster берёт
    ту же блокировку, а она не реентерабельна (дедлок, найден тестами 28.09).
    """
    key = _ctx_key(cluster, left, right)
    with _CACHE_LOCK:
        cache = _load_cache()
        if key in cache:
            hit = cache[key]
            if hit:
                return hit
        else:
            got = _cheap_translate_ctx(cluster, left, right)
            cache[key] = got or ""
            try:
                _save_cache(cache)
            except OSError as exc:
                log.warning("cjk-vkladki: кэш не записан: %s", exc)
            if got:
                return got
    return translate_cluster(cluster)  # слепой fallback — вне блокировки


def inlay(cluster: str) -> str:
    """Готовая вклейка для кластера."""
    return f"{L}{translate_cluster(cluster)}{R}"


def inlay_ctx(cluster: str, left: str, right: str) -> str:
    """Вклейка для внутрисловного кластера: перевод с учётом окружения."""
    return f"{L}{translate_cluster_ctx(cluster, left, right)}{R}"
