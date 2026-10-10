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
import contextlib
import datetime as dt
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path

SCHEMA = "helene.voice.v1"
PROGRESS = "voice-download.json"
SPEECH_PROGRESS = "speech-download.json"
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

#: Голоса синтеза — те же правила, что у моделей слуха: библиотека едет в
#: рантайме, голос качает владелец. Числа — живая проба 11.09 на машине
#: владельца: первая фраза 3,1 с (загрузка голоса с диска), следующие 0,24 с на
#: 3,7 с речи, то есть примерно в пятнадцать раз быстрее реального времени.
#:
#: ⚠ Почему piper, а не Edge и не Silero. Edge — это голос МИКРОСОФТА по сети:
#: текст ответа агента уходил бы наружу на каждую фразу, а продукт обещает
#: обратное. Silero тянет torch (около двух гигабайт) ради того же результата.
#: Piper — 34 МБ библиотеки поверх уже привезённого onnxruntime и 60 МБ голоса,
#: целиком на этой машине и без сети.
VOICES: dict[str, dict] = {
    "irina": {
        "voice": "ru_RU-irina-medium",
        "path": "ru/ru_RU/irina/medium",
        "title": "Ирина — женский",
        "size_mb": 61,
        "note": "Ровный женский голос, 22 кГц. 3,7 секунды речи синтезирует за "
                "четверть секунды (машина владельца).",
    },
    "dmitri": {
        "voice": "ru_RU-dmitri-medium",
        "path": "ru/ru_RU/dmitri/medium",
        "title": "Дмитрий — мужской",
        "size_mb": 61,
        "note": "Мужской голос того же качества и веса.",
    },
}
DEFAULT_VOICE = "irina"
VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"


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


# --- движок голоса (1.2.1) ----------------------------------------------------
#
# 1.5.5: по просьбе владельца весь голосовой runtime снова входит в основную
# Windows-поставку, включая PyYAML. Прежняя папка voice/ не перекрывает его.
# Историческая совместимость: Егор27.09 попросил уменьшить установщик. С1.2.1
# на Windows движок (faster-whisper, piper, ctranslate2, onnxruntime, av, numpy — ~315 МБ,
# ~94 МБ сжатыми) не едет в установщике, а докачивается ВМЕСТЕ с моделью или голосом:
# один архив выпуска `Helene-voice-<версия>-windows.tar.zst`, сверенный по сумме из
# паспорта сборки (`helene-build.json` → `voice_pack`), а не по слову сети. Ложится в
# `<установка>/voice/site-packages`; `.pth` рантайма (build_dist: VOICE_PTH) добавляет
# папку в путь каждого процесса при старте, уже живым — `ensure_engine_path()`. Папки
# `voice/` нет в поставке, поэтому обновление переносит её как владельческую.
# На Mac и в сборках, где голос едет в рантайме, качать нечего: библиотека уже есть.

DEFAULT_RELEASES = "josephsteuerjr/praxis"


def install_root() -> Path:
    """Корень установки: `app/localharness/voice.py` → два уровня вверх."""
    env = os.environ.get("HELENE_ROOT")
    return Path(env) if env else Path(__file__).resolve().parents[2]


def engine_home(root: Path | None = None) -> Path:
    return Path(root or install_root()) / "voice"


def engine_site(root: Path | None = None) -> Path:
    return engine_home(root) / "site-packages"


def engine_installed(root: Path | None = None) -> dict:
    return _read(engine_home(root) / INSTALLED)


def _same_python(recorded: str) -> bool:
    parts = str(recorded or "").split(".")
    return len(parts) >= 2 and parts[:2] == [str(sys.version_info.major), str(sys.version_info.minor)]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _engine_stale(root: Path | None = None) -> str:
    have = engine_installed(root)
    if have.get("python") and not _same_python(str(have["python"])):
        return f"движок голоса стоит от другого питона ({have['python']})"
    if not engine_site(root).is_dir():
        return ""
    want = pack_record(root).get("dists")
    if not isinstance(want, dict) or not want:
        return ""
    got = have.get("dists")
    if not isinstance(got, dict):
        # Перенос из старого runtime не записывал версии в installed.json.
        # Читаем metadata без импорта тяжёлых библиотек и загрузки модели.
        from importlib.metadata import distributions
        got = {str(d.metadata.get("Name") or "").lower().replace("_", "-"): d.version
               for d in distributions(path=[str(engine_site(root))])}
    if any(str(got.get(name) or "") != str(version) for name, version in want.items()):
        return "для этой версии программы нужен обновлённый движок голоса"
    return ""


