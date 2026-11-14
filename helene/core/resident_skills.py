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
import frame_language

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


def entries(skills_dir: Path, *, include_kit: bool = True) -> list[dict]:
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
    # An exact shipped resident is product text even after first birth copied
    # it into the home. Select its translated edition in memory only. A changed
    # or deliberately empty personal resident remains authoritative.
    if include_kit:
        root = os.environ.get('HELENE_SKILLS_KIT_ROOT', '')
        own = next((row for row in rows if row['slug'] == 'living-prose'), None)
        if root and own and own['text'] and not own['refused']:
            shipped = []
            for lang in frame_language.LANGUAGES:
                candidate = Path(root) / ('soul' if lang == 'ru' else f'soul.{lang}') / 'skills/living-prose/resident.md'
                try:
                    shipped.append(candidate.read_text(encoding='utf-8').rstrip())
                except (OSError, UnicodeError):
                    continue
            if own['text'] in shipped:
                translated = _kit_entry(root, frame_language.language())
                if translated:
                    translated['home_path'] = own['path']
                    rows[rows.index(own)] = translated
    # The optional product feature works on upgrades without installing text
    # into an existing authored soul. Any personal file/folder takes priority,
    # including an intentionally empty resident file. Only this stock feature
    # can fall back to the packaged kit, never arbitrary private skills.
    if include_kit and not (skills_dir / 'living-prose.md').exists() and not (skills_dir / 'living-prose').exists():
        root = os.environ.get('HELENE_SKILLS_KIT_ROOT', '')
        if root:
            translated = _kit_entry(root, frame_language.language())
            if translated:
                rows.append(translated)
    rows.sort(key=lambda row: (row['slug'].casefold(), row['slug']))
    return rows


def _kit_entry(root: str, language: str) -> dict | None:
    selected = Path(root) / ('soul' if language == 'ru' else f'soul.{language}') / 'skills'
    for row in entries(selected, include_kit=False):
        if row['slug'] == 'living-prose':
            row['source'] = 'kit'
            row['path'] = f"resources/{selected.parent.name}/skills/living-prose/resident.md"
            return row
    return None


def _source_line(row: dict) -> str:
    slug = row["slug"]
    if row.get('source') == 'kit':
        return frame_language.text('resident.kit_source', path=row['path'])
    if row["skill_file"]:
        return frame_language.text('resident.source', path=row['path'], slug=slug)
    return frame_language.text('resident.no_skill', path=row['path'], slug=slug)


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
        if row['slug'] == 'living-prose' and not frame_language.living_prose_enabled():
            row['disabled'] = True
            continue
        if row["refused"]:
            blocks.append(frame_language.text('resident.unreadable', slug=row['slug'],
                          reason=row['refused'], path=row['path']))
            continue
        block = row["text"] + "\n" + _source_line(row)
        if cap and used + len(block) > cap:
            blocks.append(frame_language.text('resident.over_cap', slug=row['slug'],
                          chars=row['chars'], cap=cap, env=ENV_MAX, path=row['path']))
            continue
        used += len(block)
        row["in_frame"] = True
        blocks.append(block)
    return "\n\n".join(blocks), rows


def fingerprint(skills_dir: Path) -> str:
    """Отпечаток для кэша возможностей: появление, правка и удаление постоянной части."""
    parts = [frame_language.fingerprint()]
    for folder in _skill_dirs(skills_dir):
        path = folder / RESIDENT_NAME
        try:
            st = path.stat()
        except OSError:
            continue
        parts.append(f"{folder.name}:{st.st_mtime_ns}:{st.st_size}")
        try:
            parts.append(str(folder.stat().st_mtime_ns))
        except OSError:
            pass
    root = os.environ.get('HELENE_SKILLS_KIT_ROOT', '')
    if root:
        # Every stock edition participates in recognizing an untouched home
        # copy; a change to any edition can alter that decision.
        for lang in frame_language.LANGUAGES:
            path = Path(root) / ('soul' if lang == 'ru' else f'soul.{lang}') / 'skills/living-prose/resident.md'
            try:
                st = path.stat()
                parts.append(f'kit:{path}:{st.st_mtime_ns}:{st.st_size}:{path.parent.stat().st_mtime_ns}')
            except OSError:
                parts.append(f'kit-missing:{path}')
    return ",".join(parts)
