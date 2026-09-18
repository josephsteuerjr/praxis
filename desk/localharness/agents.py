# -*- coding: utf-8 -*-
"""Кто живёт в этой установке: список агентов и правила его чтения.

До 11.09 правило было одно и незаписанное: установка = папка = агент. Оно жило
россыпью — оболочка брала `helene.json` рядом с exe, служба брала его же через
`--config`, раннер резолвил пути от папки конфига. Нигде не было слова «агент
здесь один», поэтому и не было места, куда добавить второго.

Теперь правило названо и записано ЗДЕСЬ, а Rust повторяет его в
`common/agents.rs` (сверка — `tests/roster-cases.json`, его читают оба стенда):

    <установка>/helene.json              — первый агент, id `main`
    <установка>/agents/<id>/helene.json  — каждый следующий, id = имя папки

⚠ Почему у второго агента ПОЛНЫЙ конфиг, а не «наследование от корневого».
Наследование заставляет держать две базы для относительных путей: `runtime/`
и `app/` считались бы от установки, а `data` — от папки агента. Один такой
разъезд уже стоил нам дня (`layout.py`): относительный путь, посчитанный не от
той папки, не падает, а МОЛЧА указывает в пустоту. Здесь база одна и та же для
всех путей внутри файла — папка самого файла, — а `../../runtime/python.exe`
в конфиге второго агента пишет не человек, а `create()`.

⚠ Порт у каждого агента свой, и это не косметика: порт — это труба окна, и на
неё же смотрит замок «чужая установка» в оболочке. Два агента на одном порту
означали бы, что окно второго показывает переписку первого. Поэтому столкновение
портов здесь не «поправится само», а называется словами и снимает агента с
подъёма (`conflict`), оставляя его в списке видимым.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# Константы продукта — как везде в дереве: объявлены здесь, сверены с
# `ui-kit/contract.json` стендом `tests/t_contract.py`. Читать контракт в
# рантайме нельзя: в поставке `ui-kit/` нет, там живёт только `app/`.
ROSTER_DIR = "agents"               # как в ui-kit/contract.json (tests/t_contract.py)
BASE_ID = "main"                    # id корневого агента — там же
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
DESK_PORT = 8094                    # порт канала по умолчанию — там же
BODY_PORT = 9480                    # порт моста тела — там же
CONFIG_NAME = "helene.json"         # имя файла настроек — там же
COMPUTER_SCOPES: tuple[str, ...] = ("computer.read", "computer.files",
                                    "computer.process", "computer.apps")
#: Питон поставки, когда в корневом конфиге ключа `python` нет. Правило одно на
#: две головы (вторая — оболочка, `shell/src/main.rs`): Windows —
#: `runtime/python.exe` (embedded CPython), иначе — `runtime/bin/python3`
#: (python-build-standalone). Явный `python` в конфиге сильнее умолчания;
#: относительный путь считается от папки конфига.
DEFAULT_PYTHON = "runtime/python.exe" if os.name == "nt" else "runtime/bin/python3"


@dataclass
class Agent:
    """Один агент установки. `dir` — папка его конфига, `tree` — папка данных."""

    id: str
    name: str
    dir: Path
    config: Path
    tree: Path
    port: int
    enabled: bool = True
    base: bool = False
    #: Порт занят другим агентом этой же установки — поднимать нельзя, сказать надо.
    conflict: str = ""

    def as_dict(self) -> dict:
        """Снимок для окна и стендов. Пути — строками, чтобы JSON был плоским."""
        return {
            "id": self.id,
            "name": self.name,
            "dir": str(self.dir),
            "config": str(self.config),
            "tree": str(self.tree),
            "port": self.port,
            "enabled": self.enabled,
            "base": self.base,
            "conflict": self.conflict,
        }


def read_config(path: Path) -> dict:
    """Конфиг агента; нечитаемый или не-объект — пусто, а не исключение.

    Список агентов обязан строиться и при битом файле у одного из них: иначе
    один лишний запятой в чужом конфиге прятал бы из окна ВСЕХ.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    for encoding in ("utf-8-sig", "utf-16", "cp1251"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
        try:
            got = json.loads(text)
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def agent_name(cfg: dict, fallback: str = "Агент") -> str:
    """Имя агента: как у оболочки — `agent.name`, потом старое место, потом честное."""
    for got in ((cfg.get("agent") or {}).get("name"),
                (cfg.get("telegram") or {}).get("agent_name")):
        if isinstance(got, str) and got.strip():
            return got.strip()
    return fallback


def _resolve(base: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else (base / path)


def _one(dir_: Path, agent_id: str, *, base: bool, index: int) -> Agent:
    """Собрать агента из его папки. Порт по умолчанию — по месту в списке."""
    config = dir_ / CONFIG_NAME
    cfg = read_config(config)
    port = cfg.get("port")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        port = DESK_PORT + index
    tree_raw = cfg.get("tree")
    tree = _resolve(dir_, tree_raw if isinstance(tree_raw, str) and tree_raw.strip() else "data")
    enabled = cfg.get("enabled")
    return Agent(
        id=agent_id,
        name=agent_name(cfg, "Агент" if base else agent_id),
        dir=dir_,
        config=config,
        tree=tree,
        port=int(port),
        enabled=True if enabled is None else bool(enabled),
        base=base,
    )


def roster(base_dir: Path) -> list[Agent]:
    """Все агенты установки: первый — корневой, дальше `agents/*` по алфавиту.

    Корневой агент есть ВСЕГДА, даже когда `helene.json` рядом с exe ещё не
    написан: до 11.09 установка без конфига открывала установщик, и список,
    который в этот момент пуст, оставил бы окно без единой строки. Пусто в
    списке и «агента нет» — разные вещи, и путать их незачем.
    """
    base_dir = Path(base_dir)
    out = [_one(base_dir, BASE_ID, base=True, index=0)]
    seen_ports: dict[int, str] = {out[0].port: out[0].id}
    home = base_dir / ROSTER_DIR
    kids = []
    try:
        kids = sorted((p for p in home.iterdir() if p.is_dir()), key=lambda p: p.name.lower())
    except OSError:
        kids = []
    index = 0
    for kid in kids:
        if not (kid / CONFIG_NAME).is_file():
            continue                      # папка без конфига — не агент, а мусор
        if not ID_PATTERN.match(kid.name):
            continue                      # имя не по правилу — в адрес его не пустим
        if kid.name == BASE_ID:
            continue                      # `agents/main` спорил бы с корневым за id
        index += 1
        got = _one(kid, kid.name, base=False, index=index)
        held = seen_ports.get(got.port)
        if held:
            got.conflict = held
        else:
            seen_ports[got.port] = got.id
        out.append(got)
    return out


def find(base_dir: Path, agent_id: str) -> Agent | None:
    """Агент по id; неизвестный — None (а не корневой втихую)."""
    wanted = (agent_id or "").strip().lower()
    if not wanted:
        return None
    for got in roster(base_dir):
        if got.id == wanted:
            return got
    return None


def raisable(base_dir: Path) -> list[Agent]:
    """Кого поднимать: включённые и без спора за порт."""
    return [a for a in roster(base_dir) if a.enabled and not a.conflict]


def slug(name: str, taken: set[str] | None = None) -> str:
    """Имя владельца → id папки. Кириллица переводится, а не выбрасывается.

    ⚠ Без транслитерации «Мира» давала пустой slug, и второй агент назывался бы
    `agent-2` при живом имени. Имя папки владелец увидит в проводнике — оно
    обязано быть узнаваемым.
    """
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    low = unicodedata.normalize("NFC", (name or "").strip().lower())
    out = "".join(table.get(ch, ch) for ch in low)
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")[:32]
    out = out or "agent"
    if not out[0].isalnum():
        out = "a" + out
    if taken:
        stem, n = out, 2
        while out in taken:
            out = f"{stem[:29]}-{n}"
            n += 1
    return out


def free_port(taken: set[int], wanted: int) -> int:
    """Первый свободный порт от желаемого вверх. Только внутри установки."""
    port = max(1024, min(int(wanted), 65000))
    while port in taken:
        port += 1
    return port


#: Ключи, которые новый агент НЕ наследует от корневого: они либо про саму
#: установку (обновление, служба, телефон), либо про то, чем агенты обязаны
#: различаться (имя, дом, порт, бот, тело). Всё остальное — мозг, ограда,
#: монтирования, ручки среды — копируется: владелец уже настроил это один раз.
_NOT_INHERITED = {
    "agent", "tree", "port", "telegram", "enabled", "setup_complete",
    "service", "update", "phone", "computer", "relay", "owner", "notifications",
}


def create(base_dir: Path, name: str, *, brain_from_base: bool = True) -> Agent:
    """Завести нового агента: папка, конфиг, свободный порт. Ничего не поднимает.

    Дом (`data/`) не создаём и не засеваем: это делает раннер при первом старте
    (`boot.ensure_layout`), и второй реализации того же засева здесь не будет.
    """
    base_dir = Path(base_dir)
    others = roster(base_dir)
    agent_id = slug(name, {a.id for a in others} | {BASE_ID})
    dir_ = base_dir / ROSTER_DIR / agent_id
    dir_.mkdir(parents=True, exist_ok=True)
    root = read_config(base_dir / CONFIG_NAME) if brain_from_base else {}
    cfg: dict = {k: v for k, v in root.items() if k not in _NOT_INHERITED}
    # Пути установки — от папки агента: два уровня вверх (`agents/<id>/`).
    for key, default in (("python", DEFAULT_PYTHON), ("app", "app/deskapp.py"),
                         ("runner", "app/localharness/runner.py"), ("code", "tree")):
        raw = str(root.get(key) or default)
        cfg[key] = raw if Path(raw).is_absolute() else f"../../{raw}"
    cfg["mode"] = str(root.get("mode") or "local")
    named = (name or "").strip() or agent_id
    cfg["agent"] = {"name": named}
    # Владелец тот же, а вот КОМНАТА окна зовётся именем своего агента: она и
    # есть «разговор с ним». Унаследованная от корневого, она подписывала бы
    # переписку с новым агентом чужим именем — поймано живой пробой окна 11.09.
    cfg["owner"] = {**dict(root.get("owner") or {}), "room": named}
    cfg["tree"] = "data"
    cfg["port"] = free_port({a.port for a in others}, DESK_PORT + len(others))
    cfg["telegram"] = {"bot_token": "", "owner_id": int((root.get("telegram") or {}).get("owner_id") or 0)}
    # Тело — своя дверь у каждого: один порт моста на двоих означал бы, что
    # рукой `computer` второго агента водит тело первого.
    body_ports = set()
    for other in others:
        got = read_config(other.config).get("computer") or {}
        if isinstance(got, dict) and isinstance(got.get("port"), int):
            body_ports.add(int(got["port"]))
    cfg["computer"] = {"enabled": False,
                       "port": free_port(body_ports, BODY_PORT + len(others)),
                       "scopes": list((root.get("computer") or {}).get("scopes")
                                      or COMPUTER_SCOPES)}
    # Своего реле второй агент не поднимает: подписка одна, порт один, и
    # поднятое дважды реле дерётся за него само с собой.
    cfg["relay"] = {"enabled": False}
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    (dir_ / CONFIG_NAME).write_text(text, encoding="utf-8", newline="\n")
    got = find(base_dir, agent_id)
    assert got is not None                # только что записали — не найтись не может
    return got
