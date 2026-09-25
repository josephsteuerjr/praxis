# -*- coding: utf-8 -*-
"""Расширения пользователя: свои тулы и крючки движка, переживающие обновление (25.09, K).

Случай Сергея: тул `quota` жил патчем в `tree/agent.py` и `data/extensions/runner.py`;
обновление 0.8.3 поменяло структуру, патч не лёг, тул пропал. Здесь — не патчи, а
**модули с манифестом и версионируемым API**, как у Уробороса (plugin_api: major.minor,
отпечаток поверхности): движок внутри major только добавляет; ломающая правка — новый
major, и расширение отказывается словами при загрузке, а не молча при первом вызове.

Место: `data/extensions/<имя>/` (папка владельца, обновление её не трогает):

    data/extensions/quota/
      extension.json      # манифест
      quota.py            # код: register(api) → api.register_tool(...) / api.register_hook(...)
      README.md           # зачем и как проверить

Манифест (`extension.json`):
    {"name": "quota", "version": "1.2", "api": "helene.ext/1.0",
     "requires": {"helene": ">=0.8.7"}, "entry": "quota:register",
     "capabilities": ["fs.read", "http"], "acceptance": "quota:check"}

`api` — версия API расширений: major обязан совпасть с движком, minor — минимум, который
расширению нужен (движок с большим minor подходит). Поверхность API имеет отпечаток
(`SURFACE_FINGERPRINT`): если она изменилась без поднятия версии, движок отказывается
грузить расширения вовсе — обещание версии дороже одного прогона (стенд `t_extensions`
краснеет первым).

Загрузка — раннером после `body.install` и `owner_words.install`; сбой одного расширения
не роняет остальные и не роняет ход: причина — в `helene.log`, в снимке
`memory/.state/extensions.json` (карточка «Расширения» окна) и строкой в ориентире хода.
Репетиция обновления — `check()`: те же манифесты под НОВЫМ движком без агента; отчёт
кладёт мастер до подмены папок.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import logging
import re
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("helene.extensions")

API_NAME = "helene.ext"
API_MAJOR = 1
API_MINOR = 0
API_VERSION = f"{API_MAJOR}.{API_MINOR}"

HOOKS = ("on_boot", "before_turn", "after_turn", "on_delivery")
CAPABILITIES = ("fs.read", "fs.write", "http", "shell", "journal", "agent")
# Бюджет — на ПРИБАВКУ расширения, а не на сумму схем дерева: у дерева схемы всех
# рук весят ~125 000 знаков (ревью 25.09, A6 F2), и потолок «30 000 на всё» не давал
# подключиться ни одному тулу. Один тул — до TOOL_CHARS, одно расширение — до
# EXTENSION_CHARS; считается одинаково при загрузке и на репетиции.
TOOL_CHARS = 4_000
EXTENSION_CHARS = 12_000
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # то, что принимают Anthropic/OpenAI
STATE_REL = ("memory", ".state", "extensions.json")

#: Поверхность API `helene.ext/1` — имена и сигнатуры методов PluginAPI, доступных
#: расширению. Отпечаток пересчитывается стендом; расхождение с записанным =
#: поверхность изменилась без поднятия версии.
SURFACE = ("register_tool", "register_hook", "agent", "tree", "config", "journal", "log",
           "receipt", "version")
SURFACE_FINGERPRINT = "9e11c0d2"


class ExtensionError(RuntimeError):
    """Отказ загрузки с причиной словами — для карточки, журнала и отчёта."""


@dataclass
class Extension:
    name: str
    version: str
    dir: str
    api: str = ""
    state: str = "pending"          # loaded | disabled | incompatible | error
    reason: str = ""
    tools: list = field(default_factory=list)
    hooks: dict = field(default_factory=dict)
    capabilities: list = field(default_factory=list)
    checked_at: float = 0.0
    last_error: str = ""

    def row(self) -> dict:
        return {"name": self.name, "version": self.version, "dir": self.dir, "api": self.api,
                "state": self.state, "reason": self.reason, "tools": list(self.tools),
                "hooks": dict(self.hooks), "capabilities": list(self.capabilities),
                "checked_at": self.checked_at, "last_error": self.last_error}


# ─────────────────────────────────────────────── версии

def parse_api(value: Any) -> tuple[int, int] | None:
    """'helene.ext/1.0' | 'helene.ext/1' | '1.0' → (1, 0); иначе None."""
    text = str(value or "").strip()
    if text.startswith(API_NAME + "/"):
        text = text[len(API_NAME) + 1:]
    m = re.fullmatch(r"(\d+)(?:\.(\d+))?", text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


def negotiate(manifest: dict) -> tuple[bool, str]:
    """Договориться о версии API: major равен, объявленный minor — минимум."""
    if surface_fingerprint() != SURFACE_FINGERPRINT:
        return False, (f"поверхность API {API_NAME}/{API_VERSION} изменилась без поднятия версии "
                       f"(отпечаток {surface_fingerprint()} ≠ {SURFACE_FINGERPRINT}) — "
                       "расширения не грузятся, пока версия не поднята")
    parsed = parse_api(manifest.get("api"))
    if parsed is None:
        return False, (f"в манифесте нет поля api вида \"{API_NAME}/{API_VERSION}\" "
                       f"(есть: {manifest.get('api')!r})")
    major, minor = parsed
    if major != API_MAJOR:
        return False, (f"расширение написано под {API_NAME}/{major}.{minor}, движок даёт "
                       f"{API_NAME}/{API_VERSION}: другой major — адаптировать")
    if minor > API_MINOR:
        return False, (f"расширению нужен {API_NAME}/{major}.{minor}, движок даёт "
                       f"{API_VERSION}: обновить программу")
    return True, f"{API_NAME}/{major}.{minor}"


def _vtuple(text: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(text or "")))


def requires_ok(manifest: dict, host_version: str) -> tuple[bool, str]:
    """`requires.helene`: '>=0.8.7' | '0.8.7' | '*' против версии программы."""
    want = str(((manifest.get("requires") or {}) if isinstance(manifest.get("requires"), dict)
                else {}).get("helene") or "").strip()
    if not want or want == "*":
        return True, ""
    if not host_version:
        return True, f"требует helene {want}; версия программы неизвестна — не проверено"
    m = re.fullmatch(r"(>=|==|=|>)?\s*([\d.]+)", want)
    if not m:
        return False, f"requires.helene {want!r} не разобрать (ждём вида >=0.8.7)"
    op, ver = m.group(1) or ">=", m.group(2)
    have, need = _vtuple(host_version), _vtuple(ver)
    ok = {">=": have >= need, ">": have > need, "==": have == need, "=": have == need}[op]
    return ok, ("" if ok else f"требует helene {want}, а стоит {host_version}")


def surface_fingerprint() -> str:
    parts = []
    for name in SURFACE:
        member = getattr(PluginAPI, name, None)
        try:
            sig = str(inspect.signature(member)) if callable(member) else "attr"
        except (TypeError, ValueError):
            sig = "?"
        parts.append(f"{name}{sig}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:8]


# ─────────────────────────────────────────────── манифесты

def discover(data_dir: Path) -> list[Path]:
    root = Path(data_dir) / "extensions"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "extension.json").is_file())


def read_manifest(ext_dir: Path) -> dict:
    path = Path(ext_dir) / "extension.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ExtensionError(f"extension.json не читается: {exc}") from exc
    if not isinstance(data, dict):
        raise ExtensionError("extension.json — не объект")
    name = str(data.get("name") or "").strip()
    if not NAME_RE.match(name):
        raise ExtensionError(f"имя {name!r} не годится: латиница, цифры, - и _, до 40 знаков")
    if name != Path(ext_dir).name:
        raise ExtensionError(f"имя в манифесте ({name}) не совпадает с папкой ({Path(ext_dir).name})")
    entry = str(data.get("entry") or f"{name}:register")
    if ":" not in entry:
        raise ExtensionError(f"entry {entry!r} — ждём вида module:function")
    data["entry"] = entry
    data["version"] = str(data.get("version") or "0")
    caps = data.get("capabilities") or []
    if not isinstance(caps, list) or any(c not in CAPABILITIES for c in caps):
        raise ExtensionError(f"capabilities {caps!r}: допустимы {', '.join(CAPABILITIES)}")
    return data


def _import_entry(ext_dir: Path, entry: str, *, name: str) -> Callable:
    module_name, _, func = entry.partition(":")
    path = Path(ext_dir) / (module_name.replace(".", "/") + ".py")
    if not path.is_file():
        raise ExtensionError(f"файла {path.name} нет в папке расширения")
    qualified = f"helene_ext_{name.replace('-', '_')}__{module_name.replace('.', '_')}"
    # Один модуль на одну загрузку: `entry` и `acceptance` из одного файла обязаны видеть
    # одно и то же состояние (register положил — check прочитал), а не два экземпляра.
    cached = _IMPORTED.get(qualified)
    if cached is not None:
        target = getattr(cached, func, None)
        if not callable(target):
            raise ExtensionError(f"в {path.name} нет функции {func}")
        return target
    spec = importlib.util.spec_from_file_location(qualified, path)
    if spec is None or spec.loader is None:
        raise ExtensionError(f"модуль {path.name} не грузится")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    sys.path.insert(0, str(ext_dir))
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(qualified, None)
        raise ExtensionError(f"импорт {path.name} упал: {type(exc).__name__}: {exc}") from exc
    finally:
        try:
            sys.path.remove(str(ext_dir))
        except ValueError:
            pass
    _IMPORTED[qualified] = module
    target = getattr(module, func, None)
    if not callable(target):
        raise ExtensionError(f"в {path.name} нет функции {func}")
    return target


# ─────────────────────────────────────────────── API расширению

_SECRET_KEY = re.compile(
    r"key|token|secret|password|passwd|pass\b|pwd|hash|phone|session|credential|auth|proxy|"
    r"url|dsn|\bpat\b|bearer|cookie", re.I)
# userinfo в адресах: http://user:p4ss@host — пароль уезжал бы строкой под ключом `env.HTTPS_PROXY`
_URL_USERINFO = re.compile(r"(://)([^/@\s]+)@")


def scrub(value: Any) -> Any:
    """Конфиг без секретов (ревью V1-7, 25.09): под совпавшим ключом маскируется ВСЁ поддерево
    (строка, число, список, словарь), а не только строка; словарь ключей шире (pass/pwd/auth/
    proxy/url/dsn/pat/bearer/cookie); в любой строке режется userinfo `://user:pass@`."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)) and v not in (None, "", [], {}):
                out[k] = "•••"
            else:
                out[k] = scrub(v)
        return out
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        return _URL_USERINFO.sub(r"\1•••@", value)
    return value


