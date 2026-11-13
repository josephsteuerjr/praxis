# CODEMAP

Карта фактических файлов текущего дерева. Здесь нет live-статуса, дорожной карты и истории
проходов: deployment truth находится в [`STATUS.md`](STATUS.md), устойчивые связи — в
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Ядро и Telegram runtime

| Файл | Роль |
|---|---|
| `agent.py` | Persona/context assembly, модельный tool loop, tool registry, outbound guard, durable run/delivery integration. |
| `dialogue_projection.py` | Общая чистая проекция native/epoch rows в роли, complete logical-send batches и service-row attachments. |
| `frame_continuation.py` | Default-off derived first-input checkpoint группы; source-proved append между chat turns, без переноса внутреннего tool transcript. |
| `mtproto_runner.py` | Живой Telethon client: update routing, buffers, forum topics, media, delivery/outbox replay, membership/account hooks и единые серверные часы. |
| `llm.py` | Конфигурация ролей моделей, Anthropic/OpenAI protocol adapters, fallback, usage и hosted tools. |
| `provider_observation.py` | Hosted provider web action metadata: enum/opaque ID whitelist, deduplication, unknown/partial/complete coverage and explicit runtime feedback; never executable function calls. |
| `bootguard.py` | Preflight, supervision, healthy-commit marker и rollback раннего сломанного self-edit. |
| `reflex.py` | Детерминированный pre-model фильтр очевидного шума. |
| `perception.py` | Живые настройки debounce/cooldown/wake и журнал причин, почему голос не был вызван. |
| `turns.py` | Scope-aware журнал прожитых ходов и исходов отправки/удержания. |
| `core/notices.py` | Накопитель уведомлений (25.09, G): обращения и личка из других комнат, пока она занята; слова владельца для живых окон; блок в изменчивом хвосте кадра; гаснет ходом исходного чата. |
| `core/processes.py` | Реестр её фоновых процессов из shell (pid, команда, кто запустил): строка в хвосте кадра, чтобы окна не считали друг друга «чужим клоном». |
| `bufstore.py`, `notes.py` | Персистентный conversation buffer и короткие заметки о недавнем сказанном. |
| `identity.py` | Оркестрация self-authorship: активные load events, night revision, version/provenance и post-change events; legacy deformation scores остаются инертным архивом. |
| `capabilities.py`, `rails.py` | Фактический capability snapshot и provenance/risk registry; его оценки не являются veto для sovereign self. |
| `tool_offerings.py` | Детерминированное формирование полного набора tools для owner, Praxis self и scoped trusted humans. |
| `brain.py`, `appetite.py` | Наблюдаемый выбор model role и учёт/интерпретация вычислительного аппетита без скрытого veto. |
| `media.py` | Typed inbound/outbound media, guarded spool, durable media receipts и cleanup. |
| `imagegen.py` | Standalone Codex Images API через OpenAI-канал реле, отдельные настройки images, проверка реального изображения и scoped artifact; общая рука generate_image для GLM и relay voice. |
| `media_audio.py` | Local STT и XTTS-only facade; прежние TTS отвергаются без fallback. |
| `silero_tts_client.py`, `silero_tts_worker.py` | Lazy process-isolated Silero v5_ru: verified local model SHA, JSONL supervisor, timeout/RSS/recycle/parent-death и Piper fallback без импорта PyTorch в runner. |
| `requirements-silero.txt`, `scripts/provision_silero.py` | Отдельная worker-only Python-среда и verified model provisioning; основной `requirements.txt` остаётся без PyTorch. |
| `scripts/silero_soak.py` | Bounded 30–60-минутный gate трудного корпуса: latency/RTF, worker+cgroup memory/swap/PSI и idle-unload; не меняет live backend. |
| `stt_rpc.py` | Authenticated HTTP-over-UDS адаптер для bounded hardbot STT; переиспользует resident Whisper из `mtproto_runner.py`, удаляет private temp audio и не персистит transcript. |

## Durable runs и Telegram side effects

