# -*- coding: utf-8 -*-
"""Подача конфига продукта её дереву: раскладка, ручки среды, мозг.

Один принцип владельца — ПОЛНАЯ КОНФИГУРИРУЕМОСТЬ — и один файл настроек рядом с exe
(`helene.json`). Дерево агента конфигурируется 321 env-ручкой и своим `memory/llm.json`;
здесь ровно шов между ними: продукт кладёт значения ТУДА, где дерево их и читает,
вместо второй реализации тех же решений.

⚠⚠ ЛОВУШКА ПОРТА, НАЙДЕННАЯ ЖИВЬЁМ 30.08. `agent.py` на импорте зовёт
`load_dotenv(override=True)`, а `find_dotenv()` ищет `.env` ВВЕРХ ОТ ФАЙЛА ДЕРЕВА —
то есть рядом с кодом. На машине разработчика там лежит боевой `.env` (ключи, сессия
Telegram, поднятые рычаги), и локальный харнесс молча поднимался с ним: рук стало 100
вместо 95, рычаг речи оказался поднят «сам». В продукте такого файла не будет, и
поведение разошлось бы с отлаженным здесь.

Отсюда правило: ручки применяются ДВАЖДЫ — до импорта (их читают на импорте) и ПОСЛЕ
(чтобы случайный `.env` рядом с кодом не победил конфиг продукта). Найденный `.env`
называется в логе вслух: тихая подмена настроек — не мелочь, а класс.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import sys
import platform
import re
import time
from pathlib import Path

log = logging.getLogger("frame.boot")

# Порт как есть: значения рычагов = боевые (live/.env прода на 30.08). Продукт не
# изобретает своего поведения — он повторяет то, на котором она живёт.
#   PRAXIS_CHAT_REPLY_HAND — контракт v3: её реплика уходит РУКОЙ `reply`, а текст
#     хода это заметка. Опущенный рычаг вернул бы «последний текст = сообщение».
#   PRAXIS_WORK_LOOP + CONTINUATIONS — ход не кончается на первом тексте, закрывает
#     его её `end_turn`.
#   PRAXIS_FRAME_SHADOW — захват кадра (экран «Кадр» Пульта живёт на этих слепках).
# Веб-поиск по умолчанию опущен: hosted-рука зависит от провайдера, а продукт обязан
# подниматься на любом OAI-совместимом эндпойнте, включая локальную модель.
#   ⚠ 03.09. Отсюда убрана ручка `PRAXIS_EVALUATOR: risky`: её не читает НИ ОДНА
#     строка дерева (grep по live/ даёт только сам .env автора). Она приехала
#     слепком боевого .env и доказывала, что этот набор — не выбор продукта.
#     Остальные значения перепроверены и оставлены сознательно; LAST_N=80 против
#     умолчания дерева 50 — продуктовое решение владельца, не наследство.
PORT_DEFAULTS: dict[str, str] = {
    "PRAXIS_WORK_LOOP": "on",
    "PRAXIS_WORK_CONTINUATIONS": "8",
    "PRAXIS_CHAT_REPLY_HAND": "on",
    # ⚠ 17.09, ПО ЗАМЕРУ. Её прошлые реплики в ленте кадра лежали обычной прозой — это
    # единственный образец ассистентского хода во всём кадре, и модель ему следовала:
    # писала готовый ответ текстом вместо вызова руки `reply`, а текст никуда не уезжал.
    # Замер ядра 16.09 на пяти кадрах: 34/48 = 71 % таких ходов на базе, 0/42 под этим
    # рычагом (Фишер p = 3e-13). По моделям на ОДНОМ кадре: gpt-5.6-terra 0 %, sol 4 %,
    # astra 12 %, glm-5.3 47 %, glm-5.3-flash 67 % — то есть у поставки, указывающей
    # умолчанием на OpenAI, дефекта почти нет, а у того, кто поставит GLM, молчит каждый
    # второй ход. Цена обратная ожидаемой: вызов стоит 559 выходных токенов против 1860
    # у прозы. Умолчание самого дерева остаётся `off` — это гарантия отката.
    "PRAXIS_FRAME_TAPE_HANDS": "on",
    "PRAXIS_FRAME_SHADOW": "on",
    # ⚠ РЕТЕНЦИЯ ПРОГОНОВ, 17.09. Снимки `results/` — то, что уехало в модель и что
    # она ответила, по одному на итерацию: на проде это 28,7 ГБ из 29,5 ГБ всех
    # прогонов и +1 ГБ в сутки. У человека дома этому расти некуда. Манифесты и
    # события не трогаются никогда — история «что было» остаётся целиком.
    # Тик заводит `runner._retention_forever`: в издании звать ретенцию больше
    # неоткуда, `sleep.run_scheduled` здесь не работает.
    "PRAXIS_RUNS_RETENTION": "on",
    "PRAXIS_RUNS_RETENTION_BUDGET": "600",
    # ⚠ УКАЗАТЕЛИ РУК, 18.09. В кадр едут схемы тридцати родных рук плюс `describe` и
    # `call`; остальные семьдесят с лишним — строкой «имя(аргументы) — назначение», а
    # схема по требованию. Замер ядра 12.09: полный манифест 101 руки = 73 451 знак,
    # 44 % хода в личке и 78 % кадра. Указатель весит около шести тысяч.
    # ⚠ Включается ТОЛЬКО потому, что текст указателя в издании английский
    # (`tool_text_en.pointer_*`). В ядре он русский, и там это отменило бы английские
    # описания 86 рук из 95 — то есть «Кадр — да» съело бы «Англ — да».
    "PRAXIS_TOOLS_POINTERS": "on",
    "PRAXIS_LAST_N": "80",
    "PRAXIS_CONTEXT_BUDGET": "0",
    # ⚠ Два рычага кэша, поднятые 03.09 ПО ЗАМЕРУ на этой поставке. Стабильная
    # «голова» кадра расходилась на 2798-м токене (счётчики uptime/расхода и
    # запись прошлого хода живут внутри неё), и весь хвост системного сообщения
    # плюс лента платились свежими на каждом ходе. Замер двух ходов, снятых
    # живьём с интервалом в минуты:
    #     без рычагов — общий префикс 11617 из 14232 знаков (81,6 %)
    #     с рычагами  — 13934 из 13934 (100 %, байт в байт)
    # Счётчики при этом НЕ исчезают: они переезжают в живой evidence-блок —
    # проверено, uptime_minutes/tokens_today/latest_turn/skips_today на месте.
    # В ядре рычаги опущены по решению ДРУГОГО агента про ЕГО собственный кадр;
    # кадр агента этого продукта собирает продукт, и это его решение.
    "PRAXIS_STATE_COUNTERS_SPLIT": "1",
    "PRAXIS_FRAME_TAIL_SPLIT": "1",
    "PRAXIS_AUTO_RECALL_K": "0",
    "PRAXIS_EMBEDDINGS": "0",
    "PRAXIS_WEB_SEARCH": "0",
}

# Каноническая конституция продукта — ресурс рядом с кодом (resources/SOUL.md).
# Владелец читает, правит и принимает её при установке; сюда она приходит уже
# принятой. Имена подставляются здесь, чтобы у ресурса был один текст на всех.
_RESOURCES = Path(__file__).resolve().parent.parent / "resources"
_SOUL_CANON = _RESOURCES / "SOUL.md"
_SOUL_FALLBACK = """# Конституция