def ensure_engine_path(root: Path | None = None) -> bool:
    """Скачанный после старта процесса движок — в путь, кэш импорта — заново. Движок от
    другого питона в путь не ставится (и убирается, если его добавил `.pth`): его .pyd
    всё равно не загрузятся, а find_spec соврал бы «есть». -> папка движка в пути."""
    site_dir = engine_site(root)
    mine = _norm(str(site_dir))
    if _read(Path(root or install_root()) / 'helene-build.json').get('voice_runtime') is True:
        # The current runtime owns these modules. An old downloaded bundle
        # must not override its compiled extensions or transitive libraries.
        sys.path[:] = [p for p in sys.path if _norm(p) != mine]
        importlib.invalidate_caches()
        return False
    if not site_dir.is_dir() or _engine_stale(root):
        sys.path[:] = [p for p in sys.path if _norm(p) != mine]
        return False
    if all(_norm(p) != mine for p in sys.path):
        sys.path.append(str(site_dir))
    importlib.invalidate_caches()
    return True


def pack_record(root: Path | None = None) -> dict:
    """Что паспорт этой установки говорит об архиве движка. Пусто — движок в рантайме."""
    passport = _read(Path(root or install_root()) / "helene-build.json")
    if passport.get('voice_runtime') is True:
        return {}
    rec = passport.get("voice_pack")
    return dict(rec, version=str(passport.get("version") or "")) if isinstance(rec, dict) else {}


def _windows_pack_supported() -> bool:
    return sys.platform == "win32"


def _engine_missing(what: str, fallback: str) -> dict:
    rec = pack_record()
    if rec.get("name", "").endswith("-windows.tar.zst") and not _windows_pack_supported():
        return {"present": False, "downloadable": False,
                "why": f"в серверном рантайме нет {what}; Windows-движок сюда не подходит — обнови зависимости серверной поставки"}
    if rec.get("name") and rec.get("sha256"):
        size = round(int(rec.get("bytes") or 0) / 1024 / 1024)
        stale = _engine_stale()
        why = (f"{stale} — скачается заново"
               if stale else f"движок голоса ещё не скачан (~{size} МБ)")
        return {"present": False, "downloadable": True, "size_mb": size, "why": why}
    return {"present": False, "downloadable": False, "why": f"в рантайме нет {what} — {fallback}"}


def _pack_url(root: Path, rec: dict) -> str:
    override = os.environ.get("HELENE_VOICE_PACK_URL", "").strip()
    if override:
        return override
    api = str((_read(root / "helene.json").get("update") or {}).get("url") or "")
    m = re.search(r"repos/([^/\s]+)/([^/\s]+)/releases", api)
    repo = f"{m.group(1)}/{m.group(2)}" if m else DEFAULT_RELEASES
    return f"https://github.com/{repo}/releases/download/v{rec['version']}/{rec['name']}"