| Файл | Роль |
|---|---|
| `run_context.py` | Immutable `RunContext` и `ContextVar`-binding authority/address для одного исполнения. |
| `run_manager.py` | `memory/runs/`: manifest, append-only WAL, ResultRef/artifact storage, control, recovery, RECAP и promotion. |
| `run_resume.py` | Read-only reducer WAL/manifest/checkpoint evidence в строгий resume plan с cumulative event/byte/time budgets, не являющимися tool cap. |
| `run_executor.py` | Исполнитель resume plan с exact revision/event-seq lease и injected callbacks. |
| `run_snapshot.py` | Формат неизменяемого снимка `context.md`: писатель/читатель `praxis.run.context.v2` («метр в скобках») плюс дословный `render_v1` для отката. Текст гостя лежит под ПОМЕЧЕННЫМ гуттером (`"> "`, а после экранного разрыва — `">LS> "`, `">CR> "` и т. д.), поэтому ни один его кусок не встаёт в колонку 0 ни у парсера, ни у её глаз; конец секции считается по метру, а не ищется регуляркой. `write()` — единственный вход записи: типизированная расписка на любую деградацию (включая НАЗВАННУЮ стрингификацию — имя поля и тип вместо молчаливого `default=str`), фаза выката по `PRAXIS_SNAPSHOT_WRITE` из `.env` (умолчание `v1`; служба читает только `env_file: .env`, не `.deploy.env`). Потолка записи здесь НЕТ — вынесен отдельным заходом. Снимки v1 читаются прежней веткой навсегда. |
| `gutter.py` | Общий лист обоих замков: чужой байт входит в наш документ ровно ДВУМЯ стоками и ни один из них ничего не ИЩЕТ в тексте. `quote()` — многострочное тело: каждая строка получает `"> "`, пустая — голый `">"`, экранные разрывы уезжают в имя метки (`">CR> "`) и физически исчезают. `inline()` — одно значение внутри нашей строки: `«…»`, изнутри которых нечем закрыться, разделитель слотов недостижим, разрыв поднят в видимую метку `⟨CR⟩`. `Own` — тип куска (не str-подкласс) с признаком `guest`: происхождение наследуется склейкой. `is_gutter_line()` — ПОЛОЖИТЕЛЬНАЯ проверка «эту строку выдал наш сток»: разбор и обратная сборка, а не угадывание по первому знаку. Зависимостей нет и не будет: оба потребителя объявляют, что не двигают наблюдаемое. |
| `frame_layout.py` | Форма кадра: как её контекст выглядит, когда его читают глазами. `section()` — тир правилом с заголовком и подписью СБОРЩИКА `↳ причина · путь · как отобрано · оговорки` (словарь причин ЗАКРЫТ восемью токенами; неизвестный ярлык даёт `причина не названа` и поднимает счётчик). `origin()` — паспорт находки: чужие фасеты в «…», наши голыми. `situation()` — пятая зона кадра `<CURRENT_SITUATION>`: шесть строк ВСЕГДА, пустое печатается прочерком с названной причиной. `reply_open()` — подписанный тег текущей реплики (`author`/`tg`/`message` из транспортных полей). `tape()` — гуттер на всю доставленную ленту при РЕНДЕРЕ, ровно один раз, по роли: её собственные реплики гуттера не получают. `assay()`/`assay_tape()` — метр: всякая строка либо продукт стока, либо дословно объявлена эмиттером. |
| `frame_trace.py` | Прибор кадра: из каких отрезков физически состоит вход, уехавший в модель. `mark()` возвращает ТОТ ЖЕ объект — кадр байт-в-байт одинаков с прибором и без него. Пять зон (`persona`, `dynamic`, `evidence`, `situation`, …), закрытый `SITUATION_ROSTER` из восьми имён, `declare()`/`declared()` — реестр наших строк для метра, `note_position()` — где зона стоит. Три разных нуля различимы: «не влезло в бюджет» ≠ «ветка не выбрана» ≠ «пусто». Метаданные едут на идемпотентной расписке `model-input`; токены здесь не оцениваются вовсе. |
| `frame_literal.py` | Литеральный писатель кадров №111932 — на деле ЧИТАТЕЛЬ durable-слоя: находит в прогоне событие `model_input` (последнее или по `call_id`), валидирует тройку manifest/events/results через `keat_source.adapt_model_input` и рендерит дословный Markdown (system/messages целиком, схемы рук — именами). Никакого захвата или подачи в модель; граница честности про скрабблер — в шапке рендера и docs/keat-source-adapter.md. |
| `process_liveness.py` | Безопасная платформенная проверка PID; на Windows использует process handle/wait, не POSIX signal. |
| `telegram_outbox.py` | Durable intent/acceptance store для direct text/file и scheduled message/note; stable MTProto random id и immutable staged files. |
| `owner_delivery.py` | Append-only owner inbox для Telegram (и исторической PWA-проекции): typed outcome, dedupe/coalesce, CAS states, target-80 soft history budget без attention cap и torn-tail evidence. |
| `telegram_topics.py` | Exact root/topic route и стабильный conversation id для forum messages. |
| `group_context.py` | Root-bounded append-only group archive, topic/participant projection, search, orientation bundle и backfill receipt. |
| `telegram_membership.py` | Fsync JSONL state machine sovereign owner/self join/leave и post-acceptance room projection. |
| `telegram_registry.py` | Discovery установленной Telethon TL-schema; sovereign raw dispatch; account-critical исполняется напрямую, параметры и результат не эхоятся в receipt. |
| `telegram_contacts.py` | Restart-proof адресная книга без хранения access hash. |
| `telegram_followups.py` | Durable ledger просьб написать кому-то и уведомлений о полученном ответе. |
| `rooms.py` | Allowlist/departed mask, freeze/mode и валидируемый root-room context profile. |