class PluginAPI:
    """То, что расширение получает в `register(api)`. Регистрация закрыта после возврата."""

    def __init__(self, ext: Extension, *, agent_mod=None, tree: Path | None = None,
                 cfg: dict | None = None, dry: bool = False, host_version: str = ""):
        self._ext = ext
        self._agent = agent_mod
        self._tree = Path(tree) if tree else None
        self._cfg = scrub(dict(cfg or {}))
        self._dry = dry
        self._open = True
        self._host_version = host_version
        self._chars = 0                  # схемы тулов этого расширения, знаков
        self._registered: list[str] = []  # что вставлено в агент — для отката при отказе

    # --- поверхность (SURFACE); менять сигнатуры = поднимать API_MINOR/API_MAJOR

    def register_tool(self, name: str, schema: dict, fn: Callable, *, purpose: str = "",
                      capabilities: tuple = ()) -> str:
        """Свой тул в общий список рук. -> итоговое имя (может получить namespace)."""
        self._guard()
        name = str(name or "").strip()
        if not NAME_RE.match(name):
            raise ExtensionError(f"имя тула {name!r} не годится")
        if not isinstance(schema, dict) or not isinstance(schema.get("input_schema"), dict):
            raise ExtensionError(f"схема тула {name}: ждём dict с input_schema")
        if not callable(fn):
            raise ExtensionError(f"тул {name}: fn не вызываем")
        for cap in capabilities:
            if cap not in CAPABILITIES:
                raise ExtensionError(f"тул {name}: capability {cap!r} неизвестна")
            if cap not in (self._ext.capabilities or []):
                raise ExtensionError(f"тул {name}: capability {cap!r} не объявлена в манифесте")
        final = name
        if self._agent is not None:
            taken = set(getattr(self._agent, "TOOL_IMPL", {}) or {})
            for row in getattr(self._agent, "BASE_TOOLS", []) or []:
                if isinstance(row, dict):
                    taken.add(str(row.get("name") or ""))
            if final in taken:
                # Разделитель — подчёркивание: имена с точками провайдеры отвергают
                # (^[A-Za-z0-9_-]{1,64}$), и один такой тул ронял бы каждый ход (A6 F5).
                final = f"ext_{self._ext.name}_{name}".replace("-", "_")[:64]
                if final in taken:
                    raise ExtensionError(f"имя тула {name} занято штатной рукой, и {final} тоже")
        full = dict(schema)
        full["name"] = final
        if not TOOL_NAME_RE.match(final):
            raise ExtensionError(f"имя тула {final!r} не пройдёт у провайдера (нужно ^[A-Za-z0-9_-]{{1,64}}$)")
        desc = str(full.get("description") or "").strip()
        full["description"] = (desc + f" (расширение {self._ext.name} {self._ext.version})").strip()
        size = len(json.dumps(full, ensure_ascii=False))
        if size > TOOL_CHARS:
            raise ExtensionError(f"тул {final}: схема {size} знаков, потолок {TOOL_CHARS} — тул не подключён")
        if self._chars + size > EXTENSION_CHARS:
            raise ExtensionError(
                f"тул {final}: расширение уже занимает {self._chars} знаков схем, с ним было бы "
                f"{self._chars + size} при потолке {EXTENSION_CHARS} — расширение "
                f"{self._ext.name} не подключено целиком (отказ откатывает все его тулы)")
        self._chars += size
        if self._agent is not None and not self._dry:
            self._agent.BASE_TOOLS.append(full)
            self._agent.TOOL_IMPL[final] = fn
            purposes = getattr(self._agent, "HAND_PURPOSE", None)
            if isinstance(purposes, dict):
                purposes[final] = (purpose or desc or f"тул расширения {self._ext.name}")[:120]
            self._registered.append(final)
        self._ext.tools.append(final)
        return final

    def register_hook(self, event: str, fn: Callable) -> None:
        """Крючок движка: on_boot(api) · before_turn(chat_id) · after_turn(chat_id, envelope)
        · on_delivery(chat_id, item, receipt). Сбой крючка — в журнал, ход продолжается."""
        self._guard()
        if event not in HOOKS:
            raise ExtensionError(f"крючок {event!r} неизвестен: {', '.join(HOOKS)}")
        if not callable(fn):
            raise ExtensionError(f"крючок {event}: fn не вызываем")
        self._ext.hooks[event] = getattr(fn, "__name__", "fn")
        _HOOKS.setdefault(event, []).append((self._ext.name, fn))

    def agent(self):
        """Модуль дерева агента — читать атрибуты; None на репетиции."""
        return self._agent

    def tree(self) -> Path | None:
        return self._tree

    def config(self) -> dict:
        """helene.json без секретов."""
        return dict(self._cfg)

    def journal(self, text: str) -> str:
        fn = getattr(self._agent, "tool_journal", None) if self._agent is not None else None
        if callable(fn) and not self._dry:
            return str(fn(f"[расширение {self._ext.name}] {text}"))
        return "(репетиция: дневник не пишется)"

    def log(self, text: str) -> None:
        log.info("расширение %s: %s", self._ext.name, text)

    def receipt(self, **fields) -> str:
        """Расписка тула в том же виде, что у штатных: JSON одной строкой."""
        return json.dumps({"extension": self._ext.name, **fields}, ensure_ascii=False)

    def version(self) -> dict:
        return {"api": f"{API_NAME}/{API_VERSION}", "helene": self._host_version}

    # --- служебное

    def _guard(self) -> None:
        if not self._open:
            raise ExtensionError("регистрация закрыта: register() уже вернулся")

    def close(self) -> None:
        self._open = False

    def rollback(self) -> None:
        """Отказ ПОСЛЕ register() (acceptance, поздняя ошибка): снять всё, что вставили в
        агент, — иначе расширение помечено error, а его тул живёт и зовётся (A6 F6)."""
        if self._agent is None:
            return
        names = set(self._registered)
        self._registered = []
        try:
            self._agent.BASE_TOOLS[:] = [row for row in self._agent.BASE_TOOLS
                                         if not (isinstance(row, dict) and row.get("name") in names)]
        except Exception:
            pass
        for name in names:
            getattr(self._agent, "TOOL_IMPL", {}).pop(name, None)
            purposes = getattr(self._agent, "HAND_PURPOSE", None)
            if isinstance(purposes, dict):
                purposes.pop(name, None)
        self._ext.tools = [n for n in self._ext.tools if n not in names]