def fetch_engine(root: Path | None = None, on_bytes=None) -> dict:
    """Скачать и поставить движок голоса. Стоит и годен — ничего не делает.

    ⚠ Порядок против полуустановки: архив качается в `.part`, сверяется по сумме из
    паспорта, распаковывается в `.new` (фильтр `data`: ни абсолютных путей, ни `..`), и
    только потом `.new` встаёт на место прежнего. Оборванная закачка или чужой файл не
    оставляют движка, который выглядит поставленным.
    """
    import tarfile  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415
    import importlib.util  # noqa: PLC0415

    root = Path(root or install_root())
    provided = ensure_engine_path(root) or _read(root / 'helene-build.json').get('voice_runtime') is True
    if provided and importlib.util.find_spec("faster_whisper") is not None and importlib.util.find_spec('yaml') is not None:
        return {"state": "present"}
    rec = pack_record(root)
    if rec.get("name", "").endswith("-windows.tar.zst") and not _windows_pack_supported():
        raise SystemExit("Windows-движок голоса не подходит этой системе; нужны зависимости серверной поставки")
    if not rec.get("name") or not rec.get("sha256"):
        raise SystemExit("в этой сборке движок голоса едет в рантайме — качать нечего")
    home = engine_home(root)
    home.mkdir(parents=True, exist_ok=True)
    tag = f"{os.getpid()}-{int(time.time())}"
    part, fresh, old = home / f".part-{tag}", home / f".new-{tag}", home / f".old-{tag}"
    total = int(rec.get("bytes") or 0)
    url = _pack_url(root, rec)
    digest = hashlib.sha256()
    got = 0
    installed_new = False
    committed = False
    try:
        with urllib.request.urlopen(url, timeout=300) as src, part.open("wb") as sink:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                sink.write(chunk)
                digest.update(chunk)
                got += len(chunk)
                if on_bytes:
                    on_bytes(got, total)
        if digest.hexdigest() != str(rec["sha256"]).lower():
            raise SystemExit("архив движка голоса не сошёлся по сумме с паспортом — скачанное удалено")
        with tarfile.open(part, mode="r:zst") as tar:
            tar.extractall(fresh, filter="data")
        meta = _read(fresh / "voice-pack.json")
        if not (fresh / "site-packages").is_dir():
            raise SystemExit("в архиве движка нет site-packages")
        if meta.get("python") and not _same_python(str(meta["python"])):
            raise SystemExit(f"движок собран под питон {meta['python']}, а здесь "
                             f"{sys.version_info.major}.{sys.version_info.minor}")
        site_dir = engine_site(root)
        if site_dir.exists():
            os.replace(site_dir, old)
        os.replace(fresh / "site-packages", site_dir)
        installed_new = True
        result = {"schema": SCHEMA, "source": "download", "version": rec.get("version", ""),
                  "python": str(meta.get("python") or rec.get("python") or ""),
                  "sha256": rec["sha256"], "bytes": got, "dists": meta.get("dists") or {},
                  "got_utc": _utc()}
        _write(home / INSTALLED, result)
        committed = True
    except BaseException:
        # The previous engine stays recoverable until its new receipt is durable.
        # A failed rename or full disk must not erase an otherwise working voice.
        if installed_new:
            os.replace(engine_site(root), fresh / "site-packages")
        if old.exists():
            os.replace(old, engine_site(root))
        raise
    finally:
        for leftover in (part, fresh, *([old] if committed else [])):
            if leftover.is_dir():
                shutil.rmtree(leftover, ignore_errors=True)
            elif leftover.exists():
                with contextlib.suppress(OSError):
                    leftover.unlink()
    ensure_engine_path(root)
    return result


def library() -> dict:
    """Есть ли чем расшифровывать. Импорта модели здесь нет: он тянет за собой
    десятки мегабайт и полсекунды на каждый вызов. С 1.2.1 «нет» бывает двух видов:
    движок можно докачать (`downloadable`) или сборка вовсе без голоса."""
    import importlib.util  # noqa: PLC0415 — нужен только здесь

    ensure_engine_path()
    if importlib.util.find_spec("faster_whisper") is not None:
        if importlib.util.find_spec('yaml') is None:
            result = _engine_missing('PyYAML (yaml)', 'обнови полную установку Hélène')
            result['why'] = 'В движке голоса отсутствует yaml (PyYAML). ' + result['why']
            return result
        return {"present": True, "why": ""}
    return _engine_missing("faster-whisper", "поставка собрана без голоса")


def dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def chosen_voice(cfg: dict) -> str:
    name = str(block(cfg).get("voice") or "").strip().lower()
    return name if name in VOICES else DEFAULT_VOICE