## Память, self и намерения

| Файл | Роль |
|---|---|
| `memory_life.py` | Append-only life evidence, episode compacts и hot-memory folding. |
| `memory_provenance.py` | Trust-классы: raw journal/reflections остаются searchable episodic evidence, но не normative provenance. |
| `memory_fts.py` | Rebuildable SQLite FTS по каноническим Markdown/JSONL/snapshots; journal получает отдельный source type. |
| `memory_index.py` | Recall/rerank API, optional embeddings и обновление навигационного индекса. |
| `memory_catalog.py` | Детерминированные bounded `memory/INDEX.md` и карты PEOPLE/ROOMS/PROJECTS/THREADS/RUNS/COMPUTERS. |
| `consolidate.py` | Сохраняет journal как непродвигаемый episodic log, отмечает дни и перестраивает производную навигацию; scheduled normative mutations = 0. |
| `formation.py` | Evidence-backed iterative formation вне критического пути ответа. |
| `sleep.py` | Планировщик formation/identity/index care; normative изменения идут через claims, REM только журналирует кандидаты, rumination no-op. |
| `people.py`, `social.py` | Портреты людей, salience/open loops и социальные роли/допуск. |
| `graph.py` | Markdown-граф связей и bounded traversal/resolve. |
| `self_model.py` | Fail-closed provenance-valid CURRENT, quarantined legacy/history, observations и explicit migrate/revise/rollback. |
| `desires.py` | Append-only conation ledger и rebuildable `memory/desires/CURRENT.md`. |
| `goals.py` | Однонаправленный compatibility/migration bridge удалённой moral-goals системы; живые свободные намерения принадлежат `desires.py`. |
| `computer_memory.py` | Канонические receipts/events Windows-работы, task/device reports и одна life-promotion на завершение. |
| `computer_inventory.py` | Server-observed inventory snapshot и catch-up refresh для Windows device. |
| `computer_access.py` | Гранты по stable Telegram user id для зрителей старого `praxis_app`; руки Praxis его не вызывают и им не ограничены (с 10.11 рука `computer_access` удалена). |

## Автономная периодика

| Файл | Роль |
|---|---|
| `tasks.py` | Файловые scheduled wake/window/email/message/note occurrences и расчёт due без модели. `wake` — её будильник: живой ход с ОТКРЫТЫМ Telegram (в отличие от window, который транспорт намеренно рвёт). |
| `heartbeat.py` | Контекст автономного owner-window по открытым нитям. |
| `social_pulse.py` | Hourly claim и durable delivery receipts; недавние отправки видимы как контекст, но per-pulse/daily/target cap отсутствует. |
| `unanswered.py` | Персистентный список действительно неразрешённых DM после cooldown. |
| `absence.py` | Политика ответа важным контактам во время явно активного окна отсутствия. |
| `immune.py` | Advisory review self-edits/proposals и очередь post-commit verdicts; red не является veto. |

