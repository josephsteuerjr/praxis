"""
Миграция ключа разговора: одна беседа перестаёт быть несколькими.

Что делает
----------
1. Читает архивы комнат и записывает в реестр маршрутов ДВА факта, оба по свидетельству:
   форум ли комната (по служебным «создана тема» на пройденном диапазоне) и какие ветки
   на самом деле наши артефакты, а не места Telegram (по правилу «корень ветки лежит в
   ней самой»).
2. Пересобирает состояние ленты жизни на КЛЮЧ МЕСТА: горячее кольцо, курсор свёртки,
   дедуп. Состояние производное — оно всегда восстанавливается из событий и компактов.
3. Проверяет три инварианта и печатает контрольную сумму.
4. Отодвигает состояния, оставшиеся от расщеплённых ключей, в `_pre_places/`.

Чего НЕ делает
--------------
Не трогает ни одного события, ни одного компакта, ни одного архива комнаты. Ничего не
удаляет. События по-прежнему хранят ключ, под которым были сказаны, — это честная
запись «где», и переписывать её незачем: место считается на чтении.

Откат
-----
    python migrate_places.py --rollback
Удаляет файлы реестра маршрутов и возвращает состояния из `_pre_places/`. После этого
всё читается ровно как до миграции: предикат места без реестра вырождается в равенство
ключей.

Канарейка (`--peer`)
--------------------
    python migrate_places.py --peer -1001240718803 [--dry-run]
    python migrate_places.py --rollback --peer -1001240718803 [--run <run_id>]
Ограничивает прогон ОДНОЙ комнатой. Каждый canary-прогон живёт в СОБСТВЕННОМ
каталоге `_pre_places/runs/<run_id>/` (её контракт 23.08 после живой канарейки
AbstractDL): полная копия ВСЕХ состояний пира + атомарно закоммиченный
`manifest.json` с точным списком baseline-файлов и их sha256; расщеплённые
состояния паркуются в `runs/<run_id>/retired/`. Плоский legacy-чердак (июльская
эпоха) канарейкой НЕ читается и НЕ пишется — коллизия со старыми файлами
невозможна по построению, правила «пропустить одноимённый» не существует, база
всегда полная и одноэпохная. Обрыв до коммита оставляет каталог без manifest —
такой черновик не база ни для кого; новый прогон получает свежий run_id.
Транзакция пира сериализована межпроцессным per-peer замком. В базу прогона
входят `route.json.baseline` и `bindings_before` (до-прогонный состав журнала
привязок этого пира). Провал инвариантов транзакционен на уровне канарейки:
точное восстановление из manifest ЭТОГО прогона — состояния и route
байт-в-байт, из журнала привязок снимаются только записи с ДОКАЗАННЫМ
авторством: receipt-факт либо durable per-entry прогресс со sha-свидетелем
журнала для обрыва посреди записи (`bindings_added.json`; до-прогонные, чужие,
параллельные и одноимённые внешние привязки недостижимы; `.rev` не
откатывается — REPAIR5);
failed-пометка ложится ДО восстановления, обрыв доводится явным
`--rollback --run` (он поднимает route-базу, а не удаляет route); успех
помечается `recovered.json` — прогон «пригодной канарейкой» больше не является
и из откатной выборки исключён.
Откат работает ТОЛЬКО по manifest выбранного прогона (никаких glob'ов по пиру):
каждый файл сверяется со своим sha256 до единой записи; неоднозначность
(несколько прогонов без `--run`) и незнакомый run_id — полный отказ.
Восстановление копирует из архива прогона — снимок остаётся на месте.
Скоуп-откат удаляет только `<peer>.route.json`; сайдкар `<peer>.route.rev`
остаётся СОЗНАТЕЛЬНО — монотонность durable-ревизии (REPAIR5) обязана переживать
откат, иначе живой процесс мог бы принять снимок из прошлой жизни токена.

Инварианты
----------
    1. Ни одно событие не пропало и не задвоилось: горячее ∪ покрытое = все события места,
       пересечение пусто.
    2. Ни один канонический компакт не перестал быть каноническим.
    3. Ни один блок сводки, который она видела по старому ключу, не исчез из сводки места.

Запуск:  python migrate_places.py [--base /app] [--dry-run] [--rollback] [--peer -100…]
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    import fcntl
except ImportError:                                    # pragma: no cover — Windows
    fcntl = None
    import msvcrt


def _modules(base: Path):
    """Импорт после установки PRAXIS_BASE: каталоги считаются на импорте."""
    os.environ["PRAXIS_BASE"] = str(base)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import group_context
    import memory_life
    import telegram_routes
    return group_context, memory_life, telegram_routes


def _peer_owns(key, peer) -> bool:
    """Принадлежит ли ключ этому пиру: сам id, `<peer>-<hash>` (архив),
    `<peer>__topic__<n>` (ветка), `<peer>.route` (реестр).

    Символ сразу после id не бывает цифрой — цифра значила бы другой, более
    длинный id, а не наш с суффиксом.
    """
    key, peer = str(key), str(peer)
    if key == peer:
        return True
    return (key.startswith(peer) and len(key) > len(peer)
            and not key[len(peer)].isdigit())


@contextlib.contextmanager
def _locked_file(path: Path):
    handle = open(path, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:                                              # pragma: no cover — Windows
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:                                          # pragma: no cover — Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()


@contextlib.contextmanager
def _peer_lock(memory_life, peer):
    """Межпроцессный замок канарейной работы ОДНОГО пира (её P0 23.08 №2).

    Снимок (recovery → каталог прогона → все копии → коммит manifest) и
    скоуп-откат этого пира происходят строго по одному. Второй конкурент ждёт на
    flock и входит уже после чужого коммита — интерливинга шагов не существует.
    """
    memory_life.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with _locked_file(memory_life.STATE_DIR / f"_canary_{peer}.lock"):
        yield


# --------------------------------------------------------------------------- #
#  канарейные прогоны: изолированные namespace (её P0 23.08 после живой
#  канарейки AbstractDL — плоский чердак смешивал эпохи с июльским legacy)
# --------------------------------------------------------------------------- #
_RUN_SCHEMA = "praxis.canary.run.v1"


def _runs_root(memory_life) -> Path:
    return memory_life.STATE_DIR / "_pre_places" / "runs"


def _new_run_id(peer) -> str:
    # Префикс `run-` ОБЯЗАТЕЛЕН: id пира начинается с «-», и run_id, начинающийся
    # с «-», ломал copy-paste команды `--run -100…` (argparse читал его как флаг —
    # её REPAIR6). Сгенерированные командой подсказки обязаны работать дословно.
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run-{stamp}-{os.urandom(3).hex()}-{peer}"


def _commit_run_manifest(run_dir: Path, manifest: dict) -> None:
    """Коммит-точка прогона: атомарная замена manifest.json. До неё каталог
    прогона — незакоммиченный черновик: базой не является ни для кого, и никакой
    другой прогон его не читает и не чистит."""
    tmp = run_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(run_dir / "manifest.json")


def _read_run_manifest(run_dir: Path) -> dict | None:
    try:
        data = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(data, dict) and data.get("schema") == _RUN_SCHEMA
            and isinstance(data.get("files"), list)
            and data.get("run_id") and data.get("peer")):
        return None
    return data


def committed_runs(memory_life, peer) -> list[dict]:
    """Закоммиченные canary-прогоны этого пира, старые раньше новых.

    Источник — ТОЛЬКО manifest.json прогонов: ни glob'а по baseline-файлам, ни
    чтения плоского legacy-чердака (июльская эпоха) здесь нет по построению.
    Прогон, восстановленный после провала инвариантов (`recovered.json`), несёт
    флаг `_recovered` — «пригодной канарейкой» он не является.
    """
    root = _runs_root(memory_life)
    out = []
    if root.exists():
        for item in sorted(root.iterdir()):
            if not item.is_dir():
                continue
            manifest = _read_run_manifest(item)
            if manifest is not None and str(manifest.get("peer")) == str(peer):
                manifest["_recovered"] = (item / "recovered.json").exists()
                out.append(manifest)
    out.sort(key=lambda m: str(m.get("created_at") or ""))
    return out


def _mark_recovered(run_dir: Path, payload: dict) -> None:
    """Атомарная пометка: прогон восстановлен после провала инвариантов и
    «пригодной канарейкой» больше не является (её контракт REPAIR4 п.4)."""
    tmp = run_dir / "recovered.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(run_dir / "recovered.json")


def _mark_failed(run_dir: Path, payload: dict) -> None:
    """Атомарная пометка ДО начала восстановления: инварианты этого прогона
    провалились. Обрыв восстановления в любой точке оставляет failed без
    recovered — явная доводка (`--rollback --run`) знает, что перед ней
    провалившийся прогон, и снимает в том числе привязки его дельты."""
    tmp = run_dir / "failed.json.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(run_dir / "failed.json")


def _write_bindings_receipt(run_dir: Path, status: str, entries: list) -> None:
    tmp = run_dir / "bindings_added.json.tmp"
    tmp.write_text(json.dumps({"status": status, "entries": entries},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(run_dir / "bindings_added.json")


def _journal_bytes(memory_life) -> bytes:
    try:
        return memory_life.bindings_path().read_bytes()
    except OSError:
        return b""


def _journal_replace(memory_life, payload: bytes) -> None:
    """Атомарная замена журнала привязок РОВНО подготовленными байтами —
    их sha256 записан в receipt ДО замены и служит свидетелем авторства
    после обрыва (её REPAIR8)."""
    path = memory_life.bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".canary.tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _read_bindings_receipt(run_dir: Path) -> dict | None:
    try:
        data = json.loads(
            (run_dir / "bindings_added.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        # наследный receipt-намерение (REPAIR6): авторство по нему НЕ доказано
        return {"status": "planned", "entries": data}
    return None


def _bind_places_with_receipt(memory_life, before: dict, peer, run_dir: Path) -> int:
    """Привязки канарейки — с авторством, доказанным ФАКТОМ записи (её REPAIR7).

    Receipt-намерение (`status: planned`) ложится атомарно до записи, но
    авторство утверждает только receipt-ФАКТ (`status: committed`), куда
    попадают ЛИШЬ пары, которые этот прогон реально записал сам —
    `bind_place` вернул 1. Внешний writer, успевший положить ту же пару между
    планом и записью, оставляет пару вне факта: она помечается `foreign` и
    восстановлением недостижима (её repro same-key race, REPAIR7). Прогресс
    durable ПО-ЗАПИСЬНО (её REPAIR8): каждая пара проходит
    `pending → writing → done` с атомарной фиксацией receipt на каждом шаге, а
    состояние `writing` несёт sha «до» и ожидаемый sha «после» замены журнала —
    обрыв между записью пары и её фиксацией остаётся ДОКАЗУЕМЫМ по свидетелю.
    Каждая entry-транзакция — под ЕДИНОЙ межпроцессной границей журнала
    `memory_life._bindings_write_lock`, которой подчиняется и сам `bind_place`
    (её REPAIR9). Недоказуемое восстановление не трогает и называет вслух — не
    гадает.
    """
    members = collections.defaultdict(set)
    for key in before:
        members[memory_life.place_key(key)].add(key)
    root = memory_life.COMPACTS_DIR
    if root.exists():
        for item in sorted(root.iterdir()):
            if item.is_dir() and _peer_owns(item.name, peer):
                members[memory_life.place_key(item.name)].add(item.name)
    memory_life.LIFE_DIR.mkdir(parents=True, exist_ok=True)
    with memory_life._BINDINGS_LOCK:
        data = memory_life.bindings()
        entries = []
        for place, keys in sorted(members.items()):
            place = str(place)
            for key in sorted(keys):
                key = str(key)
                if key and key != place and key not in data:
                    entries.append({"key": key, "place": place,
                                    "state": "pending"})
    _write_bindings_receipt(run_dir, "planned", entries)
    done = 0
    for entry in entries:
        # ЕДИНАЯ межпроцессная граница записи журнала (её REPAIR9): та же
        # `memory_life._bindings_write_lock`, которой подчиняется и bind_place.
        # Каждая entry-транзакция (свежее чтение → receipt → замена → done)
        # атомарна против ЛЮБОГО кооперативного writer'а любого процесса —
        # ни потери чужого ключа full-file replace'ом, ни ложного авторства
        # same-key в окне writing→replace не существует по построению.
        with memory_life._BINDINGS_LOCK:
            with memory_life._bindings_write_lock():
                current = dict(memory_life.bindings())
                if entry["key"] in current:
                    # внешний writer успел первым — пара НЕ наша (REPAIR7)
                    entry["state"] = "foreign"
                    _write_bindings_receipt(run_dir, "in_progress", entries)
                    continue
                current[entry["key"]] = entry["place"]
                payload = json.dumps(current, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")
                # durable per-entry прогресс (её REPAIR8): sha «до» и ожидаемый
                # sha «после» ложатся в receipt ДО замены — обрыв между записью
                # пары и её фиксацией остаётся доказуемым по свидетелю
                entry["prev_sha"] = hashlib.sha256(
                    _journal_bytes(memory_life)).hexdigest()
                entry["expect_sha"] = hashlib.sha256(payload).hexdigest()
                entry["state"] = "writing"
                _write_bindings_receipt(run_dir, "in_progress", entries)
                _journal_replace(memory_life, payload)
                entry["state"] = "done"
                _write_bindings_receipt(run_dir, "in_progress", entries)
                done += 1
    _write_bindings_receipt(run_dir, "committed",
                            [e for e in entries if e["state"] == "done"])
    return done


def _remove_run_bindings(memory_life, peer, run_dir: Path) -> tuple[list[str], int]:
    """Снять из журнала ТОЛЬКО привязки с ДОКАЗАННЫМ авторством этого прогона.
    -> (снятые ключи, недоказуемых записей).

    Доказательства (её REPAIR6+REPAIR7+REPAIR8):
    - `status: committed` — receipt-факт: пары, которые прогон реально записал;
    - `planned`/`in_progress` (обрыв посреди привязок) — по durable per-entry
      прогрессу: `done` = наша; `writing` = решает sha-свидетель журнала
      (текущий sha == expect_sha ⇒ файл ровно наш результат — наша; == prev_sha
      ⇒ замена не случилась — не наша; иначе — недоказуемо, НЕ трогаем);
      `pending`/`foreign` и наследный list-receipt — не наши.
    Ключ снимается лишь при точном совпадении значения. Set-diff не существует.
    """
    receipt = _read_bindings_receipt(run_dir)
    if not receipt:
        return [], 0
    status = receipt.get("status")
    entries = receipt.get("entries") or []
    if not isinstance(entries, list):
        return [], 0
    removed = []
    unproven = 0
    memory_life.LIFE_DIR.mkdir(parents=True, exist_ok=True)
    with memory_life._BINDINGS_LOCK:
        with memory_life._bindings_write_lock():
            journal_sha = hashlib.sha256(_journal_bytes(memory_life)).hexdigest()
            provable = []
            if status == "committed":
                provable = [e for e in entries if isinstance(e, dict)]
            elif status in ("planned", "in_progress"):
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    state = str(entry.get("state") or "pending")
                    if state == "done":
                        provable.append(entry)
                    elif state == "writing":
                        if entry.get("expect_sha") == journal_sha:
                            provable.append(entry)
                        elif entry.get("prev_sha") == journal_sha:
                            pass              # замена не случилась — не наша
                        else:
                            unproven += 1     # свидетель не сходится — не гадаем
            else:
                return [], 0
            data = dict(memory_life.bindings())
            for entry in provable:
                key = str(entry.get("key") or "")
                place = str(entry.get("place") or "")
                if key and _peer_owns(key, peer) and data.get(key) == place:
                    data.pop(key)
                    removed.append(key)
            if removed:
                path = memory_life.bindings_path()
                path.parent.mkdir(parents=True, exist_ok=True)
                memory_life._atomic_json(path, data)
    return sorted(removed), unproven


# --------------------------------------------------------------------------- #
#  1. свидетельства из архивов
# --------------------------------------------------------------------------- #
def archive_peers(group_context) -> list[str]:
    """Пиры, у которых есть канонический архив комнаты."""
    peers = []
    root = group_context.GROUPS_DIR
    if root.exists():
        for item in sorted(root.iterdir()):
            if item.is_dir():
                peer = str(item.name).rsplit("-", 1)[0]
                if peer and peer not in peers:
                    peers.append(peer)
    return peers


# Авторитетные источники topic-записи (см. group_context.record_topic).
# Запись без origin могла быть фантомной — для посева не считается.
AUTHORITATIVE_TOPIC_ORIGINS = {"opener", "forum_list"}


def seed_registry(group_context, telegram_routes, *, dry_run: bool,
                  only_peer: str | None = None) -> list[dict]:
    """Записать в реестр то, что уже видно в архивах. Идемпотентно."""
    rows = []
    peers = archive_peers(group_context)
    if only_peer is not None:
        peers = [peer for peer in peers if peer == only_peer]
    for peer in peers:
        ids, openers, openers_total, branches = [], 0, 0, set()
        for record in group_context.iter_records(peer):
            kind = record.get("kind")
            if kind == "topic":
                openers_total += 1
                if str(record.get("origin") or "") in AUTHORITATIVE_TOPIC_ORIGINS:
                    openers += 1
            elif kind == "message":
                if record.get("message_id"):
                    ids.append(int(record["message_id"]))
                if record.get("topic_id") is not None:
                    branches.add(int(record["topic_id"]))
        if not ids:
            continue
        mapping = group_context.branch_containers(peer)
        row = {"peer": peer, "messages": len(ids), "branches": len(branches),
               "openers": openers, "openers_total": openers_total,
               "artifacts": len(mapping),
               "verdict": ("форум" if openers
                           else "воздержание: нет авторитетного свидетельства")}
        if not dry_run:
            # REPAIR d2e (её приёмка 25.08): FALSE из архивной тишины не пишется
            # ВООБЩЕ. Тишина и фантомные записи дают ВОЗДЕРЖАНИЕ: эпох не трогаем,
            # ключи остаются unknown/placement до позитивного авторитетного
            # свидетельства — живого (channel_forum_missing / legacy_chat /
            # get_forum_topics_ok) или скана. Иначе старый форум с General
            # переехал бы из ложного TRUE от фантома в ложный FALSE от тишины.
            # Карта артефактов — независимое позитивное свидетельство, пишется
            # как раньше.
            if openers:
                telegram_routes.observe(
                    peer, kind="topic_opener_seen",
                    since_message_id=min(ids), until_message_id=max(ids),
                    detail=(f"миграция: пройдено {len(ids)}, авторитетных "
                            f"openers {openers} из {openers_total}"))
            if mapping:
                telegram_routes.observe_branches(peer, mapping)
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
#  2-3. пересборка и контрольная сумма
# --------------------------------------------------------------------------- #
def _summary_blocks(text: str) -> set[str]:
    return {block.strip() for block in str(text or "").split("\n\n")
            if block.strip().startswith("[compact")}


def snapshot_states(memory_life, only_peer: str | None = None,
                    telegram_routes=None):
    """Копия КАЖДОГО состояния до пересборки. Без неё откат — не откат.

    ⚠ `retire_split_states` отодвигает только осиротевшие ключи. А состояние ключа,
    который САМ является местом (у обычной комнаты это её корневой ключ), миграция
    перезаписывает сведённым кольцом — и его не сохранял никто. Проверено на копии
    живой памяти: 80 горячих событий до, 2760 после, 2760 же после «отката». Реестра
    при этом уже нет, значит корневой ключ несёт события 173 чужих веток, и следующая
    свёртка родила бы компакт, который не проходит собственную проверку привязки.
    """
    attic = memory_life.STATE_DIR / "_pre_places"
    if only_peer is None:
        if attic.exists():
            # ⚠ Второй прогон не дополняет снимок. Иначе в него попали бы файлы, которые
            # СОЗДАЛ первый прогон, и откат вернул бы не «как было», а промежуточное
            # состояние: и ветка, и место одновременно, то есть два курсора над одной
            # лентой (вторая адверсарка 25.07). Снимок делает только первый прогон — он и
            # есть «до».
            return 0
        attic.mkdir(parents=True, exist_ok=True)
        copied = 0
        for path in sorted(memory_life.STATE_DIR.glob("*.json")):
            (attic / path.name).write_bytes(path.read_bytes())
            copied += 1
        return copied
    # Канарейка (её P0 23.08 после живой AbstractDL): каждый прогон живёт в
    # СОБСТВЕННОМ каталоге `_pre_places/runs/<run_id>/` и коммитится собственным
    # manifest.json с точным списком baseline-файлов и их sha256. Плоский
    # legacy-чердак НЕ читается и НЕ пишется: правила «пропустить файл, потому
    # что одноимённый уже лежит» больше не существует — именно оно оставило
    # живую канарейку без «до» корневого состояния. Коллизия со старым чердаком
    # невозможна по построению: namespace прогона свеж (mkdir exist_ok=False —
    # столкновение даёт отказ ДО первой записи). Обрыв до коммита оставляет
    # каталог без manifest.json — такой черновик не база ни для кого, его никто
    # не читает и не чистит; новый прогон получает свежий run_id и ПОЛНУЮ базу
    # на момент старта. Каждая копия перечитывается и сверяется с оригиналом до
    # включения в manifest.
    if telegram_routes is None:
        raise ValueError("канарейный снимок требует telegram_routes: в базу "
                         "прогона входит и route.json (её контракт REPAIR4 п.4)")
    with _peer_lock(memory_life, only_peer):
        run_id = _new_run_id(only_peer)
        run_dir = _runs_root(memory_life) / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        files = []
        for path in sorted(memory_life.STATE_DIR.glob("*.json")):
            if not _peer_owns(path.stem, only_peer):
                continue
            data = path.read_bytes()
            target = run_dir / path.name
            target.write_bytes(data)
            written = target.read_bytes()
            if written != data:
                raise OSError(f"копия {path.name} не сошлась с оригиналом после записи")
            files.append({"name": path.name,
                          "sha256": hashlib.sha256(written).hexdigest(),
                          "bytes": len(written)})
        # route.json — тоже часть базы «до»: точное восстановление после провала
        # инвариантов обязано уметь вернуть и реестровое свидетельство байт-в-байт
        # (сайдкар .rev в базу не входит: durable-ревизия монотонна и не
        # откатывается — REPAIR5).
        route_path = telegram_routes.DIR / f"{only_peer}.route.json"
        if route_path.exists():
            data = route_path.read_bytes()
            baseline = run_dir / "route.json.baseline"
            baseline.write_bytes(data)
            if baseline.read_bytes() != data:
                raise OSError("копия route.json не сошлась с оригиналом после записи")
            route_meta = {"present": True,
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "bytes": len(data)}
        else:
            route_meta = {"present": False}
        # До-прогонный состав журнала привязок этого пира: восстановление после
        # провала обязано снять РОВНО добавленное прогоном, не тронув ни чужих
        # пиров, ни до-прогонных привязок (её REPAIR5: journal-binding recovery).
        bindings_before = sorted(key for key in memory_life.bindings()
                                 if _peer_owns(key, only_peer))
        _commit_run_manifest(run_dir, {
            "schema": _RUN_SCHEMA,
            "run_id": run_id,
            "peer": str(only_peer),
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "files": files,
            "route": route_meta,
            "bindings_before": bindings_before,
        })
        return len(files), run_id


def snapshot_before(memory_life, only_peer: str | None = None) -> dict:
    out = {}
    for key in memory_life.state_keys():
        if only_peer is not None and not _peer_owns(key, only_peer):
            continue
        canonical, _legacy = memory_life._canonical_compact_graph(key)
        out[key] = {
            "compacts": set(canonical),
            "blocks": _summary_blocks(memory_life.context_summary(key, max_chars=400_000)),
        }
    return out


def bind_places(memory_life, before: dict, only_peer: str | None = None) -> int:
    """Закрепить состав каждого места в неизменяемом журнале свёрток.

    Реестр маршрутов — знание, и оно может уточниться: ключ, отвечавший «эта комната»,
    после новой эпохи проваливается в `unknown`. Если бы достижимость исторических
    компактов зависела только от него, её сводка теряла бы блоки от каждого нового
    наблюдения. Журнал `places.json` пишется один раз и только растёт — поэтому состав
    места закрепляем здесь, а не оставляем на усмотрение будущих наблюдений.
    """
    members = collections.defaultdict(set)
    for key in before:
        members[memory_life.place_key(key)].add(key)
    root = memory_life.COMPACTS_DIR
    if root.exists():
        for item in sorted(root.iterdir()):
            if item.is_dir():
                if only_peer is not None and not _peer_owns(item.name, only_peer):
                    continue
                members[memory_life.place_key(item.name)].add(item.name)
    return sum(memory_life.bind_place(place, keys) for place, keys in members.items())


def verify(memory_life, before: dict) -> tuple[list[str], dict]:
    """Пересобрать места и проверить инварианты. -> (нарушения, сводка)."""
    problems: list[str] = []
    members = collections.defaultdict(list)
    for key in before:
        members[memory_life.place_key(key)].append(key)

    totals = {}
    for place, keys in sorted(members.items()):
        state = memory_life.rebuild_state(place)
        events = memory_life.iter_events(chat_id=place, kinds={"conversation_message"})
        canonical, _legacy = memory_life._canonical_compact_graph(place)

        every = {str(item.get("id")) for item in events}
        hot = {str(item.get("id")) for item in state.get("hot") or []}
        covered = {str(event) for meta, _recap in canonical.values()
                   for event in meta.get("source_event_ids") or []}
        lost = every - hot - covered
        both = hot & covered
        if lost:
            problems.append(f"{place}: потеряно {len(lost)} событий")
        if both:
            problems.append(f"{place}: {len(both)} событий одновременно горячие и свёрнутые")

        gone = set().union(*[before[key]["compacts"] for key in keys]) - set(canonical)
        if gone:
            problems.append(f"{place}: перестали быть каноническими {sorted(gone)}")

        after_blocks = _summary_blocks(
            memory_life.context_summary(place, max_chars=400_000))
        for key in keys:
            missing = before[key]["blocks"] - after_blocks
            if missing:
                problems.append(f"{place}: из сводки {key} пропало блоков {len(missing)}")

        totals[place] = {"keys": len(keys), "events": len(every), "hot": len(hot),
                         "compacts": len(canonical)}
    return problems, totals


def retire_scoped(memory_life, only_peer: str, run_dir: Path) -> dict:
    """Отодвинуть расщеплённые состояния ОДНОГО пира — в каталог СВОЕГО прогона.

    Список берётся у `retire_split_states(dry_run=True)` — вопрос «расщеплён ли
    ключ» задаёт ровно тот же код, что и глобальный путь; здесь только граница
    пира. Паркуются файлы в `runs/<run_id>/retired/`, а не в плоский чердак:
    перенос в общий каталог мог затереть одноимённый июльский архивный файл —
    чужую эпоху (её контракт №3: прогон не удаляет и не поднимает чужих
    архивных файлов).
    """
    roster = memory_life.retire_split_states(dry_run=True)
    parking = run_dir / "retired"
    moved = []
    for key in roster["moved"]:
        if not _peer_owns(key, only_peer):
            continue
        path = memory_life.STATE_DIR / f"{key}.json"
        if not path.exists():
            continue
        parking.mkdir(parents=True, exist_ok=True)
        try:
            path.replace(parking / path.name)
            moved.append(key)
        except OSError:
            pass
    kept = [key for key in roster["kept"] if _peer_owns(key, only_peer)]
    return {"moved": moved, "kept": kept}


# --------------------------------------------------------------------------- #
#  откат
# --------------------------------------------------------------------------- #
def rollback(memory_life, telegram_routes, only_peer: str | None = None,
             run_id: str | None = None) -> dict:
    """Вернуть группировку к тому, что было. Историю свёрток НЕ трогает — и не должна.

    Три действия: вернуть каждое состояние из снимка; убрать состояния, которых до
    миграции не существовало; удалить файлы реестра маршрутов.

    `memory/life/places.json` (журнал уже состоявшихся свёрток) остаётся на месте
    СОЗНАТЕЛЬНО. Откат отменяет наше знание о том, что считать одной комнатой, но не
    может отменить факт «эти события были свёрнуты вместе». Сотри журнал — и всякий
    компакт, написанный после миграции, перестанет быть каноническим, то есть её
    сводка молча потеряет блоки. Откат обязан быть безопаснее миграции, а не опаснее.
    """
    if only_peer is None:
        return _rollback_global(memory_life, telegram_routes)
    with _peer_lock(memory_life, only_peer):
        return _rollback_run(memory_life, telegram_routes, only_peer, run_id)


def _rollback_global(memory_life, telegram_routes) -> dict:
    """Глобальный откат июльской миграции — прежняя семантика, не менялась."""
    restored, removed, dropped = [], [], []
    attic = memory_life.STATE_DIR / "_pre_places"
    if attic.exists():
        keep = {path.name for path in attic.glob("*.json")}
        for path in sorted(memory_life.STATE_DIR.glob("*.json")):
            # Удаляем ТОЛЬКО состояния разговоров. `.state/life` — общий каталог: там же
            # лежит заявка на переплавку, которую ничем не восстановишь. Фильтр по
            # содержимому уже есть у `retire_split_states`; откат обязан спрашивать тот
            # же вопрос (вторая адверсарка 25.07).
            if path.name not in keep and memory_life.is_state_file(path):
                path.unlink()             # состояние, которого до миграции не было
                dropped.append(path.stem)
        for path in sorted(attic.glob("*.json")):
            path.replace(memory_life.STATE_DIR / path.name)
            restored.append(path.stem)
        try:
            (attic / "_canary_peers.txt").unlink(missing_ok=True)
            for stray in attic.glob("_canary_txn_*.txt"):
                stray.unlink(missing_ok=True)
            # runs/ — архивы канарейных прогонов; глобальный откат их не трогает,
            # attic с ними просто не удалится (rmdir упадёт и это нормально)
            attic.rmdir()
        except OSError:
            pass
    if telegram_routes.DIR.exists():
        for path in sorted(telegram_routes.DIR.glob("*.route.json")):
            path.unlink()
            removed.append(path.stem)
    return {"restored": restored, "registry_removed": removed, "dropped": dropped,
            "states_untouched": not restored and not dropped, "run_id": None,
            "runs_available": []}


def _rollback_run(memory_life, telegram_routes, peer, run_id) -> dict:
    """Откат ровно ОДНОГО закоммиченного canary-прогона (её контракт 23.08 №2).

    Работает ТОЛЬКО по manifest.json прогона: восстанавливаются ровно
    перечисленные в нём файлы, каждый предварительно сверен со своим sha256;
    «появившимся» считается только файл пира, которого нет в manifest. Плоский
    legacy-чердак не читается вовсе — glob'а по пиру не существует (живой
    инцидент: старый откат поднял бы 173 июльских файла чужой эпохи).
    Неоднозначность (несколько прогонов без --run) и незнакомый run_id — полный
    отказ без единой записи. Восстановление КОПИРУЕТ из архива прогона — снимок
    остаётся на месте, откат повторяем.
    """
    out = {"restored": [], "registry_removed": [], "dropped": [],
           "states_untouched": True, "run_id": None, "runs_available": [],
           "recovered_refused": None}
    runs = committed_runs(memory_life, peer)
    eligible = [m for m in runs if not m.get("_recovered")]
    out["runs_available"] = [str(m["run_id"]) for m in eligible]
    manifest = None
    if run_id is not None:
        manifest = next((m for m in runs if str(m["run_id"]) == str(run_id)), None)
        if manifest is None:
            return out                    # незнакомый прогон: полный отказ
        if manifest.get("_recovered"):
            # прогон уже восстановлен после провала инвариантов: его база уже
            # в живых состояниях, «откатывать» нечего — отказ, а не тихий no-op
            out["recovered_refused"] = str(run_id)
            return out
    elif len(eligible) == 1:
        manifest = eligible[0]
    elif len(eligible) > 1:
        return out                        # неоднозначность без --run: полный отказ
    if manifest is None:
        if not runs:
            # Прогонов нет ВОВСЕ — базы нет: состояния не трогаются, снимается
            # только реестровое свидетельство (идемпотентная отмена seed).
            _remove_peer_route(telegram_routes, peer, out)
        # Прогоны есть, но пригодных нет (все recovered): их route уже
        # возвращён восстановлением — трогать нечего, полный отказ.
        return out
    run_dir = _runs_root(memory_life) / str(manifest["run_id"])
    payload = _verified_payload(memory_life, manifest, run_dir, peer)
    if payload is None:
        return out                        # повреждённый manifest/снимок: отказ без записи
    # route-база сверяется ДО единой записи, вместе с состояниями
    route_meta = manifest.get("route")
    route_baseline = None
    if route_meta and route_meta.get("present"):
        try:
            route_baseline = (run_dir / "route.json.baseline").read_bytes()
        except OSError:
            return out                    # route-базы нет: отказ без записи
        if hashlib.sha256(route_baseline).hexdigest() != route_meta.get("sha256"):
            return out                    # route-база повреждена: отказ без записи
    out["states_untouched"] = False
    out["run_id"] = str(manifest["run_id"])
    _restore_states(memory_life, peer, payload, out)
    route_path = telegram_routes.DIR / f"{peer}.route.json"
    if route_meta is None:
        # наследный manifest без route-базы: прежняя семантика — снять файл
        _remove_peer_route(telegram_routes, peer, out)
        out["route"] = "removed_legacy"
    elif route_baseline is not None:
        # Откат возвращает route К БАЗЕ ПРОГОНА, а не удаляет его: удаление
        # теряло до-прогонные байты при доводке после обрыва (её REPAIR5).
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_bytes(route_baseline)
        out["route"] = "restored"
    else:
        if route_path.exists():
            route_path.unlink()
            out["registry_removed"].append(route_path.stem)
        out["route"] = "removed"
    receipt = _read_bindings_receipt(run_dir)
    bind_phase_torn = bool(receipt and receipt.get("status") in ("planned",
                                                                 "in_progress"))
    if (run_dir / "failed.json").exists():
        # Доводка восстановления провалившегося прогона: снять привязки с
        # доказанным авторством, затем пометить recovered.
        removed, unproven = _remove_run_bindings(memory_life, peer, run_dir)
        out["bindings_removed"] = removed
        if unproven:
            out["bindings_unproven"] = unproven
        _mark_recovered(run_dir, {
            "recovered_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "reason": "finish_after_crashed_recovery",
        })
        out["finished_failed_run"] = True
    elif bind_phase_torn:
        # Прогон оборвался ПОСРЕДИ фазы привязок (receipt не дошёл до
        # committed): его собственные, доказанно записанные пары снимаются —
        # упавшая канарейка не оставляет place-мутаций (её REPAIR8);
        # недоказуемое не трогаем и называем вслух.
        removed, unproven = _remove_run_bindings(memory_life, peer, run_dir)
        out["bindings_removed"] = removed
        if unproven:
            out["bindings_unproven"] = unproven
    elif "bindings_before" in manifest:
        # Завершённый прогон: журнал НЕ разматывается (адверсарка 25.07 — на
        # его привязках могут держаться компакты), но дельта называется вслух.
        before = set(manifest.get("bindings_before") or [])
        delta = sorted(key for key in memory_life.bindings()
                       if _peer_owns(key, peer) and key not in before)
        if delta:
            out["bindings_delta_warning"] = delta
    return out


def _verified_payload(memory_life, manifest, run_dir: Path, peer) -> dict | None:
    """Прочитать и сверить с manifest-хэшами ВСЕ baseline-файлы прогона ДО
    единой записи. Любое расхождение — None: отказ без побочных эффектов."""
    payload: dict[str, bytes] = {}
    for entry in manifest["files"]:
        name = str(entry.get("name") or "")
        if ("/" in name or "\\" in name or not name.endswith(".json")
                or not _peer_owns(Path(name).stem, peer)):
            return None
        try:
            data = (run_dir / name).read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            return None
        payload[name] = data
    return payload


def _restore_states(memory_life, peer, payload: dict, out: dict) -> None:
    keep = set(payload)
    for path in sorted(memory_life.STATE_DIR.glob("*.json")):
        if not _peer_owns(path.stem, peer):
            continue                      # чужой пир — канарейка его не трогает
        if path.name not in keep and memory_life.is_state_file(path):
            path.unlink()                 # состояние, которого не было в базе прогона
            out["dropped"].append(path.stem)
    for name, data in payload.items():
        (memory_life.STATE_DIR / name).write_bytes(data)
        out["restored"].append(Path(name).stem)


def recover_failed_run(memory_life, telegram_routes, peer, run_id,
                       problems) -> dict:
    """Точное восстановление ПОСЛЕ провала инвариантов (её контракт REPAIR4 п.4).

    Состояния пира возвращаются к базе прогона байт-в-байт (по manifest-хэшам),
    route.json — к своей базе из того же manifest (или снимается, если его не
    было). Сайдкар `.rev` НЕ откатывается: durable-ревизия монотонна (REPAIR5).
    Append-only журнал привязок НЕ разматывается СОЗНАТЕЛЬНО: раз-привязка задним
    числом — ровно тот способ потерять канонические компакты, против которого
    журнал заведён (адверсарка 25.07); это названное исключение, не забывчивость.
    Успех помечается `recovered.json` — прогон перестаёт быть «пригодной
    канарейкой» и уходит из откатной выборки. Обрыв ДО пометки оставляет прогон
    пригодным: явный `--rollback --peer --run <id>` идемпотентно доводит то же
    восстановление состояний.
    """
    out = {"restored": [], "registry_removed": [], "dropped": [],
           "route": "untouched", "refused": None, "run_id": str(run_id)}
    run_dir = _runs_root(memory_life) / str(run_id)
    manifest = _read_run_manifest(run_dir)
    if manifest is None or str(manifest.get("peer")) != str(peer):
        out["refused"] = "manifest прогона не читается"
        return out
    payload = _verified_payload(memory_life, manifest, run_dir, peer)
    if payload is None:
        out["refused"] = "baseline-файлы не сходятся с manifest-хэшами"
        return out
    route_meta = manifest.get("route") or {"present": False}
    route_baseline = None
    if route_meta.get("present"):
        try:
            route_baseline = (run_dir / "route.json.baseline").read_bytes()
        except OSError:
            out["refused"] = "route-база прогона не читается"
            return out
        if hashlib.sha256(route_baseline).hexdigest() != route_meta.get("sha256"):
            out["refused"] = "route-база не сходится с manifest-хэшем"
            return out
    _restore_states(memory_life, peer, payload, out)
    route_path = telegram_routes.DIR / f"{peer}.route.json"
    if route_baseline is not None:
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_bytes(route_baseline)
        out["route"] = "restored"
    else:
        if route_path.exists():
            route_path.unlink()
        out["route"] = "removed"
    # Журнал привязок: снимаются ТОЛЬКО записи с доказанным авторством (факт
    # или per-entry прогресс/sha-свидетель) — чужая параллельная привязка,
    # включая одноимённую, недостижима (её REPAIR5..REPAIR8).
    removed, unproven = _remove_run_bindings(memory_life, peer, run_dir)
    out["bindings_removed"] = removed
    if unproven:
        out["bindings_unproven"] = unproven
    _mark_recovered(run_dir, {
        "recovered_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "reason": "invariant_failure",
        "problems": [str(line) for line in problems],
    })
    return out


def _remove_peer_route(telegram_routes, peer, out: dict) -> None:
    if telegram_routes.DIR.exists():
        path = telegram_routes.DIR / f"{peer}.route.json"
        if path.exists():
            path.unlink()
            out["registry_removed"].append(path.stem)
        # `<peer>.route.rev` остаётся СОЗНАТЕЛЬНО: durable-ревизия (REPAIR5)
        # монотонна на всю жизнь комнаты — откат группировки не должен давать
        # токену вторую жизнь, иначе живой процесс примет снимок из прошлой.


def _canary_dry_report(memory_life, before: dict, peer: str) -> None:
    """Что ИМЕННО тронул бы настоящий прогон этой канарейки. Только чтение."""
    places: dict[str, list[str]] = collections.defaultdict(list)
    for key in before:
        places[memory_life.place_key(key)].append(key)
    collapsing = sorted(key for place, keys in places.items() for key in keys
                        if key != place)
    roster = memory_life.retire_split_states(dry_run=True)
    would_retire = sorted(key for key in roster["moved"] if _peer_owns(key, peer))
    bound = memory_life.bindings()
    new_binds = sorted(key for place, keys in places.items() for key in keys
                       if key != place and key not in bound)
    conflicts = sorted(f"{key}: журнал держит {bound[key]}, реестр считает {place}"
                       for place, keys in places.items() for key in keys
                       if key in bound and bound[key] != place)
    attic = memory_life.STATE_DIR / "_pre_places"
    peer_states = sorted(k for k in memory_life.state_keys() if _peer_owns(k, peer))
    runs = committed_runs(memory_life, peer)
    runs_root = _runs_root(memory_life)
    partial = sorted(d.name for d in runs_root.iterdir()
                     if d.is_dir() and d.name.endswith(f"-{peer}")
                     and _read_run_manifest(d) is None) if runs_root.exists() else []
    legacy = sorted(p.name for p in attic.glob("*.json")
                    if _peer_owns(p.stem, peer)) if attic.exists() else []

    print(f"\n--- канарейка {peer}: что тронет настоящий прогон ---")
    print(f"состояний пира: {len(peer_states)}; мест после сведения: {len(places)}; "
          f"ключей, уезжающих в место: {len(collapsing)}")
    print(f"отодвинется в _pre_places: {len(would_retire)}"
          + (f" ({', '.join(would_retire[:6])}{'…' if len(would_retire) > 6 else ''})"
             if would_retire else ""))
    print(f"новых привязок в memory/life/places.json: {len(new_binds)}")
    if conflicts:
        print(f"⚠ конфликтов журнал-против-реестра: {len(conflicts)} — журнал сильнее, "
              f"привязка НЕ переписывается:")
        for line in conflicts[:8]:
            print(f"    {line}")
    else:
        print("конфликтов журнал-против-реестра: 0")
    unresolved = sorted(key for key in peer_states
                        if key != peer and key not in bound
                        and memory_life.place_key(key) == key)
    if unresolved:
        print(f"⚠ ключей вне журнала, чьё место может уточниться ПОСЛЕ свидетельств "
              f"этого же прогона: {len(unresolved)} "
              f"({', '.join(unresolved[:6])}{'…' if len(unresolved) > 6 else ''}) — "
              f"сухой прогон меряет по сегодняшнему реестру; настоящий прогон сначала "
              f"запишет свидетельства архива, и группировка пересчитается")
    recovered = [m for m in runs if m.get("_recovered")]
    print(f"закоммиченных canary-прогонов этого пира: {len(runs)}"
          + (f" ({', '.join(str(m['run_id']) for m in runs[-3:])})" if runs else "")
          + (f"; из них восстановленных после провала инвариантов: "
             f"{len(recovered)} — канарейками не являются" if recovered else "")
          + ("; настоящий прогон создаст НОВЫЙ run со своей полной базой" if runs
             else ""))
    if partial:
        print(f"⚠ незакоммиченных черновиков прогона: {len(partial)} — базой не "
              f"являются, настоящий прогон их не читает и не чистит")
    if legacy:
        print(f"плоский legacy-чердак: {len(legacy)} файлов этого пира (июльская "
              f"эпоха) — канарейка их НЕ читает и НЕ трогает")
    print(f"файлы на запись настоящего прогона: _pre_places/runs/<run_id>/ "
          f"(полная база состояний + route.json.baseline + manifest.json c "
          f"sha256 + retired/), {peer}.route.json (+ .route.rev через _save), "
          f"состояния мест пира, memory/life/places.json")
    print("(инварианты в сухом прогоне не проверяются: их проверка требует "
          "пересборки состояний — это уже запись)")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Миграция ключа разговора в места")
    parser.add_argument("--base", default=os.environ.get("PRAXIS_BASE") or ".")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="ничего не писать: только показать, что получится")
    mode.add_argument("--rollback", action="store_true")
    parser.add_argument("--peer", default=None,
                        help="канарейка: ограничить прогон одной комнатой, "
                             "например --peer -1001240718803")
    parser.add_argument("--run", default=None,
                        help="откат канарейки: run_id закоммиченного прогона "
                             "(обязателен, если прогонов у пира больше одного)")
    args = parser.parse_args(argv)

    base = Path(args.base).resolve()
    group_context, memory_life, telegram_routes = _modules(base)

    if args.run is not None and not (args.rollback and args.peer):
        print("--run имеет смысл только вместе с --rollback --peer")
        return 2

    if args.peer is not None:
        known = archive_peers(group_context)
        if args.peer not in known:
            print(f"пир {args.peer} не найден среди архивов комнат; знаю: "
                  + (", ".join(known) if known else "ни одного"))
            return 2

    if args.rollback:
        out = rollback(memory_life, telegram_routes, only_peer=args.peer,
                       run_id=args.run)
        scope = f" (канарейка {args.peer})" if args.peer else ""
        print(f"откат{scope}: возвращено состояний {len(out['restored'])}, "
              f"убрано появившихся {len(out['dropped'])}, "
              f"удалено файлов реестра {len(out['registry_removed'])}")
        print("журнал свёрток memory/life/places.json оставлен намеренно: "
              "он держит уже написанные компакты каноническими")
        if args.peer:
            print(f"сайдкар {args.peer}.route.rev оставлен намеренно: durable-ревизия "
                  f"монотонна на всю жизнь комнаты")
            if out.get("run_id"):
                print(f"откат по manifest прогона {out['run_id']}; архив прогона "
                      f"остаётся на месте (восстановление копированием); "
                      f"route: {out.get('route')}")
                if out.get("finished_failed_run"):
                    print(f"это была доводка провалившегося прогона: снято "
                          f"привязок дельты {len(out.get('bindings_removed') or [])}; "
                          f"прогон помечен recovered")
                if out.get("bindings_delta_warning"):
                    print(f"⚠ привязки, добавленные после базы прогона, оставлены "
                          f"(журнал успешного прогона не разматывается — "
                          f"адверсарка 25.07): "
                          f"{', '.join(out['bindings_delta_warning'][:6])}")
            elif out.get("recovered_refused"):
                print(f"ОТКАЗ: прогон {out['recovered_refused']} уже восстановлен "
                      f"после провала инвариантов — его база уже в живых "
                      f"состояниях, откатывать нечего; ни одной записи не сделано")
                return 2
            elif out.get("states_untouched"):
                runs = out.get("runs_available") or []
                if args.run is not None:
                    print(f"ОТКАЗ: прогон {args.run} не найден среди закоммиченных "
                          f"({', '.join(runs) if runs else 'нет ни одного'}); "
                          f"ни одной записи не сделано")
                    return 2
                if len(runs) > 1:
                    print(f"ОТКАЗ: у пира {len(runs)} закоммиченных прогонов — "
                          f"укажи --run из: {', '.join(runs)}; ни одной записи "
                          f"не сделано")
                    return 2
                print("состояния НЕ тронуты: закоммиченных canary-прогонов нет — "
                      "базы нет; снято только реестровое свидетельство; плоский "
                      "legacy-чердак канарейный откат не читает")
        return 0

    print(f"=== база: {base}"
          + (f" · канарейка {args.peer}" if args.peer else "") + " ===")
    before = snapshot_before(memory_life, only_peer=args.peer)
    print(f"ключей состояния до: {len(before)}; "
          f"канонических компактов: {len(set().union(*[v['compacts'] for v in before.values()]) if before else set())}")

    run_id = None
    if args.peer is not None and not args.dry_run:
        # Снимок ДО seed_registry: route-база прогона обязана быть ДО-прогонной,
        # иначе восстановление после провала инвариантов вернуло бы route с уже
        # записанными свидетельствами этого же прогона (её REPAIR4 п.4).
        copied, run_id = snapshot_states(memory_life, only_peer=args.peer,
                                         telegram_routes=telegram_routes)
        print(f"снимок состояний до пересборки: {copied} файлов + route-база; "
              f"canary-прогон {run_id} (manifest c sha256 закоммичен)")

    for row in seed_registry(group_context, telegram_routes, dry_run=args.dry_run,
                             only_peer=args.peer):
        print(f"  {row['peer']}: {row['messages']} сообщений, {row['branches']} веток, "
              f"{row['verdict']}, наших псевдоветок {row['artifacts']}")

    if args.dry_run:
        if args.peer is not None:
            _canary_dry_report(memory_life, before, args.peer)
        print("\n(сухой прогон: реестр не записан, состояния не пересобраны)")
        return 0

    if args.peer is None:
        print(f"снимок состояний до пересборки: "
              f"{snapshot_states(memory_life)} файлов")
    if args.peer is None:
        print(f"закреплено привязок ключ→место: "
              f"{bind_places(memory_life, before)}")
    else:
        print(f"закреплено привязок ключ→место (с receipt авторства): "
              f"{_bind_places_with_receipt(memory_life, before, args.peer, _runs_root(memory_life) / run_id)}")
    problems, totals = verify(memory_life, before)
    print(f"\nмест после сведения: {len(totals)}")
    for place, row in sorted(totals.items(), key=lambda kv: -kv[1]["events"])[:8]:
        print(f"  {place}: ключей {row['keys']}, событий {row['events']}, "
              f"горячих {row['hot']}, свёрнутых блоков {row['compacts']}")

    if problems:
        print("\nИНВАРИАНТЫ НАРУШЕНЫ:")
        for line in problems:
            print(f"  ! {line}")
        if run_id is None:
            print("состояния оставлены на месте (глобальный прогон — прежняя "
                  "семантика), откат не нужен")
            return 1
        # Канарейка транзакционна на своём уровне (её контракт REPAIR4 п.4 +
        # REPAIR5): провал инвариантов не оставляет снаружи НИ state-, НИ route-,
        # НИ place-мутации — точное восстановление из manifest этого же прогона.
        # failed-пометка ложится ДО восстановления: обрыв в любой его точке
        # оставляет failed без recovered, и явная доводка знает, что делать.
        _mark_failed(_runs_root(memory_life) / run_id, {
            "failed_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "reason": "invariant_failure",
            "problems": [str(line) for line in problems],
        })
        rec = recover_failed_run(memory_life, telegram_routes, args.peer,
                                 run_id, problems)
        if rec.get("refused"):
            print(f"ВОССТАНОВЛЕНИЕ НЕ ВЫПОЛНЕНО: {rec['refused']}; прогон "
                  f"остаётся пригодным — доведи явным: python migrate_places.py "
                  f"--rollback --peer {args.peer} --run {run_id}")
            return 1
        print(f"точное восстановление из manifest {run_id}: состояний "
              f"{len(rec['restored'])}, route {rec['route']}, снято привязок по "
              f"receipt прогона {len(rec.get('bindings_removed') or [])}; прогон "
              f"помечен recovered — пригодной канарейкой не является, из "
              f"откатной выборки исключён")
        print("журнал places.json: сняты ТОЛЬКО записи с авторством, доказанным "
              "фактом собственной записи (receipt-факт); до-прогонные, чужие, "
              "параллельные и одноимённые внешние привязки не тронуты; сайдкар "
              ".route.rev монотонен и не откатывается (REPAIR5)")
        if rec.get("bindings_unproven"):
            print(f"⚠ receipt остался намерением ({rec['bindings_unproven']} "
                  f"ключ(ей)): обрыв случился посреди привязок — авторство "
                  f"недоказуемо, журнал не тронут, решение руками")
        return 1

    if args.peer is None:
        retired = memory_life.retire_split_states()
        print(f"\nинварианты целы: событий не потеряно, компактов не потеряно, "
              f"блоков сводки не потеряно")
        print(f"состояний отодвинуто в _pre_places: {len(retired['moved'])}; "
              f"осталось живых: {len(retired['kept'])}")
        print("откат: python migrate_places.py --rollback")
    else:
        retired = retire_scoped(memory_life, args.peer,
                                _runs_root(memory_life) / run_id)
        print(f"\nинварианты целы: событий не потеряно, компактов не потеряно, "
              f"блоков сводки не потеряно")
        print(f"состояний отодвинуто в runs/{run_id}/retired: "
              f"{len(retired['moved'])}; осталось живых: {len(retired['kept'])}")
        print(f"откат: python migrate_places.py --rollback --peer {args.peer} "
              f"--run {run_id}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