# ─────────────────────────────────────────────── загрузка и крючки

_LOADED: list[Extension] = []
_HOOKS: dict[str, list] = {}
_APIS: dict[str, PluginAPI] = {}      # живой api каждого загруженного расширения (on_boot)
_STATE_PATH: Path | None = None


_IMPORTED: dict[str, Any] = {}


def _load_one(ext_dir: Path, *, agent_mod, tree, cfg, host_version: str, dry: bool) -> Extension:
    ext = Extension(name=Path(ext_dir).name, version="", dir=str(ext_dir), checked_at=time.time())
    _IMPORTED.clear()
    api: PluginAPI | None = None
    try:
        manifest = read_manifest(ext_dir)
        ext.version = manifest["version"]
        ext.capabilities = list(manifest.get("capabilities") or [])
        ok, word = negotiate(manifest)
        ext.api = str(manifest.get("api") or "")
        if not ok:
            ext.state, ext.reason = "incompatible", word
            return ext
        ok, note = requires_ok(manifest, host_version)
        if not ok:
            ext.state, ext.reason = "incompatible", note
            return ext
        register = _import_entry(ext_dir, manifest["entry"], name=ext.name)
        api = PluginAPI(ext, agent_mod=agent_mod, tree=tree, cfg=cfg, dry=dry,
                        host_version=host_version)
        try:
            register(api)
        finally:
            api.close()
        acceptance = str(manifest.get("acceptance") or "")
        if acceptance:
            check = _import_entry(ext_dir, acceptance, name=ext.name)
            verdict = check(api)
            if verdict not in (None, True, "ok", ""):
                raise ExtensionError(f"acceptance: {verdict}")
        ext.state = "loaded"
        ext.reason = note or ""
        _APIS[ext.name] = api
    except ExtensionError as exc:
        ext.state, ext.reason = ("error" if ext.state == "pending" else ext.state), str(exc)
        _forget_hooks(ext.name)
        if api is not None:
            api.rollback()
    except Exception as exc:
        ext.state = "error"
        ext.reason = f"{type(exc).__name__}: {exc}"
        ext.last_error = traceback.format_exc()[-1500:]
        _forget_hooks(ext.name)
        if api is not None:
            api.rollback()
    return ext