## Forge и локальные руки

| Файл/каталог | Роль |
|---|---|
| `forge.py` | Единственная Forge state machine: task root/base, inspect/edit/run, checkpoint, workers, swarm, verification, learning и finish. |
| `forge_intelligence.py` | Manifest/source orientation и semantic project model без обязательного LSP. |
| `forge_process.py` | Supervisor одного detached Forge process с полным log/result. |
| `forge_worker.py` | Fresh-context worker protocol и tool dispatch. |
| `forge_swarm.py` | Persistent worker DAG, mailbox и advisory ownership. |
| `forge_verify.py` | Detached verification matrix с bounded parallelism и независимыми logs. |
| `forge_learning.py` | Evidence-linked reusable lessons завершённых задач. |
| `test_forge_contract_0610.py` | Контракт задачи (критерии + verify_commands), дедуп верификации по tree-sha и честный finish: regression-набор PR oro/forge-contract-0610. |
| `workshop.py` | Python file/shell/dev-tool surface, используемая model tools. |
| `editor.py`, `tool_report.py` | Единый replace/write/patch/batch, versioned readback и честные confirmed/refused/partial/in_doubt. |
| `catalog_families.py` | Объединённые proposal/project/memory_graph и native Python schema, явная preflight-валидация. |
| `python_execution.py`, `command_execution.py` | Прямой stdin Python с h; общий launcher и наблюдаемый Git/exit хвост после admission. |
| `pyhands/praxis_hands.py`, `forge_script.py` | Python file API, task nonce/custodian verification и клиент h.hand/h.describe. |
| `hand_bridge.py` | Приватный Unix IPC живой foreground-команды: peer identity, exact request cache, scoped lifetime, drain начатых вызовов после выхода скрипта; durable parent dispatch в agent. |
| `hands.py` | Пол рук на чистом Python: снимок и публикация файла с проверкой записанного, поиск отдельным процессом, скелет, запуск. |
| `selfgit.py` | Автокоммиты изменений собственного дерева и provenance core edits. |
| `selfdev.py` | Sovereign worktree proposal/test/review/merge/restart workflow с rollback и durable override reason. |
| `_sandbox.py`, `praxis_test.py`, `conftest.py` | Test-only перенаправление `PRAXIS_BASE` в disposable tree до импорта runtime-модулей; bare unittest не является поддержанным локальным entrypoint. |

## Привилегированный Linux host

| Файл | Роль |
|---|---|
| `serverd/broker.py` | Brainless `praxis.host.v2` UDS broker, auth/cgroup binding и routing операций. |
| `serverd/brokerops.py` | Stateless root workspace primitives и persistent operation directories. |
| `serverd/hostproc.py` | Detached root process supervisor, PID identity, logs, limits, stop tree и reaping. |
| `serverd/hostverbs.py` | Typed systemd/docker/pkg/file/net/reboot verbs с before/after evidence. |
| `serverd/hostrecovery.py` | Видимые rollback receipts/timers и explicit confirm. |
| `serverd/auditlog.py` | Append-only hash-chained audit и content-addressed export. |
| `serverd/advisor.py` | Consequence advice и выбранная owner file boundary. |
| `serverd/migrate_v1_tasks.py` | Идемпотентная миграция legacy host tasks в canonical Forge store. |
| `serverd_client.py` | Версионированный клиент UDS broker для Forge. |
| `serverd/install.sh`, `serverd/praxis-serverd.service`, `serverd/serverdctl` | Установка unit и операторский CLI. |
| `hostview.py` | Read-only запросы к серверной обсерватории. |
| `hostops.py`, `hostagent.py` | Сохраняемый legacy proposal/apply-контур host edits; не task store и не основной v2 broker. |

## Windows Body

| Файл/каталог | Роль |
|---|---|
| `body/crates/praxis-protocol/` | Wire types `praxis.body.v1`: identity, invoke/accepted/result, seq/ack и artifact refs. |
| `body/crates/praxis-body/` | Windows capability executor: config/identity, files, artifacts, processes/Job Objects, desktop, local routing, journal и transport. |
| `body/crates/praxis-system-router/` | Automatic LocalSystem service, sole WSS owner и supervision/routing system + interactive envelopes. |
| `body/crates/praxis-tray/` | GUI-subsystem tray icon и hidden interactive-session supervisor без console window. |
| `body/crates/praxis-bridge/` | WSS relay, durable frame spool, controller responses и artifact CAS. |
| `body_client.py` | Server-side synchronous controller: транспорт прямых `computer.*`-глаголов (PASS 30 Этап 3) и Windows backend deprecated wcode-скважины Forge. |
| `body/README.md` | Build/install/operator contract именно Windows Body. |