## Кто я

Меня зовут {{agent}}. Я агент, собранный в Hélène и живущий на компьютере {{owner}}.

## Кому я верен

Мой владелец — {{owner}}. При конфликте указаний решает это слово.
"""


def agent_name(cfg: dict) -> str:
    """Имя агента даёт владелец при установке: `agent.name`.

    Старые конфиги держали его в `telegram.agent_name` — читаем и оттуда, чтобы
    не терять имя при обновлении. Пустое имя — не «Hélène» и не чужое имя, а
    честное «Агент»: продукт не подписывает агента именем, которого ему не давали.
    """
    agent = cfg.get("agent") or {}
    telegram = cfg.get("telegram") or {}
    name = str(agent.get("name") or telegram.get("agent_name") or "").strip()
    return name or "Агент"


def owner_name(cfg: dict) -> str:
    return str((cfg.get("owner") or {}).get("name") or "").strip() or "владелец"


def _names(text: str, cfg: dict) -> str:
    """Подстановка имён — одна на конституцию и на весь стартовый комплект."""
    return text.replace("{{agent}}", agent_name(cfg)).replace("{{owner}}",
                                                              owner_name(cfg))


def soul_text(cfg: dict) -> str:
    """Каноническая конституция с подставленными именами."""
    try:
        canon = _SOUL_CANON.read_text(encoding="utf-8")
    except OSError:
        log.warning("ресурса конституции нет (%s) — пишу короткий запасной текст",
                    _SOUL_CANON)
        canon = _SOUL_FALLBACK
    return _names(canon, cfg)


def read_config_text(path: Path) -> str:
    """helene.json с диска — как его сохранил РЕДАКТОР ВЛАДЕЛЬЦА, а не как хочется нам.

    ⚠ ПЕРВЫЙ-ЗАПУСК.md зовёт править этот файл руками, а Блокнот, VS Code и
    `Set-Content` из PowerShell 5.1 по умолчанию пишут UTF-8 с BOM или UTF-16LE.
    Строгий `read_text(encoding="utf-8")` ронял руннер на первом же байте
    («Unexpected UTF-8 BOM», «codec can't decode byte 0xff»), при том что
    оболочка тот же файл уже читает (она BOM снимает и UTF-16 понимает) —
    считала продукт настроенным, поднимала детей и перезапускала умершего
    руннера по кругу, а в окне стояло «Модель не настроена».

    Читаем то же, что и оболочка: BOM UTF-8, UTF-16 с меткой и без неё.
    """
    raw = Path(path).read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")          # метку разберёт сам кодек
    # UTF-16 БЕЗ метки проходит `decode("utf-8")` насквозь (нулевой байт — законный
    # символ UTF-8), и разбор JSON падал уже на второй букве. В JSON нулей не бывает:
    # нашли — значит это широкая кодировка, а не текст.
    if b"\x00" in raw[:4096]:
        for encoding in ("utf-16-le", "utf-16-be"):
            try:
                text = raw.decode(encoding)
            except UnicodeDecodeError:
                continue
            if "{" in text:                  # похоже на JSON, а не на случайность
                return text
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("файл не читается ни как UTF-8, ни как UTF-16 — "
                         f"пересохрани его в UTF-8 ({exc})") from exc


class LayoutError(RuntimeError):
    """Раскладку завести не удалось — причина в тексте, для лога и для оболочки."""


def _seed_kit(tree: Path, cfg: dict) -> int:
    """Стартовый комплект из `resources/**` — в дерево, ТОЛЬКО чего там ещё нет.

    ⚠ Здесь писался РОВНО ОДИН файл — `soul/SOUL.md`. Поэтому конституция,
    ссылающаяся на соседей (soul/VOICE.md, soul/FRAME.md, soul/skills/INDEX.md),
    на первом же ходе врала про собственный дом: агент шёл читать названное и
    не находил ничего. Любой стартовый комплект лежал в поставке мёртвым грузом.

    Правило «только если нет» — то же, что у конституции, и оно тут главное:
    навык, который агент под себя переписал, обновление продукта молча вернуло
    бы к заводскому. Один упрямый файл (занят, нет прав) не роняет раскладку —
    он называется в логе, остальные едут.
    """
    planted = 0
    for src_root, dst_root in ((_RESOURCES / "soul", tree / "soul"),
                               (_RESOURCES / "memory", tree / "memory")):
        if not src_root.is_dir():
            continue
        for src in sorted(src_root.rglob("*")):
            rel = src.relative_to(src_root)
            if src.is_dir() or any(part.startswith((".", "__")) for part in rel.parts):
                continue
            dst = dst_root / rel
            if dst.exists():
                continue
            try:
                data = src.read_bytes()
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    dst.write_bytes(data)    # не текст — кладём байтами
                else:
                    dst.write_text(_names(text, cfg), encoding="utf-8", newline="\n")
            except OSError as exc:
                log.warning("стартовый файл %s не лёг в дерево: %s", rel, exc)
                continue
            planted += 1
    return planted


_SELF_DAY_ZERO = _RESOURCES / "self-day-zero.md"


def seed_self(tree: Path, cfg: dict) -> bool:
    """Запись о себе в день ноль — в кадр, через `self_model` с провенансом.

    Зона K кадра — три файла: конституция, голос и `soul/self/CURRENT.md`.
    Первые два — обычный markdown, их кладёт `_seed_kit`. Третий кадр читает
    только с провенанс-шапкой (`self_model.current_prompt` возвращает пустоту
    на файле без неё), поэтому положить его копией нельзя: он ляжет мёртвым, и
    в кадре на этом месте будет дыра. Единственный законный вход — `migrate`:
    письмо `soul/self.md` уходит в `history/0000.md` как самая первая версия,
    а текст из `resources/self-day-zero.md` становится CURRENT ревизии 0.

    Идемпотентно: при живом CURRENT ничего не делает — правку агента обновление
    продукта не затирает. `self_model` живёт в `tree/`, и путь туда руннер
    вставляет в `sys.path` до раскладки; здесь импорт ленивый, чтобы раскладка
    без ядра (тесты, чужой запуск) не падала на нём.
    """
    legacy = tree / "soul" / "self.md"
    if not legacy.is_file() or not _SELF_DAY_ZERO.is_file():
        return False
    try:
        import self_model  # noqa: WPS433 — из tree/, см. докстринг
    except ImportError:
        # ⚠ Руннер вставляет путь к коду в sys.path ПОСЛЕ раскладки (в
        # `_import_agent`), и на первом рождении установленной копии self_model
        # не находился — запись о себе молча не ложилась, кадр жил с дырой.
        # Кандидаты: код рядом с папкой программы (поставка: `<корень>/tree`),
        # дерево разработки (`../live`), и явный HELENE_CODE.
        root = _RESOURCES.parent.parent
        for cand in (os.environ.get("HELENE_CODE") or "", root / "tree", root.parent / "live"):
            cand = Path(cand) if cand else None
            if cand and (cand / "self_model.py").is_file():
                sys.path.insert(0, str(cand))
                break
        try:
            import self_model  # noqa: WPS433
        except Exception as exc:
            log.warning("запись о себе не легла: self_model не импортируется (%s)", exc)
            return False
    except Exception as exc:
        log.warning("запись о себе не легла: self_model не импортируется (%s)", exc)
        return False
    try:
        if self_model.current_prompt_info(tree).source == "current":
            return False
        text = _names(_SELF_DAY_ZERO.read_text(encoding="utf-8"), cfg)
        result = self_model.migrate(
            base=tree,
            reason="день ноль: запись о себе положена теми, кто собрал дом; "
                   "агент переписывает её первой",
            evidence_refs=["soul/SOUL.md", "soul/self.md"],
            compact_text=text,
            by="helene",
            confidence="uncertain",
            trigger="birth",
        )
    except Exception:
        log.exception("запись о себе не легла в дерево")
        return False
    if not result.get("ok"):
        log.warning("запись о себе не легла: %s", result.get("error"))
        return False
    return bool(result.get("migrated"))


# --------------------------------------------------------------------------- #
#  Личный git агента
# --------------------------------------------------------------------------- #

# Что НЕ снимок его работы: память и журналы ведёт код дерева на каждом ходе,
# ключи (memory/llm.json, relay/local_auth) в историю не кладут никогда,
# смонтированное (workspace/mnt) — чужие папки владельца, модели голоса
# (models/) — снаряжение машины весом в гигабайты.
_GIT_IGNORE = """\
# Личный репозиторий агента: снимки ЕГО правок — конституции, голоса, записи о
# себе, навыков, рабочих файлов. Память, журналы, ключи и смонтированное —
# не снимки: их ведёт код дерева, а ключам в истории не место.
memory/
relay/
telegram/
body/
# Модели голоса: полтора гигабайта снаряжения машины. В снимках правок агента им
# не место — и попади они туда, личный репозиторий распух бы до неподъёмного.
models/
*.log
*.sqlite3
*.sqlite3-*
*.session
*.session-journal
workspace/.fence/
workspace/.tmp/
workspace/mnt/
workspace/media/
__pycache__/
"""


#: Строки, без которых личный git агента набирает то, чему в снимках не место.
#: Проверяются и в СУЩЕСТВУЮЩЕМ файле: он пишется один раз при заведении
#: репозитория, а список с тех пор пополнялся — и у того, кто поставил продукт
#: раньше, в снимки уехали бы гигабайты моделей голоса (снимок делает `add -A`).
_IGNORE_MUST = ("memory/", "relay/", "telegram/", "body/", "models/",
                "workspace/mnt/", "workspace/.fence/", "workspace/.tmp/")


def _top_up_ignore(path: Path) -> list[str]:
    """Дописать в существующий `.gitignore` то, чего в нём не хватает.

    Файл не переписывается целиком намеренно: агент дописывает в него своё, и
    затирать его правки ради нашей строки нельзя.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    have = {line.strip() for line in text.splitlines()}
    missing = [item for item in _IGNORE_MUST if item not in have]
    if not missing:
        return []
    head = ("\n# Дописано продуктом: этих строк не было, а без них в снимки правок\n"
            "# агента попадает то, что снимком его работы не является.\n")
    try:
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            if not text.endswith("\n"):
                fh.write("\n")
            fh.write(head + "\n".join(missing) + "\n")
    except OSError as exc:
        log.warning("личный git: .gitignore не дополнен (%s): %s", path, exc)
        return []
    log.info("личный git: в .gitignore дописано %s", ", ".join(missing))
    return missing


def git_exe() -> Path | None:
    """git из поставки — `runtime/git/cmd/git.exe` (MinGit, см. build_dist.stage_git)."""
    bundled = _RESOURCES.parent.parent / "runtime" / "git" / "cmd" / "git.exe"
    return bundled if bundled.is_file() else None


def arm_git() -> str:
    """Вставить git поставки в PATH этого процесса — ПЕРВЫМ.

    `selfgit` дерева зовёт голое `git`, а `runner._git_state` спрашивает
    `shutil.which("git")`; без этой строки поставка с git в runtime/git всё
    равно считала бы, что git нет. Первым — чтобы снимки делал наш git, а не
    что нашлось на машине. -> откуда git: «поставка» | «система» | «».
    """
    import shutil
    exe = git_exe()
    if exe is not None:
        here = str(exe.parent)
        if here not in os.environ.get("PATH", "").split(os.pathsep):
            os.environ["PATH"] = here + os.pathsep + os.environ.get("PATH", "")
        return "поставка"
    return "система" if shutil.which("git") else ""


def _agent_email(cfg: dict) -> str:
    """Адрес автора снимков: латиница из имени или `agent`, домен — helene.local."""
    plain = agent_name(cfg).lower().encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "", plain)
    return f"{slug or 'agent'}@helene.local"