def _forget_hooks(name: str) -> None:
    for event, rows in list(_HOOKS.items()):
        _HOOKS[event] = [(n, fn) for n, fn in rows if n != name]


def install(agent_mod, tree: Path, cfg: dict, *, data_dir: Path | None = None,
            host_version: str = "") -> list[Extension]:
    """Загрузить расширения в живой движок. Никогда не бросает."""
    global _LOADED, _STATE_PATH
    data_dir = Path(data_dir or tree)
    _STATE_PATH = Path(tree).joinpath(*STATE_REL)
    _LOADED = []
    _HOOKS.clear()
    _APIS.clear()
    for ext_dir in discover(data_dir):
        ext = _load_one(ext_dir, agent_mod=agent_mod, tree=tree, cfg=cfg,
                        host_version=host_version, dry=False)
        _LOADED.append(ext)
        if ext.state == "loaded":
            log.info("расширение %s %s: загружено (тулы: %s; крючки: %s)", ext.name, ext.version,
                     ", ".join(ext.tools) or "—", ", ".join(ext.hooks) or "—")
        else:
            log.warning("расширение %s не загружено (%s): %s", ext.name, ext.state, ext.reason)
    write_state()
    run_hook("on_boot")
    return list(_LOADED)


def loaded() -> list[Extension]:
    return list(_LOADED)


