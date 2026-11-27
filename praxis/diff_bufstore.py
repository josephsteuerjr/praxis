# -*- coding: utf-8 -*-
"""Дифференциальный прогон: bufstore_OLD (checkpoint 88098e7c) vs bufstore_NEW.

Обе версии загружаются в один процесс, каждая прогоняется на идентичных
сценариях в собственном свежем каталоге. Сравниваются:
  - байты буферного файла и мета-файла после сценария;
  - возвращаемые значения функций;
  - факт переписывания файла при идентичном payload (mtime).

Расхождение — не приговор, а карта миграции: каждое объясняется или
чинится. Выход: HTML-совместимый отчёт в stdout + счётчики.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


OLD_PATH = Path("/tmp/bufstore_old.py")
if not OLD_PATH.exists():
    # воспроизводимость: OLD материализуется из git-чекпоинта, не из воздуха
    import subprocess
    OLD_PATH.write_text(subprocess.run(
        ["git", "show", "88098e7c:bufstore.py"], capture_output=True, text=True,
        check=True, cwd=Path(__file__).resolve().parent).stdout, encoding="utf-8")

OLD = load_module("bufstore_old", OLD_PATH)
NEW = load_module("bufstore_new", Path(__file__).resolve().parent / "bufstore.py")

import step_control  # noqa: E402
step_control.suppresses_replay = lambda cid, ts, base=None: False

T0 = 1_800_000_000.0  # фиксированная точка отсчёта
diffs: list[str] = []
checks = 0


def fresh(mod, root: Path):
    mod.BASE = root
    mod.BUF_DIR = root / "buffers"
    mod.STATE_DIR = root / "state"
    mod.META_PATH = root / "state" / "buf_meta.json"


def snap(mod) -> dict:
    out = {"buffers": {}, "meta": None}
    if mod.BUF_DIR.exists():
        for f in sorted(mod.BUF_DIR.glob("*.json")):
            out["buffers"][f.name] = f.read_text(encoding="utf-8")
    if mod.META_PATH.exists():
        out["meta"] = mod.META_PATH.read_text(encoding="utf-8")
    return out


def run_both(label, scenario):
    """scenario(mod) — выполняется на старой и новой версии в свежих корнях."""
    global checks
    checks += 1
    results = {}
    for tag, mod in (("old", OLD), ("new", NEW)):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fresh(mod, root)
            scenario(mod)
            results[tag] = snap(mod)
    if results["old"] != results["new"]:
        diffs.append((label, results["old"], results["new"]))


def cmp_values(label, fn_old, fn_new):
    global checks
    checks += 1
    a, b = fn_old(OLD), fn_new(NEW)
    if a != b:
        diffs.append((label, a, b))


# --- 1. save/load: содержимое и байты ------------------------------------- #
CASES = [
    ("ascii", ["Anna: hi", "Praxis: hello"]),
    ("unicode", ["Йож: привет 🐈‍⬛", "Praxis: здравствуй"]),
    ("coerce", [42, None, 3.14, True]),
    ("empty", []),
    ("nested-str", ["a: x", "a: x"]),  # дубль строки — валидная история
    ("quotes", ['X: сказал "привет" и \\ ушёл']),
]
for label, lines in CASES:
    def sc(mod, lines=lines):
        mod.save("chat1", lines)
    run_both(f"save::{label}", sc)
    cmp_values(f"load::{label}", lambda m, lines=lines: m.load("chat1"),
               lambda m, lines=lines: m.load("chat1"))


def sc_special_ids(mod):
    mod.save("a b/c", ["x"])
    mod.save("", ["empty-key"])
    mod.save(-1003701205730, ["grp"])
    mod.save(809306689, ["dm"])
run_both("save::special-ids", sc_special_ids)
cmp_values("load_all::special-ids", lambda m: m.load_all(), lambda m: m.load_all())

# --- 2. идентичная запись не трогает файл ---------------------------------- #
def sc_identical(mod):
    mod.save("c", ["a: 1"])
    p = mod.path_for("c")
    os.utime(p, (T0, T0))
    mod.save("c", ["a: 1"])  # байт-в-байт то же
    global mtime_after
    mtime_after = p.stat().st_mtime
run_both("save::identical-keep", sc_identical)

def mtime_probe(mod):
    mod.save("c", ["a: 1"])
    p = mod.path_for("c")
    os.utime(p, (T0, T0))
    mod.save("c", ["a: 1"])
    return p.stat().st_mtime
cmp_values("mtime::identical-not-rewritten",
           lambda m: mtime_probe(m) == T0, lambda m: mtime_probe(m) == T0)

def mtime_probe2(mod):
    mod.save("c", ["a: 1"])
    p = mod.path_for("c")
    os.utime(p, (T0, T0))
    mod.save("c", ["a: 2"])
    return p.stat().st_mtime > T0
cmp_values("mtime::changed-rewritten",
           lambda m: mtime_probe2(m), lambda m: mtime_probe2(m))

# --- 3. битые файлы --------------------------------------------------------- #
def sc_broken(mod):
    mod.BUF_DIR.mkdir(parents=True, exist_ok=True)
    (mod.BUF_DIR / "broken.json").write_text("[1,", encoding="utf-8")
    (mod.BUF_DIR / "dict.json").write_text('{"a":1}', encoding="utf-8")
    (mod.BUF_DIR / "str.json").write_text('"hello"', encoding="utf-8")
    (mod.BUF_DIR / "ok.json").write_text('["x: 1"]', encoding="utf-8")
run_both("broken::files", sc_broken)
cmp_values("load::broken", lambda m: m.load("broken"), lambda m: m.load("broken"))
cmp_values("load::dict", lambda m: m.load("dict"), lambda m: m.load("dict"))
cmp_values("load_all::mixed", lambda m: m.load_all(), lambda m: m.load_all())
cmp_values("load::missing", lambda m: m.load("nope"), lambda m: m.load("nope"))
cmp_values("load_all::no-dir", lambda m: m.load_all(), lambda m: m.load_all())

# --- 4. мета ---------------------------------------------------------------- #
META_SEQS = [
    ("fresh-dm-by-sign", [("u1", dict(author="Ann", ts=T0))]),
    ("fresh-group-by-sign", [("g1", dict(author="GR", ts=T0))]),
    ("explicit-then-omit", [("u1", dict(author="a", is_dm=True, name="Ann", ts=T0)),
                            ("u1", dict(author="b", ts=T0 + 10))]),
    ("monotonic-regression", [("u1", dict(author="late", ts=T0 + 100)),
                              ("u1", dict(author="early", ts=T0))]),
    ("equal-ts-wins-new", [("u1", dict(author="first", ts=T0)),
                           ("u1", dict(author="second", ts=T0))]),
    ("dm-flip", [("u1", dict(author="a", is_dm=False, ts=T0)),
                 ("u1", dict(author="b", is_dm=True, ts=T0 + 1))]),
    ("negative-id-explicit-dm", [("grp", dict(author="g", is_dm=True, ts=T0))]),
    ("positive-id-explicit-group", [("dm", dict(author="d", is_dm=False, ts=T0))]),
    ("empty-author", [("u1", dict(author="", ts=T0))]),
    # [1] ревьюера: пустой автор СТИРАЕТ прежнего (старая семантика)
    ("author-clear-after-praxis", [("u1", dict(author="praxis", ts=T0)),
                                   ("u1", dict(author="", ts=T0 + 10))]),
    # [2] ревьюера: приведения типов name/is_dm
    ("name-int-coerce", [("u1", dict(author="a", name=123, ts=T0))]),
    ("isdm-truthy-coerce", [("u1", dict(author="a", is_dm=1, ts=T0))]),
    ("sanitized-key", [("a b/c", dict(author="x", ts=T0))]),
]
for label, seq in META_SEQS:
    def sc(mod, seq=seq):
        for cid, kw in seq:
            mod.meta_update(cid, **kw)
    run_both(f"meta::{label}", sc)

def sc_meta_broken(mod):
    mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    mod.META_PATH.write_text("~~not json~~", encoding="utf-8")
run_both("meta::broken-load", sc_meta_broken)
cmp_values("meta_load::broken", lambda m: m.meta_load(), lambda m: m.meta_load())
cmp_values("meta_load::missing", lambda m: m.meta_load(), lambda m: m.meta_load())

# --- 5. missed_dm_candidates: матрица --------------------------------------- #
NOW = T0 + 3600  # час после базовой точки


def seed(mod, cid, lines, author="Human", is_dm=None, ts=T0, name=None,
         write_meta=True):
    mod.save(cid, lines)
    if write_meta:
        kw = dict(author=author, ts=ts)
        if is_dm is not None:
            kw["is_dm"] = is_dm
        if name is not None:
            kw["name"] = name
        mod.meta_update(cid, **kw)


DM_CASES = [
    ("fresh-human", dict(cid="809306689", lines=["Yegor: q?"], author="Yegor",
                         is_dm=True, ts=T0, name="Yegor"), 1),
    ("answered-in-file", dict(cid="u1", lines=["Y: q", "Praxis: a"], author="Praxis",
                              is_dm=True, ts=T0), 0),
    ("answered-in-meta-only", dict(cid="u2", lines=["Y: q"], author="praxis",
                                   is_dm=True, ts=T0), 0),
    ("author-casefold", dict(cid="u3", lines=["Y: q"], author="  PRAXIS ",
                             is_dm=True, ts=T0), 0),
    ("stale-72h", dict(cid="u4", lines=["Y: q"], author="Y", is_dm=True,
                       ts=T0 - 72 * 3600), 0),
    ("boundary-48h", dict(cid="u5", lines=["Y: q"], author="Y", is_dm=True,
                          ts=T0 - 48 * 3600), 0),
    ("just-under-48h", dict(cid="u6", lines=["Y: q"], author="Y", is_dm=True,
                            ts=T0 - 47 * 3600), 1),
    ("group-by-sign", dict(cid="-100123", lines=["GR: эй"], author="GR"), 0),
    ("group-meta-nodm", dict(cid="-100999", lines=["GR: эй"], author="GR",
                             is_dm=False, ts=T0), 0),
    ("negid-meta-dm", dict(cid="-100777", lines=["M: q"], author="M",
                           is_dm=True, ts=T0, name="M"), 1),
    ("no-meta-posid", dict(cid="5e5", lines=["X: q"], write_meta=False), 1),
    ("no-meta-negid", dict(cid="-5e5", lines=["X: q"], write_meta=False), 0),
    ("empty-buffer", dict(cid="u7", lines=[], author="X", is_dm=True, ts=T0), 0),
    ("meta-no-ts", dict(cid="u8", lines=["Y: q"], author="Y", is_dm=True,
                        ts=None), None),  # ts None → time.time() → свежо → 1
]


def cand_probe(mod, case, now=NOW, window=48.0):
    fresh(mod, Path(tempfile.mkdtemp()))
    kw = dict(case)
    cid = kw.pop("cid")
    lines = kw.pop("lines")
    kw.pop("write_meta", None)
    seed(mod, cid, lines, **kw)
    if not case.get("write_meta", True):
        # стереть мета-файл целиком: сценарий "только файл"
        if mod.META_PATH.exists():
            mod.META_PATH.unlink()
    return mod.missed_dm_candidates(max_age_hours=window, now=now)


for label, case, expected_count in DM_CASES:
    cmp_values(f"cand::{label}",
               lambda m, c=case: cand_probe(m, c),
               lambda m, c=case: cand_probe(m, c))

# мета ts=0 → фолбэк на mtime файла
def cand_mtime_fallback(mod):
    fresh(mod, Path(tempfile.mkdtemp()))
    mod.save("u9", ["Y: q"])
    p = mod.path_for("u9")
    os.utime(p, (T0, T0))          # mtime = T0
    mod.meta_update("u9", author="Y", is_dm=True, ts=0.0)  # ts=0 не блокирует
    # старая: cur.last_ts=0 > 0? нет (>) → запишется 0.0 → фолбэк mtime
    return mod.missed_dm_candidates(max_age_hours=48.0, now=NOW)
cmp_values("cand::meta-ts-zero-mtime-fallback",
           lambda m: cand_mtime_fallback(m), lambda m: cand_mtime_fallback(m))

# [5] ревьюера: нечисловой last_ts в мете (порча/legacy). Решение автора:
# признать нормализацию намеренной миграцией НОВОЙ версии, но НЕ молчаливой —
# оба новых поведения (запись вместо исключения; кандидат по mtime вместо
# исключения чата) фиксируются тестами в test_bufstore.py как карта миграции.
def sc_garbage_meta(mod):
    mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    mod.META_PATH.write_text(json.dumps(
        {"u1": {"last_ts": "garbage", "last_author": "Y", "is_dm": True}}),
        encoding="utf-8")
    try:
        mod.meta_update("u1", author="Z", ts=T0)
    except Exception:
        pass  # старая версия кидала ValueError наружу; новая не должна
def cand_garbage_ts(mod):
    fresh(mod, Path(tempfile.mkdtemp()))
    mod.save("u1", ["Y: q"])
    p = mod.path_for("u1")
    os.utime(p, (T0, T0))
    mod.STATE_DIR.mkdir(parents=True, exist_ok=True)
    mod.META_PATH.write_text(json.dumps(
        {"u1": {"last_ts": "garbage", "last_author": "Y", "is_dm": True}}),
        encoding="utf-8")
    return mod.missed_dm_candidates(max_age_hours=48.0, now=NOW)
sc_garbage_meta(OLD); sc_garbage_meta(NEW)  # side-effect smoke, не сравнение
print("migration::garbage-ts:", json.dumps(cand_garbage_ts(NEW), default=str)[:200])

# подавление: обе версии должны вызвать suppresses_replay и учесть False/True
for verdict in (False, True):
    def probe(mod, v=verdict):
        fresh(mod, Path(tempfile.mkdtemp()))
        step_control.suppresses_replay = lambda cid, ts, base=None: v
        mod.save("u10", ["Y: q"])
        mod.meta_update("u10", author="Y", is_dm=True, ts=T0)
        r = mod.missed_dm_candidates(max_age_hours=48.0, now=NOW)
        step_control.suppresses_replay = lambda cid, ts, base=None: False
        return r
    cmp_values(f"cand::suppress={verdict}",
               lambda m: probe(m), lambda m: probe(m))

# --- итог ------------------------------------------------------------------- #
print(f"checks={checks} diffs={len(diffs)}")
for label, a, b in diffs:
    print(f"\nDIFF {label}:\n  old={json.dumps(a, ensure_ascii=False, default=str)[:600]}\n  new={json.dumps(b, ensure_ascii=False, default=str)[:600]}")
sys.exit(1 if diffs else 0)