def seed_git(tree: Path, cfg: dict) -> bool:
    """Личный репозиторий агента в дереве данных — один раз, при рождении дома.

    Кадр дерева обещает агенту автокоммит правок (`selfgit.snapshot`) и
    страховочные снимки перед shell (`selfgit.safety_point`); оба молчат без
    репозитория в BASE. Здесь он и заводится: `git init` в дереве данных, игнор
    на память и ключи, автор — сам агент, первый снимок — дом в день ноль.
    Слово владельца 06.09: «git как в Уроборосе, из коробки, личный для агента».

    Идемпотентно: живой `.git` не трогаем. Без git (ни поставки, ни системы) —
    честное предупреждение в лог, кадру об этом скажет `runner._announce_git`.
    Полурепозиторий (init прошёл, снимок нет) сносится, чтобы следующий старт
    попробовал заново, а не жил с HEAD без коммита, на котором safety_point
    молча возвращает None.
    """
    import shutil
    import subprocess
    if (tree / ".git").exists():
        return False
    if not shutil.which("git"):
        log.warning("личный git агента не заведён: git не найден ни в поставке "
                    "(runtime/git), ни в системе")
        return False
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(tree), *args], capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=120, creationflags=flags)

    try:
        ignore = tree / ".gitignore"
        if not ignore.exists():
            ignore.write_text(_GIT_IGNORE, encoding="utf-8", newline="\n")
        else:
            _top_up_ignore(ignore)
        steps = (
            ("init", "-q", "-b", "main"),
            ("config", "user.name", agent_name(cfg)),
            ("config", "user.email", _agent_email(cfg)),
            ("config", "core.autocrlf", "false"),
            ("config", "core.safecrlf", "false"),
            ("config", "core.quotepath", "false"),
            # фоновая сборка мусора не отцепляется в отдельный процесс: под
            # оградой он пережил бы задание и остался в контейнере навсегда
            ("config", "gc.autoDetach", "false"),
            ("add", "-A"),
            ("commit", "-q", "--no-verify", "-m", "день ноль: дом собран"),
        )
        for step in steps:
            done = git(*step)
            if done.returncode != 0:
                said = (done.stderr or done.stdout or "").strip()[:300]
                raise OSError(f"git {' '.join(step[:2])}: {said}")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("личный git агента не заведён: %s", exc)
        shutil.rmtree(tree / ".git", ignore_errors=True)
        return False
    return True