def run_hook(event: str, *args, **kwargs) -> list[tuple[str, bool, str]]:
    """Позвать крючки события. Сбой — в журнал и в снимок; ход не рвётся."""
    out = []
    for name, fn in list(_HOOKS.get(event, ())):
        try:
            if event == "on_boot":
                fn(_api_for(name))
            else:
                fn(*args, **kwargs)
            out.append((name, True, ""))
        except Exception as exc:
            note = f"{event}: {type(exc).__name__}: {exc}"
            log.warning("крючок расширения %s упал — %s", name, note)
            for ext in _LOADED:
                if ext.name == name:
                    ext.last_error = note
            out.append((name, False, note))
    if any(not ok for _n, ok, _w in out):
        write_state()
    return out


def _api_for(name: str) -> PluginAPI | None:
    """Живой api расширения (агент, дерево, конфиг) с закрытой регистрацией — для on_boot
    (A6 F8: «сухой» api без дерева ронял крючок, читающий свой файл)."""
    return _APIS.get(name)


def write_state() -> None:
    if _STATE_PATH is None:
        return
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"api": f"{API_NAME}/{API_VERSION}", "updated_at": time.time(),
                   "items": [ext.row() for ext in _LOADED]}
        tmp = _STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_STATE_PATH)
    except Exception:
        log.debug("снимок расширений не записался", exc_info=True)


