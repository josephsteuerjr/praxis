# -*- coding: utf-8 -*-
"""Голос на Windows: локальный whisper на процессоре — и агент слышит.

До 0.5.2 расшифровки на Windows не было вовсе. На сервере она есть с 0.3.x
(та же коробка, что у Праксис: `faster-whisper`, модель на диске, всё
переменными `PRAXIS_STT_*`), а в поставке под Windows — ни библиотеки, ни
модели, и голосовое в Telegram или в окне превращалось в молчание.

Что здесь есть:

  * **Библиотека едет в рантайме.** `faster-whisper` (и ctranslate2, av,
    onnxruntime, numpy) ставятся в `runtime/` при сборке — поставка тяжелеет
    примерно на 170 МБ, зато голос работает без интернета и без чужого питона.
  * **Модель не едет и ехать не может.** Самая маленькая — 460 МБ, рабочая —
    1,6 ГБ; это не то, что кладут в архив продукта. Владелец выбирает модель в
    окне, и она скачивается один раз в `data/models/whisper` (`--get` ниже).
  * **Тому, что модели нет, положено быть видимым.** `state()` отвечает всей
    правдой: есть ли библиотека, скачана ли модель, сколько весит, и почему
    голос молчит, если молчит. Умолчание — fail-closed: не знаем — значит нет.

Что читает дерево (`live/media_audio.py`) и что мы ему ставим:

    PRAXIS_AUDIO_MODEL_DIR   <дерево>/models          (whisper — в подпапке)
    PRAXIS_STT_MODEL         путь к скачанной модели   (не имя: путь однозначен)
    PRAXIS_STT_LOCAL_FILES_ONLY=1                      скачивать на ходу нельзя
    PRAXIS_STT_LANGUAGE / COMPUTE_TYPE / CPU_THREADS / BEAM_SIZE / KEEP_LOADED

Ручки владельца — блок `voice` в `helene.json`:

    {"voice": {"enabled": true, "model": "turbo", "language": "ru",
               "threads": 4, "keep_loaded": false}}

⚠ `keep_loaded` по умолчанию ВЫКЛЮЧЕН, в отличие от сервера. На сервере модель
держат в памяти, чтобы не платить секундами на каждое голосовое; на домашней
машине это 1,5–2 ГБ занятой памяти всё время, пока агент жив, — за то, чем
пользуются несколько раз в день.

Живые числа (машина владельца, 10.09.2026, int8, 4 потока, 9,2 с русской речи):
`small` — загрузка модели с диска 3,8 с, расшифровка 3,9 с, но «реле на сервере»
расслышано как «Релена сервере»; `large-v3-turbo` — расшифровка 25 с и ни одной
ошибки. Отсюда и выбор в окне: скорость против точности, оба варианта названы.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import threading
import time
from pathlib import Path

SCHEMA = "helene.voice.v1"
PROGRESS = "voice-download.json"
INSTALLED = "installed.json"

#: Модели, между которыми выбирает владелец. Размер — по факту скачивания на
#: 10.09.2026; он ЗАЯВЛЕННЫЙ и служит только для полосы прогресса и текста в
#: окне, а «скачана или нет» решается по манифесту, а не по числу байт.
CATALOG: dict[str, dict] = {
    "small": {
        "repo": "Systran/faster-whisper-small",
        "title": "small — быстрее и легче",
        "size_mb": 480,
        "note": "Понимает речь, но путается в именах и редких словах: «перезапусти реле "
                "на сервере» расслышала как «перезапусти Релена сервере». Быстрая — "
                "9 секунд речи разобрала за 4 (машина владельца, 4 потока).",
    },
    "turbo": {
        "repo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "title": "large-v3-turbo — как на сервере",
        "size_mb": 1600,
        "note": "Та же модель, что у агента на сервере: ту же фразу разобрала без "
                "ошибок. Медленнее и тяжелее — 9 секунд речи за 25 (машина владельца, "
                "4 потока), в памяти около 1,5 ГБ.",
    },
}
DEFAULT_MODEL = "turbo"


def _utc() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".tmp-" + path.name)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def block(cfg: dict) -> dict:
    got = cfg.get("voice")
    return got if isinstance(got, dict) else {}


def chosen_model(cfg: dict) -> str:
    name = str(block(cfg).get("model") or "").strip().lower()
    return name if name in CATALOG else DEFAULT_MODEL


def models_dir(tree: Path) -> Path:
    """Куда кладутся модели. Внутри — раскладка кэша Hugging Face, как её
    делает сам faster-whisper: своей мы не изобретаем, иначе он её не найдёт."""
    return Path(tree) / "models" / "whisper"


def manifest(tree: Path) -> dict:
    """Что скачано на самом деле. Пусто — не скачано ничего."""
    return _read(models_dir(tree) / INSTALLED)


def library() -> dict:
    """Есть ли в рантайме то, чем расшифровывать. Импорта модели здесь нет:
    он тянет за собой десятки мегабайт и полсекунды на каждый вызов."""
    import importlib.util  # noqa: PLC0415 — нужен только здесь

    found = importlib.util.find_spec("faster_whisper") is not None
    return {"present": found,
            "why": "" if found else "в рантайме нет faster-whisper — "
                                    "поставка собрана без голоса"}


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def state(tree: Path, cfg: dict) -> dict:
    """Вся правда о голосе: чем расшифровывать, чем слушать, и что мешает."""
    tree = Path(tree)
    want = chosen_model(cfg)
    lib = library()
    have = manifest(tree)
    ready_model = bool(have.get("path")) and Path(str(have.get("path"))).is_dir()
    enabled = bool(block(cfg).get("enabled"))
    out = {
        "schema": SCHEMA,
        "enabled": enabled,
        "model": want,
        "catalog": [dict(spec, id=key, installed=(have.get("model") == key and ready_model))
                    for key, spec in CATALOG.items()],
        "library": lib,
        "installed": have if ready_model else {},
        "dir": str(models_dir(tree)),
        "download": _read(Path(tree) / "memory" / ".state" / PROGRESS) or None,
    }
    if not enabled:
        out["ready"] = False
        out["why"] = "голос выключен владельцем"
    elif not lib["present"]:
        out["ready"] = False
        out["why"] = lib["why"]
    elif not ready_model:
        out["ready"] = False
        out["why"] = "модель не скачана — голосовые не расшифровываются"
    elif have.get("model") != want:
        out["ready"] = True
        out["why"] = (f"скачана модель «{have.get('model')}», а выбрана «{want}» — "
                      "работает скачанная, пока не скачана выбранная")
    else:
        out["ready"] = True
        out["why"] = ""
    return out


def env_for(tree: Path, cfg: dict) -> dict:
    """Переменные для дерева. Пусто — голос не поднимается, и это не молчание:
    причину скажет `state()['why']`, её печатает раннер в журнал."""
    said = state(tree, cfg)
    if not said["ready"]:
        return {}
    have = said["installed"]
    voice = block(cfg)
    threads = str(voice.get("threads") or 4)
    return {
        "PRAXIS_AUDIO_MODEL_DIR": str(Path(tree) / "models"),
        # Путь, а не имя: имя разрешается через кэш Hugging Face и молча
        # промахивается, если раскладка кэша сменилась между версиями.
        "PRAXIS_STT_MODEL": str(have["path"]),
        "PRAXIS_STT_LOCAL_FILES_ONLY": "1",
        "PRAXIS_STT_DEVICE": "cpu",
        "PRAXIS_STT_COMPUTE_TYPE": str(voice.get("compute_type") or "int8"),
        "PRAXIS_STT_CPU_THREADS": threads,
        "PRAXIS_STT_BEAM_SIZE": str(voice.get("beam_size") or 5),
        "PRAXIS_STT_LANGUAGE": str(voice.get("language") or "ru"),
        # На домашней машине держать модель в памяти постоянно — 1,5–2 ГБ за
        # несколько голосовых в день. На сервере наоборот; там своё значение.
        "PRAXIS_STT_KEEP_LOADED": "1" if voice.get("keep_loaded") else "0",
    }


def apply(tree: Path, cfg: dict) -> dict:
    """Проставить переменные процессу раннера и вернуть отчёт для журнала."""
    said = state(tree, cfg)
    for key, value in env_for(tree, cfg).items():
        os.environ[key] = value
    return said


# --- скачивание --------------------------------------------------------------


def fetch(tree: Path, model: str, *, quiet: bool = False) -> dict:
    """Скачать модель в дерево, рассказывая о ходе дела файлом прогресса.

    Прогресс считается по РАЗМЕРУ ПАПКИ, а не по счётчику библиотеки: у
    huggingface_hub он менялся от версии к версии, а папка растёт одинаково во
    всех. Число «сколько всего» — заявленное в каталоге, поэтому проценты
    приблизительны, и окно так их и называет.
    """
    tree = Path(tree)
    model = str(model or "").strip().lower() or DEFAULT_MODEL
    if model not in CATALOG:
        raise SystemExit(f"нет такой модели: {model} (есть: {', '.join(CATALOG)})")
    spec = CATALOG[model]
    dest = models_dir(tree)
    dest.mkdir(parents=True, exist_ok=True)
    progress_path = tree / "memory" / ".state" / PROGRESS
    total = int(spec["size_mb"]) * 1024 * 1024
    started = _utc()

    def say(state_name: str, note: str, got: int) -> None:
        _write(progress_path, {
            "schema": SCHEMA, "model": model, "repo": spec["repo"],
            "state": state_name, "note": note,
            "got_bytes": got, "total_bytes": total,
            "started_utc": started, "updated_utc": _utc(),
        })

    base = dir_size(dest)
    say("running", "качаю…", 0)
    if not quiet:
        print(f"качаю {spec['repo']} (~{spec['size_mb']} МБ) в {dest}", flush=True)

    result: dict = {}
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(2.0):
            got = max(0, dir_size(dest) - base)
            say("running", "качаю…", got)
            if not quiet:
                print(f"  {got / 1e6:.0f} из ~{spec['size_mb']} МБ", flush=True)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        from faster_whisper.utils import download_model  # noqa: PLC0415

        path = download_model(spec["repo"], cache_dir=str(dest), local_files_only=False)
        result = {
            "schema": SCHEMA, "model": model, "repo": spec["repo"],
            "path": str(Path(path).resolve()),
            "bytes": dir_size(Path(path)),
            "got_utc": _utc(),
        }
        _write(dest / INSTALLED, result)
        say("done", "модель на месте", max(0, dir_size(dest) - base))
        if not quiet:
            print(f"готово: {path}", flush=True)
    except ImportError as exc:
        say("failed", f"в рантайме нет faster-whisper: {exc}", 0)
        raise SystemExit("в рантайме нет faster-whisper — поставка собрана без голоса")
    except Exception as exc:  # noqa: BLE001 — причина обязана доехать до окна
        say("failed", f"{type(exc).__name__}: {exc}"[:400], max(0, dir_size(dest) - base))
        raise SystemExit(f"модель не скачалась: {exc}")
    finally:
        stop.set()
        watcher.join(timeout=3)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="голос: состояние и скачивание модели")
    ap.add_argument("--tree", required=True, help="папка данных агента")
    ap.add_argument("--get", metavar="МОДЕЛЬ", help=f"скачать модель ({', '.join(CATALOG)})")
    ap.add_argument("--config", default="", help="helene.json — для состояния")
    args = ap.parse_args()
    tree = Path(args.tree).resolve()
    if args.get:
        fetch(tree, args.get)
        return 0
    cfg = _read(Path(args.config)) if args.config else {}
    print(json.dumps(state(tree, cfg), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