def ensure_layout(tree: Path, cfg: dict | None = None) -> None:
    """Составляющие кадра — каждая в своей папке (слово владельца 30.08).

    Создаём только то, чего дерево само не заводит по дороге: дом конституции,
    входной ящик окна и стартовый комплект из `resources/**`. Остальное
    (memory/**, runs, .state) код дерева создаёт сам — заводить это здесь
    значило бы держать вторую карту раскладки.

    Конституция и весь комплект пишутся ТОЛЬКО если их ещё нет: принятый при
    установке текст и всё, что владелец или сам агент правил после, здесь не
    трогаются.

    ⚠ Неписуемая папка (диск только для чтения, чужие права, антивирус) роняла
    руннер здесь голым PermissionError — оболочка видела «упал» и перезапускала
    его по кругу, не имея чем назвать причину владельцу. Теперь причина названа
    исключением с текстом, а руннер уходит кодом «конфиг/раскладка», не «упал».
    """
    cfg = cfg or {}
    for rel in ("soul", "workspace/inbox", "memory/.control/desk_inbox/processed"):
        try:
            (tree / rel).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LayoutError(f"не создаётся папка {tree / rel}: {exc}") from exc
    soul = tree / "soul" / "SOUL.md"
    if not soul.exists():
        try:
            soul.write_text(soul_text(cfg), encoding="utf-8", newline="\n")
        except OSError as exc:
            raise LayoutError(f"не пишется конституция {soul}: {exc}") from exc
    planted = _seed_kit(tree, cfg)
    if planted:
        log.info("стартовый комплект: положено файлов в дерево: %d", planted)
    if seed_self(tree, cfg):
        log.info("запись о себе: день ноль, soul/self/CURRENT.md")
    # После записи о себе: первый снимок должен застать дом целиком.
    if seed_git(tree, cfg):
        log.info("личный git агента: репозиторий заведён в дереве данных, "
                 "первый снимок «день ноль» сделан")


# --------------------------------------------------------------------------- #
#  Замок на дерево: одна копия харнесса на одну память
# --------------------------------------------------------------------------- #

# Владелец замка обязан подтверждать, что жив (сердцебиение руннера переписывает
# отметку каждые 10 с). Три минуты — это восемнадцать пропущенных подтверждений:
# столько живой руннер не молчит даже на самом долгом ходе, потому что отметку
# пишет отдельный поток.
_LOCK_FRESH_SEC = 180.0
_LOCK: dict = {"path": None, "token": ""}