def voices_dir(tree: Path) -> Path:
    """Куда кладутся голоса синтеза. Рядом с моделями слуха, но отдельно: это
    разные вещи, и «удалить голос» не должно задевать слух."""
    return Path(tree) / "models" / "piper"


def speech_library() -> dict:
    """Есть ли чем говорить. Импорта нет: он тянет onnxruntime на полсекунды."""
    import importlib.util  # noqa: PLC0415 — нужен только здесь

    ensure_engine_path()
    if importlib.util.find_spec("piper") is not None:
        return {"present": True, "why": ""}
    return _engine_missing("piper-tts", "поставка собрана без голоса наружу")


def speech_state(tree: Path, cfg: dict) -> dict:
    """Правда о голосе агента НАРУЖУ: чем говорить, каким голосом, что мешает.

    Отдельно от слуха намеренно: слышать и говорить — разные умения, и владелец
    вправе включить одно без другого (например, слышать голосовые, но отвечать
    текстом). Общий выключатель `voice.enabled` — про слух; здесь свой.
    """
    tree = Path(tree)
    want = chosen_voice(cfg)
    lib = speech_library()
    spec = VOICES[want]
    model = voices_dir(tree) / f"{spec['voice']}.onnx"
    config = voices_dir(tree) / f"{spec['voice']}.onnx.json"
    # Голос — это ДВА файла, и один без другого piper не поднимет. Проверяем оба:
    # «скачано наполовину» и «скачано» выглядели бы одинаково.
    ready_voice = model.is_file() and config.is_file() and model.stat().st_size > 1_000_000
    enabled = bool(block(cfg).get("speak"))
    out = {
        "enabled": enabled,
        "voice": want,
        "catalog": [dict(one, id=key,
                         installed=(voices_dir(tree) / f"{one['voice']}.onnx").is_file())
                    for key, one in VOICES.items()],
        "library": lib,
        "model": str(model) if ready_voice else "",
        "dir": str(voices_dir(tree)),
        "download": _read(Path(tree) / "memory" / ".state" / SPEECH_PROGRESS) or None,
    }
    if not enabled:
        out["ready"] = False
        out["why"] = "голос агента выключен владельцем"
    elif not lib["present"]:
        out["ready"] = False
        out["why"] = lib["why"] + (" — скачается вместе с голосом" if lib.get("downloadable") else "")
    elif not ready_voice:
        out["ready"] = False
        out["why"] = "голос не скачан — агент отвечает текстом"
    else:
        out["ready"] = True
        out["why"] = ""
    return out


def state(tree: Path, cfg: dict) -> dict:
    """Вся правда о голосе: чем расшифровывать, чем слушать, и что мешает."""
    tree = Path(tree)
    want = chosen_model(cfg)
    lib = library()
    have = manifest(tree)
    ready_model = bool(have.get("path")) and Path(str(have.get("path"))).is_dir()
    if not ready_model and os.environ.get("HELENE_SUPERVISOR") == "serverboot":
        external = Path(os.environ.get("PRAXIS_STT_MODEL") or "/nonexistent-model")
        if external.is_dir() and (external / "model.bin").is_file() and (external / "config.json").is_file():
            have = {"model": "server:" + external.name, "path": str(external), "source": "server-volume"}
            ready_model = True
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
        out["why"] = lib["why"] + (" — скачается вместе с моделью" if lib.get("downloadable") else "")
    elif not ready_model:
        out["ready"] = False
        out["why"] = "модель не скачана — голосовые не расшифровываются"
    elif have.get("model") != want:
        out["ready"] = True
        out["why"] = (f"работает модель «{have.get('model')}»; выбранную «{want}» ещё нужно скачать")
    else:
        out["ready"] = True
        out["why"] = ""
    # Голос наружу — в том же ответе: окно рисует обе половины одной карточкой,
    # и две ручки за двумя запросами разъезжались бы у него в руках.
    out["speech"] = speech_state(tree, cfg)
    return out