def state_line() -> str:
    """Строка в ориентир хода: что подключено и что — нет и почему."""
    if not _LOADED:
        return ""
    parts = []
    for ext in _LOADED:
        if ext.state == "loaded":
            parts.append(f"{ext.name} {ext.version} — подключено"
                         + (f" (тулы: {', '.join(ext.tools)})" if ext.tools else ""))
        else:
            parts.append(f"{ext.name} — не загружено: {ext.reason}")
    return "Расширения владельца: " + "; ".join(parts) + "."


# ─────────────────────────────────────────────── репетиция обновления

def check(data_dir: Path, *, host_version: str = "") -> dict:
    """Те же манифесты под ЭТИМ движком, без агента: что загрузится, что нет и почему.

    Зовётся мастером обновления из НОВОЙ поставки до подмены папок и раннером по
    `--check-extensions`. Крючки и тулы регистрируются вхолостую (dry): проверяются
    версия API, requires, импорт кода и сама регистрация; штатные имена рук на репетиции
    неизвестны — конфликт имён покажет уже загрузка.
    """
    global _LOADED
    saved, saved_hooks = list(_LOADED), dict(_HOOKS)
    _LOADED, items = [], []
    _HOOKS.clear()
    # 25.09 (ревью V3 F4): репетиция гоняла код без `tree()` и `config()` — совместимое по
    # документации расширение (`api.tree() / "memory"`, `api.config()["telegram"]`) получало
    # отказ в обновлении. Папка данных известна (data_dir), конфиг лежит рядом с ней;
    # scrub — внутри PluginAPI. Агента на репетиции по-прежнему нет.
    data_dir = Path(data_dir)
    cfg: dict = {}
    try:
        raw = json.loads((data_dir.parent / "helene.json").read_text(encoding="utf-8"))
        cfg = raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        cfg = {}
    try:
        for ext_dir in discover(data_dir):
            ext = _load_one(ext_dir, agent_mod=None, tree=data_dir, cfg=cfg, host_version=host_version,
                            dry=True)
            items.append(ext.row())
    finally:
        _LOADED = saved
        _HOOKS.clear()
        _HOOKS.update(saved_hooks)
    bad = [row for row in items if row["state"] != "loaded"]
    return {"api": f"{API_NAME}/{API_VERSION}", "helene": host_version, "checked_at": time.time(),
            "items": items, "ok": not bad,
            "summary": (f"все {len(items)} расширений грузятся под {API_NAME}/{API_VERSION}"
                        if items and not bad else
                        "расширений нет" if not items else
                        "не грузятся: " + "; ".join(f"{r['name']} — {r['reason']}" for r in bad))}