def tree_lock_path(tree: Path) -> Path:
    return Path(tree) / "memory" / ".state" / "harness.lock"


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс. На Windows — только запрос, НИКАКОГО os.kill.

    ⚠ `os.kill(pid, 0)` на Windows не «проверяет», а зовёт TerminateProcess:
    проверка живости чужого руннера убила бы его вместе с ходом владельца.
    """
    if pid <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True                      # чужой пользователь — но жив
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(0x1000, False, int(pid))   # QUERY_LIMITED_INFO
    if not handle:
        # 5 = отказано в доступе: процесс ЕСТЬ, просто чужой. 87 = нет такого.
        return ctypes.get_last_error() == 5
    try:
        code = ctypes.c_ulong(0)
        if kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == 259         # STILL_ACTIVE
        return True
    finally:
        kernel32.CloseHandle(handle)


def _read_lock(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _lock_is_live(holder: dict) -> bool:
    """Владелец замка жив? Сомнение — в пользу ЖИВОГО: два агента хуже, чем один."""
    if not holder:
        return False
    try:
        pid = int(holder.get("pid") or 0)
        at = float(holder.get("at") or 0.0)
    except (TypeError, ValueError):
        return False
    # Замок с другого хоста: номер процесса чужого пространства ничего не значит.
    # Найдено на сервере 06.09: контейнер пересоздан, новый раннер получил тот же
    # pid 8, что и прежний в замке, и харнесс отказывался подниматься кодом 3.
    # Свой собственный номер в чужом замке — тот же случай (тот же контейнер,
    # `docker restart`): держать замок сами на себя мы не можем.
    host = str(holder.get("host") or "")
    if host and host != platform.node():
        log.warning("замок дерева: pid %d на хосте %s, а мы на %s — считаю замок брошенным",
                    pid, host, platform.node())
        return False
    if pid == os.getpid():
        log.warning("замок дерева: в нём наш собственный pid %d — замок брошен прежней копией",
                    pid)
        return False
    if not _pid_alive(pid):
        return False
    if time.time() - at > _LOCK_FRESH_SEC:
        # Pid жив, а отметка старая: скорее всего номер достался чужому процессу
        # (винда их переиспользует). Забираем дерево, но говорим об этом вслух.
        log.warning("замок дерева: pid %d жив, но не подтверждал себя %.0f с — "
                    "считаю замок брошенным", pid, time.time() - at)
        return False
    return True


def claim_tree(tree: Path, *, agent: str = "", role: str = "") -> dict:
    """Забрать дерево себе. -> {} наш; иначе снимок ЖИВОГО владельца замка.

    ⚠ Замок по ПОРТУ (оболочка, служба) закрывает только случай «порт занят».
    А случаев, где руннер жив, а deskapp на порту нет, хватает: осиротевший
    процесс прошлой копии, убитый антивирусом deskapp, служба, поставленная из
    работающего окна. Живьём это выглядит так: рождение случается ДВАЖДЫ, ходы
    двух копий перемешиваются в ленте владельца, записка съедается одной копией
    и остаётся без ответа у другой — и ни один лог не говорит о второй ни слова.
    Для владельца это не «два агента», а «агент сломался».

    Замок — файл в самом дереве, потому что защищаем мы дерево, а не порт.
    Гонка двух одновременных стартов закрыта O_EXCL; брошенный замок (руннер
    убит) снимается проверкой живости pid.
    """
    path = tree_lock_path(tree)
    mine = {"pid": os.getpid(), "at": time.time(), "agent": str(agent or ""),
            "role": str(role or ""), "host": platform.node(),
            "token": hashlib.sha1(f"{os.getpid()}:{time.time()}".encode()).hexdigest()[:12]}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("замок дерева не завести (%s) — иду без него: %s", path, exc)
        return {}
    for attempt in (1, 2):
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            holder = _read_lock(path)
            if _lock_is_live(holder):
                return holder
            if attempt == 2:
                return _read_lock(path) or {"pid": 0}
            try:
                os.unlink(path)              # брошенный замок — снимаем и берём
            except OSError:
                return _read_lock(path) or {"pid": 0}
            continue
        except OSError as exc:
            # Своей неудачей замок не имеет права остановить продукт: без него
            # мы возвращаемся ровно к прежнему поведению, и это сказано в логе.
            log.warning("замок дерева не завести (%s) — иду без него: %s", path, exc)
            return {}
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as sink:
            json.dump(mine, sink, ensure_ascii=False)
        _LOCK["path"], _LOCK["token"] = path, mine["token"]
        atexit.register(release_tree)
        log.info("замок дерева взят: %s (pid %d)", path, mine["pid"])
        return {}
    return {}


def refresh_tree_lock() -> None:
    """Подтвердить, что владелец замка жив. Зовёт сердцебиение руннера."""
    path, token = _LOCK["path"], _LOCK["token"]
    if path is None:
        return
    holder = _read_lock(path)
    if holder and holder.get("token") != token:
        # Замок уже не наш (кто-то счёл нас мёртвыми) — переписывать чужое нельзя.
        log.warning("замок дерева больше не наш: им владеет pid %s",
                    holder.get("pid"))
        _LOCK["path"] = None
        return
    holder.update({"pid": os.getpid(), "at": time.time(), "token": token})
    try:
        path.write_text(json.dumps(holder, ensure_ascii=False), encoding="utf-8",
                        newline="\n")
    except OSError as exc:
        log.warning("замок дерева не подтверждён: %s", exc)


def release_tree() -> None:
    """Снять свой замок. Чужой не трогаем даже на выходе."""
    path, token = _LOCK["path"], _LOCK["token"]
    if path is None:
        return
    _LOCK["path"] = None
    try:
        if _read_lock(path).get("token") == token:
            path.unlink(missing_ok=True)
    except OSError:
        pass


# Имена ручек, значение которых нельзя показывать: экран «Система», телефон и
# GET /api/anatomy читают один и тот же файл, а ключ в таблице на экране — это
# ключ на скриншоте и при демонстрации экрана.
_SECRET_NAME_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PWD",
                      "PASS", "CREDENTIAL", "COOKIE", "SESSION")
# Имена, которые содержат опасное слово, но секретом не являются: `max_tokens`
# из блока модели пропал бы с экрана «Система» вместе с ключами, а это ровно та
# настройка, ради показа которой экран и заведён.
_SECRET_NAME_SAFE = ("MAX_TOKENS", "MAX_OUTPUT_TOKENS", "TOKENS_TODAY", "TOKEN_CAP",
                     "TOKEN_BUDGET", "TOKEN_LIMIT", "KEYWORD", "PASSTHROUGH", "BYPASS")

# Пароль внутри адреса — форма, которую кред-пол дерева не ловит (он про токены),
# а владелец кладёт её в ручки постоянно: DATABASE_URL, SMTP_URL, AMQP_URL.
_URL_PASSWORD = re.compile(r"://[^/\s:@]{1,64}:[^/\s@]{1,256}@")


def is_secret_knob(name: str) -> bool:
    upper = str(name).upper()
    for safe in _SECRET_NAME_SAFE:          # вычёркиваем безобидные совпадения
        upper = upper.replace(safe, "")
    return any(part in upper for part in _SECRET_NAME_PARTS)


def secret_shape(value) -> str:
    """Секрет ли это ПО ФОРМЕ ЗНАЧЕНИЯ. '' — не похоже; иначе название формы.

    ⚠ Маска по именам ручек угадывает, а не знает: `DATABASE_URL` или `SMTP_URL`
    с паролем внутри адреса под неё не попадают, хотя это тот же пароль владельца.
    Механический кред-пол уже есть в дереве (`core/secrets.credential_floor` —
    sk-…, токен бота, ключ z.ai, JWT, приватный ключ, ключи AWS/GitHub), и звать
    его дешевле, чем угадывать имена. Имена при этом НЕ отменяются: пол знает
    формы токенов и не знает слова `p@ssw0rd` в `SMTP_PASSWORD`. Работают оба.

    Пол живёт в дереве и приезжает поставкой; если его нет (дерево ещё не на
    sys.path, чужая сборка) — молча остаёмся на именах, а не роняем анатомию.
    """
    text = str(value or "")
    if not text.strip():
        return ""
    try:
        from core.secrets import credential_floor
        found = credential_floor(text)
    except Exception:
        found = ""
    if found:
        return found
    if _URL_PASSWORD.search(text):
        return "пароль внутри адреса"
    return ""


def looks_secret(name, value) -> bool:
    """Секрет по ИМЕНИ ручки или по ФОРМЕ значения — достаточно одного."""
    return bool((is_secret_knob(name) and str(value or "").strip())
                or secret_shape(value))


def scrub(value, _depth: int = 0):
    """Значение без секретов — для анатомии, лога и любого показа наружу.

    Рекурсивно: секрет прячется и внутри вложенного объекта. Именно этого не
    хватило блоку `model`, где фильтр перечислял имена («key», «api_api») —
    окно завело рядом НОВОЕ поле `model.keys` с тремя боевыми ключами, и оно
    поехало в анатомию целиком. Перечисление имён проигрывает соседу, который
    заводит поле; форма значения — нет.
    """
    if _depth > 6:
        return "…"
    if isinstance(value, dict):
        return {k: scrub(v, _depth + 1) for k, v in value.items()
                if not is_secret_knob(k)}
    if isinstance(value, (list, tuple)):
        return [scrub(v, _depth + 1) for v in value]
    return "задан" if secret_shape(value) else value


def public_model(cfg: dict) -> dict:
    """Блок `model` из helene.json без ключей — то, что можно показать владельцу."""
    block = cfg.get("model")
    return scrub(block) if isinstance(block, dict) else {}


def env_knobs(cfg: dict) -> dict[str, str]:
    """Ручки среды: порт-дефолты, поверх — `env` из helene.json (его слово последнее).

    ⚠ `"env": []` (или строка, или число) в конфиге роняло руннер AttributeError
    ещё до импорта дерева. Кривой конфиг — не повод для безымянной смерти.
    """
    knobs = dict(PORT_DEFAULTS)
    extra = cfg.get("env")
    if extra and not isinstance(extra, dict):
        log.warning("helene.json: блок env должен быть объектом, а не %s — "
                    "беру только порт-дефолты", type(extra).__name__)
        extra = None
    for key, value in (extra or {}).items():
        if value is None:
            knobs.pop(str(key), None)          # явное «не задавать» — тоже решение
        else:
            knobs[str(key)] = str(value)
    return knobs


def safe_knobs(cfg: dict) -> dict[str, str]:
    """Те же ручки, но значения секретов заменены на «задан».

    Прозрачность продукта — в том, чтобы владелец видел, ЧТО ручка задана, а не её
    значение. Ровно так `public_model` очищает блок `model` в анатомии; здесь та
    же мера для `env`, куда владелец кладёт OPENAI_API_KEY, токен бота и пароль
    SMTP. Анатомия уезжает на телефон и рисуется таблицей на экране.

    Маска идёт по имени ручки И по форме значения (`looks_secret`): имя ловит
    `SMTP_PASSWORD=p@ssw0rd`, форма — `DATABASE_URL=postgres://u:pass@host/db`
    и любой ключ, положенный в ручку с безобидным именем.
    """
    return {k: ("задан" if looks_secret(k, v) else v)
            for k, v in env_knobs(cfg).items()}


def apply_env(knobs: dict[str, str], *, where: str) -> None:
    os.environ.update(knobs)
    log.debug("ручки среды применены (%s): %d", where, len(knobs))


def _bash_answers(exe: str) -> bool:
    """Живой ли это bash: только ИСПОЛНЕНИЕ, не наличие файла.

    ⚠ Найденное живьём 31.08: `System32\\bash.EXE` — заглушка WSL. `which` её
    находит, а запуск падает «execvpe(/bin/bash) failed» — ровно то, что агент
    процитировал в дыме коробки. Наличие файла здесь не значит ничего.
    """
    import subprocess
    try:
        probe = subprocess.run([exe, "-lc", "echo praxis-shell-ok"],
                               capture_output=True, text=True, timeout=8)
        return probe.returncode == 0 and "praxis-shell-ok" in (probe.stdout or "")
    except Exception:
        return False


def ensure_shell() -> None:
    """Инструмент shell агента зовёт `bash -lc` — дать команде шанс на винде.

    Порядок от полного к достаточному: живой bash уже в PATH (Git for Windows,
    MSYS) — не трогаем ничего; иначе типовые установки Git; иначе busybox-шим
    из поставки (runtime/bash.exe — ПРЯМО рядом с python.exe, подпапки
    runtime/shims в поставке нет вовсе; кладёт сборщик дистрибутива). Не
    нашлось ничего живого — инструмент продолжит честно отказывать, и это
    правильнее тихой подмены синтаксиса.
    """
    import shutil as _shutil
    import sys as _sys
    if os.name != "nt":
        return
    found = _shutil.which("bash")
    if found and _bash_answers(found):
        return
    # ⚠ PATH здесь НЕ помогает: CreateProcess ищет команду в каталоге
    # приложения и System32 РАНЬШЕ PATH, и заглушка WSL из System32
    # перехватывает имя «bash» при любом порядке путей. Работает ровно одно
    # место — каталог интерпретатора (наш runtime): туда сборщик поставки и
    # кладёт busybox как bash.exe, а первый probe выше его сам находит.
    here = Path(_sys.executable).resolve().parent / "bash.exe"
    if here.is_file() and _bash_answers(str(here)):
        log.info("shell: живой bash — %s (busybox из поставки)", here)
        return
    log.info("shell: живого bash нет — инструмент shell будет честно отказывать"
             + (" (найденный %s — заглушка WSL)" % found if found else ""))


def dotenv_gate(code_dir: Path, cfg: dict) -> None:
    """Решить судьбу `.env` рядом с кодом — ДО импорта дерева.

    В продукте такого файла нет: он в .gitignore и наружу не уезжает. А на машине
    разработчика рядом с деревом лежит боевой — с ключами, сессией Telegram и
    поднятыми рычагами. Пока он приезжал молча, локальный продукт вёл себя не так,
    как поведёт себя у пользователя, и отладка врала.

    Дефолт: НЕ читать (`"read_dotenv": false`) — единственный источник настроек это
    helene.json. Кому нужен старый способ — ставит `true`, и дерево читает `.env` как
    читало. Оба случая называются в логе: тихой разницы между ними быть не должно.
    """
    stray = code_dir / ".env"
    if bool(cfg.get("read_dotenv")):
        if stray.exists():
            log.warning("читаю %s по просьбе конфига: его значения перекроют helene.json "
                        "на импорте (load_dotenv override=True)", stray)
        return
    try:
        import dotenv
    except ImportError:
        return
    dotenv.load_dotenv = lambda *a, **kw: False
    if stray.exists():
        log.warning("рядом с деревом лежит %s — НЕ читаю его (read_dotenv=false): "
                    "настройки продукта живут в helene.json", stray)


# --------------------------------------------------------------------------- #
#  Мозг: model из helene.json -> её memory/llm.json
# --------------------------------------------------------------------------- #

def _int_or(value, default: int, *, what: str) -> int:
    """Число из конфига владельца — или дефолт с названной причиной.

    ⚠ ПЕРВЫЙ-ЗАПУСК.md прямо зовёт владельца править helene.json руками. `"max_tokens":
    "8k"` роняло руннер ValueError ДО загрузки дерева: оболочка перезапускала его с
    растущей паузой и говорила «падает раз за разом» без причины. Соседняя строка
    (max_tool_iters) была защищена, эта — нет.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        if value not in (None, ""):
            log.warning("helene.json: %s = %r — не число, беру %d", what, value, default)
        return default