def env_for(tree: Path, cfg: dict) -> dict:
    """Переменные для дерева. Пусто — голос не поднимается, и это не молчание:
    причину скажет `state()['why']`, её печатает раннер в журнал."""
    said = state(tree, cfg)
    if not said["ready"]:
        return {}
    have = said["installed"]
    voice = block(cfg)
    # 26.09: окну и владельцу — хотя бы одно ядро. Четыре потока на четырёх ядрах
    # забирали процессор целиком, и окно дёргалось, пока шла расшифровка.
    wanted = int(voice.get("threads") or 4)
    threads = str(max(1, min(wanted, (os.cpu_count() or 4) - 1)))
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


def speech_env_for(tree: Path, cfg: dict) -> dict:
    """Переменные синтеза для дерева. Пусто — агент отвечает текстом, и почему,
    говорит `speech_state()['why']`."""
    said = speech_state(tree, cfg)
    if not said["ready"]:
        return {}
    return {
        "PRAXIS_TTS_BACKEND": "piper",
        # Путь к файлу, а не имя голоса: имя разрешается внутри библиотеки и
        # молча промахивается, когда раскладка папки не та, которую она ждёт.
        "PRAXIS_PIPER_MODEL": said["model"],
        # Синтезированное складывается В ДЕРЕВО агента, а не во временную папку
        # системы: это его слова, и переезд дерева обязан увозить их с собой.
        "PRAXIS_TTS_OUTPUT_DIR": str(Path(tree) / "media" / "tts"),
    }


def apply(tree: Path, cfg: dict) -> dict:
    """Проставить переменные процессу раннера и вернуть отчёт для журнала."""
    said = state(tree, cfg)
    for key, value in env_for(tree, cfg).items():
        os.environ[key] = value
    # Речь ставится теми же правилами и ТЕМ ЖЕ вызовом: две точки применения
    # разъехались бы на первой же правке, и одна половина голоса поднималась бы
    # без другой молча.
    for key, value in speech_env_for(tree, cfg).items():
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

    def say(state_name: str, note: str, got: int, *, of: int | None = None, what: str = "") -> None:
        _write(progress_path, {
            "schema": SCHEMA, "model": what or model, "repo": spec["repo"],
            "state": state_name, "note": note,
            "got_bytes": got, "total_bytes": total if of is None else of,
            "started_utc": started, "updated_utc": _utc(),
        })

    # 1.2.1: движок голоса — первым, той же полосой: владелец нажал одну кнопку.
    lib = library()
    if not lib["present"]:
        if not lib.get("downloadable"):
            say("failed", lib["why"], 0)
            raise SystemExit(lib["why"])
        say("running", "качаю движок голоса…", 0, of=0, what="движок голоса")
        if not quiet:
            print(f"сначала движок голоса (~{lib.get('size_mb')} МБ)", flush=True)
        try:
            fetch_engine(on_bytes=lambda got, of: say(
                "running", "качаю движок голоса…", got, of=of, what="движок голоса"))
        except SystemExit as exc:
            say("failed", f"движок голоса не встал: {exc}", 0, what="движок голоса")
            raise
        except Exception as exc:  # noqa: BLE001 — причина обязана доехать до окна
            say("failed", f"движок голоса не скачался: {type(exc).__name__}: {exc}"[:400], 0,
                what="движок голоса")
            raise SystemExit(f"движок голоса не скачался: {exc}")

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
        missing = str(getattr(exc, 'name', '') or 'зависимость голоса')
        say("failed", f"Не загрузилась библиотека голоса {missing}. Обнови полную установку Hélène.", 0)
        raise SystemExit(f"Не загрузилась библиотека голоса {missing}; требуется полная установка Hélène")
    except Exception as exc:  # noqa: BLE001 — причина обязана доехать до окна
        say("failed", f"{type(exc).__name__}: {exc}"[:400], max(0, dir_size(dest) - base))
        raise SystemExit(f"модель не скачалась: {exc}")
    finally:
        stop.set()
        watcher.join(timeout=3)
    return result