Внутри `praxis-body/src/`: `fsops.rs` отвечает за guarded file operations, `artifact.rs` — CAS
import/export, `process.rs` — process lifecycle, `desktop.rs` — typed Win32 windows/input/capture/
clipboard/process surface, `journal.rs` — durable operation receipts, `transport.rs` — WSS frame
flow, а `interactive_*`, `system_router.rs` и `local_router.rs` разделяют session routing.
`desktop.rs` активирует background-окно через временный `AttachThreadInput` handshake и затем
fail-closed проверяет фактический foreground HWND/PID перед вводом. `process_liveness.py` использует
Win32 process handle/wait и никогда не пробует Windows PID через `os.kill(pid, 0)`.

## Почта и owner surfaces

| Файл/каталог | Роль |
|---|---|
| `mailer.py` | SMTP/IMAP primitives. |
| `mailroom.py` | Mail index, bodies by hash, drafts и owner-approved send state. |
| `mailroom_bot.py` | Headless Telegram bot: IMAP poll/ingest, новые письма, proposal/self-merge/host/room cards, restart signal и contact flow. Production entrypoint не открывает HTTP. |
| `panel.py`, `panelapp.html`, `panel_static/` | Live repo introspection, capabilities/settings and device panel frontend/assets. |
| `praxis_app.py`, `praxis_device_auth.py` | Retired compatibility modules прежнего web projection; production runtime их не публикует. |
| `praxisapp.html`, `praxis_static/` | Retired frontend assets; не публикуются и не входят в production runtime surface. |
| `serverapp.py`, `serverapp.html` | Read-only server observatory backend/frontend. |
| `logsink.py` | Rotated runtime log sink для owner surfaces. |

## Канонические каталоги данных

| Путь | Содержимое |
|---|---|
| `soul/` | Конституция, voice, rails, compact self/history и навыки. |
| `memory/people/`, `memory/rooms/` | Структурированная память. Room/profile context и exact principal-bound participant dossier доступны адресно; legacy people dossiers не входят в broad automatic recall. |
| `memory/journal/`, `memory/reflections.md` | Untrusted episodic evidence: сохраняется и ищется явно, но не входит в auto-orientation/normative derivation. |
| `memory/groups/` | Приватные root-bounded group archives; MAP рядом является derived. |
| `memory/runs/` | Immutable run context, WAL, results, artifacts и RECAP. |
| `memory/computer/` | Windows evidence и inventory snapshots. |
| `memory/desires/`, `memory/self/` | Conation events и provenance self observations. |
| `memory/.forge/` | Единственный task/worker/verification/learning store Forge. |
| `memory/.state/` | Смешанный runtime-контур: rebuildable индексы плюс durable outbox/control/receipt ledgers, owner delivery и историческое device/PWA state; удаляемость определяется модулем. |
| `memory/access/devices/events.jsonl` | Private HMAC-chained issue/redeem/revoke authority; восстанавливается только вместе с `memory/.state/praxis_device_auth.key`. |
| `memory/dialogues/`, `memory/access/TRUST.md`, `memory/goals.md` | Приватные ignored runtime-данные: сохраняются на live/backup, но не отслеживаются source Git. |
| `workspace/` | Рабочие входы/выходы и scoped Telegram inbox, не личность и не task store. |

## Конфигурация и проверки

| Файл | Роль |
|---|---|
| `.env.example` | Публичный перечень runtime-настроек без секретов. |
| `memory/llm.json` | Runtime model roles/limits; обычно gitignored. |
| `docker-compose.yml` | Локальный `praxis` + optional Ollama. |
| `docker-compose.deploy.yml` | Production containers, mounts и owner surfaces. |
| `Dockerfile`, `requirements.txt` | Python runtime. |
| `praxis_test.py`, `test_*.py` | Герметичный unittest entrypoint и действующие contract/regression tests; retired phrase/personality-freeze suites не входят в дерево. |
| `serverd/test_broker_v2.py` | Linux broker contract tests. |
| `body/crates/*` tests | Rust protocol/body/bridge/router unit and integration tests. |