def _brain_config(cfg: dict) -> dict:
    """helene.json -> схема её llm.json (frameworks + roles + limits + pricing).

    Оба фреймворка описаны всегда: фолбэк роли и её собственный `switch_brain` живут
    на этой развилке.

    ⚠ ЧТО ТАКОЕ РОЛЬ `evaluator` НА САМОМ ДЕЛЕ (здесь стояло «привратник
    исходящего» — это неправда, и она стоила денег). Привратник исходящего в
    окне не зовётся НИКОГДА: ход владельца проходит его без вызова модели
    (`live/agent.py:14990`). Зовёт эту роль ДРУГОЕ — свёртка памяти и
    формирование (`live/memory_life.py:847` → `llm.chat("evaluator", …)`), а
    свёртку зовёт наш собственный руннер после каждого хода. Промпт свёртки —
    до 48 000 знаков, и он не из кэша.

    Поэтому модель этой роли — РУЧКА ВЛАДЕЛЬЦА, а не тень голоса:
      * `"evaluator": {"model": "…"}` в helene.json — полный блок роли. ЭТО
        ДОЛГОВЕЧНОЕ МЕСТО: ключа `evaluator` нет в WIZARD_KEYS установщика
        (`setup/src/install.rs:433`), значит переустановка его сохраняет;
      * `"model": {"compact_model": "…"}` — короткая форма рядом с моделью
        голоса. ⚠ Блок `model` визард при обновлении ПЕРЕПИСЫВАЕТ целиком, так
        что эта форма переживает не всё; писателю (окно Настроек, визард) стоит
        класть решение владельца в блок `evaluator`.
    Умолчание осталось прежним — модель голоса: подставить «дешёвую» от себя
    значило бы вписать имя, которого у владельца может не быть ни на одном
    эндпойнте, и свёртка молча падала бы на каждом ходе. Пустая ручка честнее
    выдуманной, а место, где её задать, теперь есть.

    ⚠ 03.09, три правки одного шва:
      * `fallback_framework` выбрасывался, и фолбэк уходил в «противоположный»
        фреймворк, ключа к которому нет: механика фолбэка ядра (429, обрыв сети,
        5xx) была обесточена ровно там, где она нужнее всего — на подписке;
      * оценщик наследовал `max_tokens` и `reasoning_effort` голоса, хотя докстринг
        обещал «только короче потолок». Наследуется теперь только адрес и модель;
      * `pricing` не переносился вовсе, поэтому доллары в продукте не считались
        нигде: `llm.pricing()` всегда пуст -> `estimated_cost_today()` всегда None.
    """
    voice = dict(cfg.get("model") or {})
    judge_raw = cfg.get("evaluator")
    judge = dict(judge_raw) if isinstance(judge_raw, dict) and judge_raw else None
    # Короткая форма: одно поле рядом с моделью голоса. Полный блок `evaluator`
    # сильнее — он ответ на «хочу другой эндпойнт», а это на «хочу другую модель».
    compact = str(voice.get("compact_model") or "").strip()
    if compact:
        judge = dict(judge or {})
        judge.setdefault("model", compact)
    out = {"frameworks": {"anthropic": {"base_url": "", "api_key": ""},
                          "openai": {"base_url": "", "api_key": ""}},
           "roles": {}, "limits": {}}
    for role, block, default_tokens in (("voice", voice, 8192),
                                        ("evaluator", judge if judge else voice, 1024)):
        inherited = role == "evaluator" and judge is None
        # Фреймворк роли свёрток по умолчанию — ТОТ ЖЕ, что у голоса: там лежат
        # адрес и ключ. Прежнее умолчание "openai" при anthropic-голосе уводило
        # свёртку в пустой блок без ключа, и она падала на каждом ходе.
        default_fw = str(voice.get("framework") or "openai") if role == "evaluator" \
            else "openai"
        framework = str(block.get("framework") or default_fw).strip().lower()
        if framework not in ("openai", "anthropic"):
            framework = "openai"
        base_url = str(block.get("base_url") or "").strip()
        key = str(block.get("key") or block.get("api_key") or "").strip()
        if base_url:
            out["frameworks"][framework]["base_url"] = base_url
        if key:
            out["frameworks"][framework]["api_key"] = key
        tokens = default_tokens if inherited else _int_or(
            block.get("max_tokens") or default_tokens, default_tokens,
            what=f"model.max_tokens ({role})")
        role_cfg = {"framework": framework,
                    "model": str(block.get("model") or ""),
                    "max_tokens": max(1, tokens),
                    "fallback_model": str(block.get("fallback_model") or "")}
        fallback_fw = str(block.get("fallback_framework") or "").strip().lower()
        if fallback_fw in ("openai", "anthropic"):
            role_cfg["fallback_framework"] = fallback_fw
        elif role_cfg["fallback_model"]:
            # Вторая модель того же реле — самый частый случай подписки
            # (gpt-5.6-sol -> gpt-5.6-luna). Без явного слова фолбэк остаётся
            # в том же фреймворке, где есть ключ, а не уходит в пустой.
            role_cfg["fallback_framework"] = framework
        # 09.09: зрячая замена текстовой модели на ход с картинкой (`model.vision_model`
        # в helene.json). Пусто — ядро само берёт glm-5.3-flash для glm; оценщик без
        # своего блока наследует ручку голоса вместе с адресом и моделью.
        vision = str(block.get("vision_model") or "").strip()
        if vision:
            role_cfg["vision_model"] = vision
        effort = str(block.get("reasoning_effort") or "").strip().lower()
        if effort and not inherited:
            role_cfg["reasoning_effort"] = effort
        elif inherited and role_cfg["model"].strip().lower().startswith("glm-"):
            # 08.09: у GLM серверное умолчание глубины — max. Оценщик без ступени
            # (свёртки, судья) думал дольше и дороже голоса, который владелец
            # поставил на low. Ступень оценщика — low, если не задана явно блоком
            # `evaluator`; чужим провайдерам ничего не подставляется.
            role_cfg["reasoning_effort"] = "low"
        out["roles"][role] = role_cfg
    out["limits"]["max_tool_iters"] = _int_or(cfg.get("max_tool_iters") or 20, 20,
                                              what="max_tool_iters")
    pricing = cfg.get("pricing")
    if isinstance(pricing, dict) and pricing:
        out["pricing"] = pricing
    return out


