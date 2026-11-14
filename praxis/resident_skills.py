"""Постоянные навыки — часть навыка, которая стоит в кадре каждого хода.

Навык — это `soul/skills/<slug>.md`: рабочая заметка, которую она открывает по
потребности (recall, fs_read). `my_capabilities` и указатель памяти перечисляют навыки
адресами, не телами. Некоторым навыкам этого мало: манера речи помогает, когда она перед
глазами в каждом ответе, а не когда о ней вспомнили.

Поэтому у навыка может быть ПОСТОЯННАЯ ЧАСТЬ: `soul/skills/<slug>/resident.md`. Её текст
входит в персону сразу после VOICE — тот же кэшируемый префикс и та же аудитория, что у
VOICE, — а сам навык и материалы в папке `soul/skills/<slug>/` по-прежнему открываются
по потребности. Папка навыка — её собственная конвенция (так уже устроен
`praxis-translation-check/`); постоянная часть — просто ещё один файл в ней.

Правила (каждое проверено тестом `test_resident_skills_1411`):

- порядок — по slug (casefold, затем байты имени): персона детерминирована байт-в-байт;
- нет файла или он пуст — постоянной части нет, секция объявляется отсутствующей;
- ссылки (symlink) не читаются: в персону попадает только то, что лежит в её `soul/`;
- общий потолок `PRAXIS_RESIDENT_SKILLS_MAX` (6000 знаков; `0` — без потолка).
  Текст, который не помещается, НЕ режется посередине: на его месте одна строка с
  адресом и размером. Молчаливой обрезки нет — тот же закон, что у тела INDEX;
- постоянная часть видна КАЖДОЙ аудитории, как VOICE: приватному там не место;
- файлы — её. Правка `resident.md` меняет персону со следующего хода; это граница кэша
  того же рода, что правка VOICE, и обещания сохранить кэш через неё нет.

Модуль ничего не пишет и никогда не бросает: отказ чтения превращается в пустую часть и
в строку отчёта, а не в несобранный кадр.
"""
from __future__ import annotations

import os
from pathlib import Path

RESIDENT_NAME = "resident.md"
DEFAULT_MAX_CHARS = 6000
ENV_MAX = "PRAXIS_RESIDENT_SKILLS_MAX"


def max_chars() -> int:
    """Потолок суммы постоянных частей. `0` — без потолка; мусор — умолчание."""
    raw = (os.environ.get(ENV_MAX) or "").strip()
    if not raw:
        return DEFAULT_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CHARS
    return value if value >= 0 else DEFAULT_MAX_CHARS


def _skill_dirs(skills_dir: Path) -> list[Path]:
    try:
        dirs = [p for p in Path(skills_dir).iterdir()
                if not p.name.startswith((".", "_")) and p.is_dir()]
    except OSError:
        return []
    return sorted(dirs, key=lambda p: (p.name.casefold(), p.name))


def entries(skills_dir: Path) -> list[dict]:
    """Все найденные постоянные части, в порядке персоны.

    Строка: slug, адрес, текст (без хвостовых пробелов), есть ли файл навыка рядом,
    материалы папки и причина отказа, если файл не прочитан."""
    skills_dir = Path(skills_dir)
    rows: list[dict] = []
    for folder in _skill_dirs(skills_dir):
        path = folder / RESIDENT_NAME
        row = {"slug": folder.name,
               "path": f"soul/skills/{folder.name}/{RESIDENT_NAME}",
               "skill_file": (skills_dir / f"{folder.name}.md").is_file(),
               "text": "", "refused": ""}
        try:
            if not path.exists() and not path.is_symlink():
                continue
            if folder.is_symlink() or path.is_symlink():
                row["refused"] = "symlink"
            elif not path.is_file():
                row["refused"] = "not_a_file"
            else:
                row["text"] = path.read_text(encoding="utf-8", errors="ignore").rstrip()
            row["materials"] = sorted(
                p.name for p in folder.iterdir()
                if p.name != RESIDENT_NAME and not p.name.startswith(".")
                and p.is_file() and not p.is_symlink())
        except OSError as exc:
            row["refused"] = type(exc).__name__
            row["text"] = ""
            row.setdefault("materials", [])
        if not row["text"] and not row["refused"]:
            continue  # пустой файл = постоянной части нет
        rows.append(row)
    return rows


def _source_line(row: dict) -> str:
    slug = row["slug"]
    if row["skill_file"]:
        return (f"[из: {row['path']} · навык целиком и материалы: "
                f"soul/skills/{slug}.md, по потребности]")
    return f"[из: {row['path']} · файла навыка soul/skills/{slug}.md нет]"


def render(skills_dir: Path, *, limit: int | None = None) -> tuple[str, list[dict]]:
    """Текст постоянных навыков для персоны и отчёт о каждом.

    Возвращает ("", rows), когда показать нечего. В отчёте у каждой строки поле
    `in_frame`: стоит ли её текст в кадре, и `chars` — его длина."""
    try:
        rows = entries(skills_dir)
    except Exception:
        return "", []
    cap = max_chars() if limit is None else max(0, int(limit))
    blocks: list[str] = []
    used = 0
    for row in rows:
        row["chars"] = len(row["text"])
        row["in_frame"] = False
        if row["refused"]:
            blocks.append(f"⚠ Постоянная часть навыка «{row['slug']}» не прочитана "
                          f"({row['refused']}): {row['path']}")
            continue
        block = row["text"] + "\n" + _source_line(row)
        if cap and used + len(block) > cap:
            blocks.append(f"⚠ Постоянная часть навыка «{row['slug']}» ({row['chars']} знаков) "
                          f"не поместилась в потолок постоянных навыков ({cap} знаков, "
                          f"{ENV_MAX}) и в кадре не показана; текст: {row['path']}")
            continue
        used += len(block)
        row["in_frame"] = True
        blocks.append(block)
    return "\n\n".join(blocks), rows


def fingerprint(skills_dir: Path) -> str:
    """Отпечаток для кэша возможностей: появление, правка и удаление постоянной части."""
    parts = []
    for folder in _skill_dirs(skills_dir):
        path = folder / RESIDENT_NAME
        try:
            st = path.stat()
        except OSError:
            continue
        parts.append(f"{folder.name}:{st.st_mtime_ns}:{st.st_size}")
    return ",".join(parts)