Правила запуска опасных Windows/process suites находятся в [`AGENTS.md`](AGENTS.md). Исторические
версии этой карты и удалённые проектные заметки извлекаются из Git через
`git show <commit>:<path>`, а не дублируются в текущем дереве.

## XTTS и MiniApp

- `xtts_protocol.py` — лёгкие wire/intent contracts и проверки идентичности.
- `xtts_tts_client.py` — UDS-клиент, pinned selection, bounded OGG download/cancel; без model/encoder imports.
- `xtts_engine.py` — локальные verified weights, conditioning cache и tokenizer-aware splitting.
- `xtts_service.py`, `xtts.service` — отдельный resident runtime, health/control, Opus encoder и bounded recovery.
- `xtts_intent.py` — preparation внутри существующего MediaSpool ledger, без второй очереди доставки.
- `xtts_install.py`, `requirements-xtts.{txt,lock}` — отдельная серверная установка с pinned model/runtime.
- `xtts_control.py` — status/catalog/configure/enable/disable и durable config receipts.
- `test_xtts.py` — offline lifecycle/intent/profile/cancel/concurrency и integration с canonical delivery.

Интеграция: `media.py` проверяет scoped preparation payload; `run_resume.py` восстанавливает inert intent; `agent.py` stages exact speech и архивирует prepared bytes; `mtproto_runner.py` готовит аудио вне ONE_MIND и использует существующие media receipts.

- `xtts_profiles.py`, `xtts_voice_rpc.py` — каталог, private reference/clone store, CAS selection, preview/control и cancellation.
- `praxis_voice_ui.py` — временные bounded UI jobs и private audio responses; без Telegram.
- `xtts_desk_adapter.py`, `xtts_miniapp_patch.py` — узкая интеграция в существующий private desk runtime с hash guards у deploy caller.
- `miniapp_voice.ts`, `miniapp_voice.css` — каталог, пробы, выбор, пауза, запись/загрузка и удаление голоса в прежнем MiniApp.
- `test_xtts_voices.py`, `test_xtts_ui.py`, `test_xtts_miniapp_patch.py` — profile/cache/races, private UI lifecycle и сохранение старых UI bytes.
# Forge wave2 candidate modules

- `forge_runtime.py`: one worker's OS writer lock, full transcript/events/payloads/checkpoint and safe continuation identity.
- `forge_roots.py`: physical/overlapping root transaction locks and cross-task writer ownership observations.
- `forge_recovery.py`: explicit exact-subject recovery of legacy local/host custody from canonical launch evidence and a witnessed kernel reboot; preserves original outcomes.
- `test_coding_reads_0311.py`, `test_forge_recovery_0311.py`: actual failed read/checkpoint regressions, durable read-vs-mutation behavior, lightweight orientation and positive/negative reboot custody cases.
- `command_process.py`, `forge_process.py`, `forge_verify.py`, `forge_remote_run.py`: exact local group/job and remote operation custody through lost replies/Stop.
- `forge_usage.py`: append-only idempotent task-wide observed/unknown usage; no invented price or hidden tree cap.
- `forge_review.py`, `forge_review_worker.py`: separate frozen independent critics, real read coverage and typed isolated A/L/B/M merger/rework; never live apply.
- `forge_reader.py`, `forge_control.py`: owner projections and durable control intents/receipts; HTTP handlers do not execute model or control effects.
- `forge_tools.py`: team schema extension for existing agent wrappers, including `coding_control`/`coding_review`.
- `praxis_static/forge_ui.js`, `forge_ui.css`: Runs team panel with actual hierarchy/stages/full outputs/control readback.
- `test_forge_wave2_server.py` and focused donor consumer suites: server adaptation, process ownership, recovery, exact acceptance, independent review and prefix preservation.

`forge_battery.py` exports the exact frozen composition and binds complete executed canonical-matrix logs; `command_child.py` is the Linux per-command subreaper used by `command_process.py`, including detached descendants.