def fetch_voice(tree: Path, voice_id: str, *, quiet: bool = False) -> dict:
    """Скачать голос синтеза: два файла, оба обязательны.

    ⚠ Файла два — сам голос (`.onnx`) и его описание (`.onnx.json`), и piper без
    второго не поднимется. Поэтому «скачано» здесь значит «оба на месте»: иначе
    полускачанный голос выглядел бы готовым и молчал уже в бою.
    """
    import urllib.request  # noqa: PLC0415 — нужен только здесь

    tree = Path(tree)
    voice_id = str(voice_id or "").strip().lower() or DEFAULT_VOICE
    if voice_id not in VOICES:
        raise SystemExit(f"голос «{voice_id}»: знаю только {', '.join(VOICES)}")
    spec = VOICES[voice_id]
    home = voices_dir(tree)
    home.mkdir(parents=True, exist_ok=True)
    progress = Path(tree) / "memory" / ".state" / SPEECH_PROGRESS
    started = time.time()

    def note(**extra) -> dict:
        got = {"schema": SCHEMA, "voice": voice_id, "at": _utc(),
               "size_mb": spec["size_mb"], **extra}
        _write(progress, got)
        if not quiet:
            print(json.dumps(got, ensure_ascii=False), flush=True)
        return got

    # 1.2.1: без движка голос не заговорит — он первым, той же строкой хода.
    lib = speech_library()
    if not lib["present"]:
        if not lib.get("downloadable"):
            return note(state="failed", error=lib["why"])
        note(state="running", voice="движок голоса", done_mb=0, size_mb=lib.get("size_mb"))
        try:
            fetch_engine(on_bytes=lambda got, of: note(
                state="running", voice="движок голоса", done_mb=round(got / 1024 / 1024, 1),
                size_mb=round(of / 1024 / 1024) if of else lib.get("size_mb")))
        except SystemExit as exc:
            return note(state="failed", error=f"движок голоса не встал: {exc}"[:300])
        except Exception as exc:  # noqa: BLE001 — причина уезжает владельцу
            return note(state="failed", error=f"движок голоса не скачался: {type(exc).__name__}: {exc}"[:300])
    note(state="running", done_mb=0)
    got_bytes = 0
    try:
        for name in (f"{spec['voice']}.onnx", f"{spec['voice']}.onnx.json"):
            url = f"{VOICES_BASE}{spec['path']}/{name}?download=true"
            # Кладём под временным именем и переименовываем: оборванная закачка
            # не должна оставить файл, который выглядит скачанным.
            final = home / name
            partial = home / f".part-{name}"
            with urllib.request.urlopen(url, timeout=300) as src, partial.open("wb") as sink:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    sink.write(chunk)
                    got_bytes += len(chunk)
                    note(state="running", done_mb=round(got_bytes / 1024 / 1024, 1))
            os.replace(partial, final)
    except Exception as exc:                      # noqa: BLE001 — причина уезжает владельцу
        return note(state="failed", error=f"{type(exc).__name__}: {exc}"[:300])
    return note(state="done", done_mb=round(got_bytes / 1024 / 1024, 1),
                seconds=round(time.time() - started, 1))


def main() -> int:
    ap = argparse.ArgumentParser(description="голос: состояние и скачивание модели")
    ap.add_argument("--tree", required=True, help="папка данных агента")
    ap.add_argument("--get", metavar="МОДЕЛЬ", help=f"скачать модель слуха ({', '.join(CATALOG)})")
    ap.add_argument("--get-voice", metavar="ГОЛОС",
                    help=f"скачать голос синтеза ({', '.join(VOICES)})")
    ap.add_argument("--get-engine", action="store_true",
                    help="скачать только движок голоса (Windows, 1.2.1+)")
    ap.add_argument("--config", default="", help="helene.json — для состояния")
    args = ap.parse_args()
    tree = Path(args.tree).resolve()
    if args.get_engine:
        print(json.dumps(fetch_engine(), ensure_ascii=False))
        return 0
    if args.get:
        fetch(tree, args.get)
        return 0
    if args.get_voice:
        fetch_voice(tree, args.get_voice)
        return 0
    cfg = _read(Path(args.config)) if args.config else {}
    print(json.dumps(state(tree, cfg), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