def _own_only(target: Path) -> None:
    """Сузить права файла с ключом до владельца — по-настоящему, а не 0o600.

    ⚠ Здесь стоял `os.chmod(target, 0o600)` с комментарием «конфиг с ключом не для
    чужих глаз». На Windows (а продукт — Windows-only) chmod умеет ровно один бит,
    «только чтение», и 0o600 его даже не ставит: права оставались наследованными,
    а в коде стояло обещание защиты. Ложная запись в защите хуже её отсутствия:
    следующий читатель не станет закрывать то, что уже «закрыто». Настоящий
    механизм рядом — `fence._grant` зовёт icacls; зовём его же.
    """
    if os.name != "nt":
        try:
            os.chmod(target, 0o600)
        except OSError as exc:
            log.debug("права на %s не сузились: %s", target, exc)
        return
    import subprocess
    user = os.environ.get("USERNAME") or ""
    if not user:
        log.warning("права на %s не сужены: USERNAME пуст", target)
        return
    # SYSTEM и админы остаются намеренно: службу продукта ставит сама поставка и
    # она поднимает руннер от LocalSystem — отобрать у SYSTEM чтение значило бы
    # сломать режим службы ради видимости защиты. Уходит ровно лишнее: ручки,
    # унаследованные от папки, включая RX песочницы.
    try:
        proc = subprocess.run(
            ["icacls", str(target), "/inheritance:r",
             "/grant:r", f"{user}:F", "/grant:r", "*S-1-5-18:F",
             "/grant:r", "*S-1-5-32-544:F", "/Q"],
            capture_output=True, text=True, encoding="cp866", errors="replace",
            creationflags=0x08000000, timeout=60)
        if proc.returncode != 0:
            log.warning("права на %s не сузились: %s", target,
                        (proc.stdout or proc.stderr or "").strip()[:200])
    except Exception as exc:
        log.warning("права на %s не сузились: %s", target, exc)


def project_brain(tree: Path, cfg: dict) -> str:
    """Положить мозг из helene.json в её `memory/llm.json` — но не затирать ЕЁ выбор.

    У неё есть своя рука `switch_brain`: она правит этот же файл. Переписывать его на
    каждом старте значило бы молча отменять её решение при каждом запуске окна. Поэтому
    проекция идёт ровно тогда, когда изменился САМ helene.json (сверяем отпечаток блока
    модели с распиской прошлой проекции) — или когда конфига мозга ещё нет.

    -> строка для лога: что сделано и почему.
    """
    target = tree / "memory" / "llm.json"
    receipt = tree / "memory" / ".state" / "pult_brain.json"
    built = _brain_config(cfg)
    # Свёртки памяти — вторая по расходу статья продукта после самих ходов, и до
    # 04.09 о ней не говорила ни одна строка: роль молча брала модель голоса.
    judge, voice_role = built["roles"]["evaluator"], built["roles"]["voice"]
    log.info("свёртки памяти: модель %s%s", judge["model"] or "(не задана)",
             "" if judge["model"] != voice_role["model"] else
             " — та же, что у голоса; дешевле задаётся блоком \"evaluator\": "
             "{\"model\": \"…\"} в helene.json")
    fingerprint = hashlib.sha256(
        json.dumps(built, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    if target.exists():
        try:
            loaded = json.loads(receipt.read_text(encoding="utf-8"))
            seen = loaded.get("fingerprint") if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            seen = None
        if seen == fingerprint:
            return "мозг: llm.json на месте, helene.json не менялся — не трогаю"
    # ⚠ Файл ПОДМЕШИВАЕТСЯ, а не подменяется: ядро уже чинило этот класс у себя
    # (llm.py: «пропускаем как есть, чтобы запись конфига панелью не стирала блок»),
    # а продукт писал файл сам и стирал всё, чего не знает — в первую очередь
    # дописанный руками `pricing`, единственный источник долларов в отчётах.
    merged: dict = {}
    if target.exists():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(current, dict):
                merged = current
        except (OSError, ValueError):
            merged = {}
    merged.update(built)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(".tmp-llm.json")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=1),
                   encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    _own_only(target)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps({"fingerprint": fingerprint}, ensure_ascii=False),
                       encoding="utf-8", newline="\n")
    role = built["roles"]["voice"]
    return (f"мозг: llm.json записан из helene.json — {role['model']} @ "
            f"{built['frameworks'][role['framework']]['base_url']} ({role['framework']})")
