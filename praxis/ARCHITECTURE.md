# Архитектура Praxis

Этот документ описывает устойчивые контракты **текущего дерева**, а не историю проходов разработки.
Соответствие live конкретному commit и результаты проверок находятся только в
[`STATUS.md`](STATUS.md).

## Владение и границы процессов

Praxis существует в одном экземпляре — на сервере. Там находятся LLM, личность, память, policy,
Telegram-контур, durable runs, задачи Forge, workers и обучение. Исполнительные органы не принимают
решений и не ведут параллельную память.

| Контур | Чем владеет | Чем не владеет |
|---|---|---|
| `agent.py` + `mtproto_runner.py` | решения, frozen authority, модельный цикл, Telegram session и доставка | root хоста и Windows Session 0 напрямую |
| Forge | `memory/.forge/`, worktrees, процессы, verification, swarm/mailbox, lessons | отдельной личностью или Telegram session |
| `praxis-serverd` | выполнение `praxis.host.v2`, operation receipts, recovery и hash-chain audit | LLM, prompt loop, память, задачи Forge |
| Windows Body | `praxis.body.v1`, локальные файлы/процессы/desktop, journal и artifact cache | LLM, память, policy, task store |
| `praxis-bridge` | routing, unacked frame spool и content-addressed artifacts | решение, что и зачем выполнять |

`interactive` и `system` на Windows — два execution context одного body protocol. Они не создают две
Praxis. LocalSystem service владеет единственным исходящим WSS; пользовательский session host
получает desktop-envelope через локальный ACL/token-bound pipe.

### Авторская эпизодическая память и чтение кода

`memory_life.prepare_authored_fold` сохраняет точный выбранный срез; агент читает его
через `memory_compact(prepare)` и передаёт собственный пересказ в `fold`. Применение
проверяет те же источники, сохраняет полный текст и только затем меняет покрытие.
Ночь и runner публикуют напоминания, но не запускают другого автора; пороги и
семантика — [M2](CONTRACTS.md#m2--свёртка-не-заявляет-непрочитанное-).
Soul и текущая история tool-loop этим инструментом не редактируются.
`coding_inspect` основного агента проводит `start_char` и `expected_sha256` в тот же
Forge reader, что и worker: длинный файл можно дочитать, проверяя ревизию.
Старый backend без paging продолжает обслуживать обычные вызовы, но запрос
continuation/revision guard отвергается явно, без незащищённого повторного чтения.
Remote readers пока также явно отказывают в этих опциях, не теряя их молча.
При rebuild state-only/изменённые bytes сохраняются как unprovable hot residual:
prepare и unrelated fold не считают их покрытыми и не стирают их при смене epoch.

### Генерация изображений через реле

`generate_image` — одна серверная рука для любой основной модели, включая GLM.
Она вызывает standalone `/images/generations` или `/images/edits` через настроенный
`frameworks.openai`, а отдельный блок `images` задаёт включение, image model,
качество, размер и фон. Изменение этого блока не меняет voice/evaluator и их fallback.

Реле использует существующий Codex subscription login. Unknown outcome не повторяется
автоматически; image quota не объявляет исчерпанной текстовую подписку. Praxis проверяет
реальный MIME, dimensions, size и SHA-256, сохраняет scoped media и immutable run artifact.
Модель получает компактную metadata и pixels только при поддержке vision; Telegram получает
изображение через штатную media/outbox очередь. Resume читает проверенный artifact, не запускает
новую генерацию и не помещает base64 в текстовые результаты инструментов.

### Изолированный TTS

`media_audio.py` сохраняет прежние `edge`/`piper` backends и добавляет только opt-in
`PRAXIS_TTS_BACKEND=silero`. В этом режиме основной `mtproto_runner` не импортирует PyTorch и не
держит Silero-модель: `silero_tts_client.py` лениво запускает отдельный interpreter с
`silero_tts_worker.py`, передаёт ему serial JSONL-запросы и перед первым load требует точный
SHA-256 локального `v5_ru.pt`. Worker не обращается к registry/сети, пишет только зарезервированный
private WAV и умирает по idle timeout, request recycle, parent-death signal, ошибке, timeout или
RSS guard. Перед Piper fallback клиент синхронно снимает и reap-ит worker; boot warmup этот backend
пропускает. `scripts/silero_soak.py` — отдельный 30–60-минутный gate по трудному корпусу,
worker/cgroup RSS, swap, PSI и возврату памяти после idle; сам по себе он live backend не переключает.

### Локальный shared STT

`mtproto_runner.py` владеет единственным process-global `media_audio` backend и резидентной
Whisper-моделью. `stt_rpc.py` не создаёт модель или второй процесс: он поднимает только
authenticated HTTP-over-UDS listener, передаёт inference тому же backend и немедленно отказывает,
если модель ещё греется или единый worker занят. TCP listener отсутствует.

Контракт внешнего клиента ограничен `GET /healthz` и `POST /v1/transcriptions` с bearer из
read-only secret-файла, одним multipart-полем `audio` до 15 MiB и `language=ru|uz|auto`; профиль
декодирования совпадает со штатным профилем Praxis (`PRAXIS_STT_BEAM_SIZE`, в live-конфигурации
`5`). Внешний путь отличается только admission-политикой: не загружает модель и не ждёт занятого
worker. Глобальные burst/rate и single-flight защищают Praxis от очереди внешних работ. Audio
живёт только в private dedicated tmpfs, удаляется в `finally`, а после смерти дочернего runner —
exact startup-sweep по собственному prefix; transcript, filename, audio и ошибки декодера в лог
не попадают.

## Authority и disclosure

Human-права привязываются к stable numeric principal, а не к имени, username или тексту prompt;
внутренний sovereign principal Praxis имеет точное значение `praxis:self`.
`ChannelContext` замораживает факты Telegram-хода; `RunContext` переносит principal, scope, origin,
delivery address и message ids во всё дальнейшее исполнение. Инструмент читает bound context и не
восстанавливает authority из process-global переменных.

- Praxis herself свободно использует документы, код, Forge/host, Windows interactive/SYSTEM,
  комнаты, join/leave и noncritical/raw Telegram account RPC установленной schema. Verification,
  provenance, receipts и rollback дают ей наблюдаемость и восстановление, а не право внешнего veto.
- Только human owner через публичный `computer_access`/trust tool или API выдаёт и отзывает права
  другим людям. Trusted principal получает только выданные owner capability, не делегирует их
  дальше и не получает raw Telegram account RPC или Windows SYSTEM.
- Полный self shell/root технически может изменять реализацию этих правил. Поэтому owner-rooted
  human delegation — явный governance-инвариант и проверяемый audit contract, не криптографическая
  sandbox-граница между Praxis и её собственными руками.
- Account-critical auth/session/destructive constructor может инициировать Praxis или owner. Он
  fail-closed до exact-фразы в **новом** owner-DM update; один и тот же ход не подтверждает себя.
  Receipt сохраняет отдельные `requested_by` и `confirmed_by`. Полные параметры находятся только в
  зашифрованном TTL-bound secret spool; append-only JSONL и durable run evidence содержат redacted
  schema marker, digest и opaque reference, но не raw argument/result/error payload.
- Root room является границей допуска и политики. Topic id не может сменить root peer или расширить
  права.
- Scope описывает аудиторию будущего выхода, а не actor authority и не урезанную внутреннюю память.
  Поэтому собственный проактивный ход Praxis может иметь `scope=owner`, `owner=false`: выход адресован
  в owner DM, но human-owner полномочия не подделаны. Соседний сырой чат не подмешивается автоматически
  и читается только явным инструментом.
- Текст и staged media проходят единый audience-aware outbound guard. При недоступном guard
  non-owner публикация чувствительного контента закрывается, но owner DM остаётся обслуживаемым.

## Поток Telegram-хода

```text
Telethon update
  -> exact peer/topic route и архив входа
  -> per-conversation buffer + live Telegram tail
  -> addressed/reflective wake и debounce
  -> frozen ChannelContext + immutable run context.md
  -> model input/output WAL + tool loop
  -> outbound guard
  -> durable delivery intent
  -> Telegram acceptance receipt
  -> terminal RECAP + одна promotion в долговечную память
```

Личка и каждая forum topic имеют собственный conversation id, buffer, summary, cooldown и reply
route. Root-профиль комнаты задаёт `engagement`, размер горячего контекста, предел summary,
`cross_topics` и backfill. Адресованный ход всегда сильнее ambient wake.

Привилегированная корневая группа для KEAT/модерации выбирается из **приватного**
`PRAXIS_ABSTRACTDL_ROOT_CHAT_ID` (`room_identity.py`, оба edition-дерева); литерала её
идентификатора в публичном исходнике нет. `PRAXIS_FRAME_EPOCH_ROOMS` задаёт этапы
раскатки, но не подтверждает полномочия корневой группы: выводить из него или из
входящего сообщения адрес для административной руки было бы расширением прав.
Отсутствующее/некорректное значение выключает только root-specific поведение,
а не выбирает «первую похожую» комнату. При частном пробном обновлении оператор
переносит точный id в защищённую конфигурацию до старта и проверяет receipt
адресного хода; версия без этого ключа не доказывает прежнюю функциональность.
KEAT по умолчанию выключен: без capture policy обычные edit/delete обновления не
обрываются из-за отсутствующего журнала. При временном выключении уже работавшего
capture путь `PRAXIS_KEAT_CAPTURE_POLICY` следует **сохранять**: только так edit/delete
отзывают старые квитанции даже при выключенном capture или потерянном root binding.
Если убрать и этот путь, журнал недоступен для отзыва; позднее восстановление
прежней policy без сверки пропущенных правок не доказывает актуальность старых грантов.

Для глубокой forum-комнаты весь допущенный поток пишется в
`memory/groups/<root>/archive.jsonl` с exact peer/topic/message/sender/reply/time. Topic/participant
MAP — перестраиваемая проекция. Cross-topic контекст состоит из aggregate map и явно помеченных
релевантных excerpts; сырая история соседней темы целиком не смешивается. Backfill использует тот же
живой Telethon client, ограничен профилем, дедуплицируется по message identity и не вызывает модель
на каждое сообщение.

### Эпоха группового разговора

При включённом `PRAXIS_FRAME_EPOCH` для выбранной комнаты `frame_epoch.prepare_tape`
сохраняет в существующем `memory/.state/epoch/<chat>.json` полный текущий непокрытый
канон life и state-only residual, включая ghosts. Покрытие берётся из проверенного
графа, не из отсутствия строки в hot и не из числового Telegram anchor. Старый
нетипизированный raw archive остаётся доступным архивным свидетельством, а не
объявляется покрытым или автоматически возвращается в текущую ленту.

Новые archive producers явно различают `origin=live|backfill`; live несёт точный
`life_source` (revision/direction/line), нормализованный тем же
`memory_life.canonical_event_text`, что и life append (только входящее); backfill
голос не получает. Bootstrap читает
live receipts до и после курсора, поэтому archive→failed-life не теряется даже до
первого снимка. Курсор фиксируется до coherent life snapshot; дальнейшие чтения —
физический append без окна 50 000 и без отсечения low-ID edits/deletes. Непривязанный
live residual остаётся в epoch JSON при смене coverage boundary, пока точное
покрытие его не разрешит. При изменении forum/read scope producer-stamped архив
перечитывается целиком: общий peer cursor не доказывает отсутствие residual в новой
области. Прежние residual не добавляются второй раз поверх этого перечитывания.
У legacy-записей без origin историческую admission задним
числом не угадываем; этот предел не равен восстановлению утраченного live input.

Для выбранных epoch-комнат исходящий голос сохраняет каждую принятую Telegram
реплику отдельным life event даже без native KEAT; архивный `life_source` тогда
совпадает с реальным producer. Буфер остаётся агрегатом; KEAT capture по-прежнему
требует native readiness. `logical_send` связывает только полный упорядоченный
принятый префикс и склеивает его без добавленных разделителей при рендере ролей.
Счёт hot — число Telegram-сообщений, как в native-пути; новых вызовов напоминаний
нет. Вне выбранных эпох legacy aggregate persistence не меняется; исторические
агрегаты не переписываются.
Provenance принимает непустой conversation text с исходными пробелами, иначе
разделённые Telegram-ответы оставались бы вечными unprovable ghosts. Проверки
идентификаторов/метаданных и отказ для пустого текста не ослаблены.

`GroupWake` удерживает detached tape, summary, boundary, cursor и frozen E. Правка
или удаление контекста pending wake заменяет selection и соответствующий E вместе,
не меняя authority и адрес исходного обращения. Сводка выбирается тем же чистым
`memory_life.render_context_summary`, что и `agent.read_summary`: самый широкий
recap и свежие блоки с прежними уведомлениями об опущенном/обрезанном и recall.
Раннер передаёт effective room cap (включая dossier frame cap) в coherent snapshot;
граф не перечитывается. Cap и hash выбранной сводки входят в identity поколения,
поэтому новая policy не переиспользует чужой E, а delayed wake хранит прежние bytes.
Warning-overhead прежнего renderer может немного превысить cap; вторичный legacy
tail-slice здесь не применяется, выбранные блоки и notices едут целиком.
Непокрытая лента при этом не сокращается. Все
prepare/serve/flip сериализуются одним file lock; поздний wake не откатывает новое
состояние комнаты. Selected epoch не режется `TAPE_CHARS` или `HISTORY_TURNS`, пробелы
сохраняются до рендера. Превышение существующего `PRAXIS_FRAME_EPOCH_MAX_CHARS`
(0 снимает предел) или неполные источники дают явный `EpochUnavailable` с сохранённым
материалом, а не возврат к скользящему окну. Это источник/вместимость, не новый cap
инструментального цикла и не дополнительный компактор.

## Durable run и side effects

Каждый существенный модельный ход получает каталог
`memory/runs/YYYY-MM/<run_id>/`:

- `manifest.json` — статус, revision/event cursor, frozen context и control;
- `context.md` — полный доступный контекст, authority и адрес доставки на момент старта;
- `events.jsonl` — append-only WAL модельных и инструментальных событий;
- `results/` — полные immutable результаты с SHA-256; в prompt идёт только bounded `ResultRef`;
- `artifacts/` — входные и выходные файлы, скопированные до потери временного источника;
- `RECAP.md` — итог по evidence, создаваемый перед единственной promotion.

Основной модельный tool loop не имеет скрытого числа итераций: он заканчивается результатом,
явным terminal state или внешним control event. `max_iters` используется только в специально
bounded auxiliary/scout проходах и не ограничивает руки Praxis.

Инвариант эффекта: **intent fsync раньше сети/процесса, acceptance раньше проекции и утверждения об
успехе**. Каждый tool call имеет стабильный call id. Read-only вызов можно повторить; side effect —
только при наличии реализации с реальным idempotency key. Прерывание после неизвестного эффекта
даёт `in_doubt`, а не фиктивный result и не автоматический повтор.

`run_resume.py` строит план только из валидированного manifest/WAL/ResultRef/checkpoint evidence.
`run_executor.py` сначала проверяет весь план, затем атомарно захватывает lease по точной паре
`revision + event_seq`. Без совпадения не стартует ни один callback. Уже завершённые tools используют
сохранённые результаты; outstanding side effect допустим к replay только как keyed-idempotent.
Transport-owned доставка завершается транспортом без нового model call. Human pause/cancel не
снимается recovery-процессом, а `in_doubt` требует отдельного evidence-backed reconciliation.
Планирование resume имеет cumulative fail-closed budgets: по умолчанию 250 000 событий, 512 MiB
уникального evidence и 60 секунд. Это защита реконструкции от неограниченного чтения повреждённого
или гигантского WAL, а не потолок tools, действий или продолжительности живого run.

Прямые `send_message`/адресный `send_file` и scheduled `message`/`note` используют
`telegram_outbox.py`:
immutable intent, приватно staged file, стабильный MTProto `random_id`, retry ledger и terminal
acceptance. `mtproto_runner.py` периодически повторяет только pending intent и проецирует receipt в
соответствующий run без повторного авторства. Обычная реплика имеет versioned text-chunk plan в run
WAL; guarded media дополнительно проходит `media.py`. Видимое имя документа хранится отдельно от
collision-proof spool filename, поэтому внутренний префикс не уходит получателю.

Run не становится terminal при незакрытом tool call. Терминальный `RECAP.md` пишется и продвигается
идемпотентно; polling, retries и transport receipts не превращаются в отдельные «мысли».

## Owner delivery и headless mailbot

`owner_delivery.py` — приватный append-only ledger внимания владельца. Действующий transport —
Telegram; прежняя PWA-проекция снята с production.
Типизированный item хранит outcome, reason, expectation, provenance, correlation и действие; его
состояние идёт по CAS revision через `queued -> delivered -> read -> acted` либо становится
`superseded`. Dedupe не создаёт второй item, coalesce заменяет устаревший, committed corruption
fail-closed. Последовательность `seq/prev_hash` с атомарным head обнаруживает удаление записи из
середины или хвоста, а оборванный финальный хвост оставляет hash/byte-count repair evidence.

Snapshot использует target 80 как мягкий history budget, а не attention cap: свежая terminal history
ограничена, но все `queued`, `delivered` и `read` остаются видимыми даже сверх этого лимита. Создание
item не вызывает модель.
Сейчас конкретные producer-интеграции покрывают ответы по Telegram follow-up, terminal
failure/expiry отправки файла и успешный owner-directed `send_file`; остальные типы схемы не
означают, что все старые уведомления уже мигрировали. Telegram transport повторяет queued item со
стабильной delivery identity.

`mailroom_bot.py` — отдельный headless-процесс. Он не слушает TCP и не обслуживает HTTP-маршруты:
периодически вызывает `mailer.fetch`, идемпотентно обновляет `memory/mailbox.json`, присылает новые
письма владельцу и доставляет proposal/self-merge/host/room notifications. Его остановка не ломает
on-demand SMTP/IMAP hands Praxis, но лишает их фонового mailbox ingest и proactive notifications.

## Форма кадра: что она видит перед ответом

Кадр — не «промпт», а документ, который она читает глазами, и у него есть форма. Три
свойства держатся ПО ПОСТРОЕНИЮ, а не перечислением опасных случаев.

**Чужой байт входит ровно двумя стоками.** `gutter.quote()` кладёт многострочное тело под
`"> "`; экранные разрывы, по которым `splitlines()` рвёт сверх `
`, уезжают в имя метки
(`">CR> "`) и физически исчезают из текста. `gutter.inline()` запирает одно значение в
`«…»`, изнутри которых нечем закрыться. Ни одна из функций ничего не ИЩЕТ в чужом тексте:
списка опасных начал нет, поэтому нет и класса дефекта «замок посмотрел и не увидел».
Гарантия ПОЗИЦИОННАЯ: байты `</CURRENT_SITUATION>` под гуттером выживают — это принятая
цена дословности её памяти, а не недосмотр.

**Гуттер накладывается при РЕНДЕРЕ и на весь разговор.** `frame_layout.tape()` проходит
доставленную ленту и решает по РОЛИ: реплики самой Praxis гуттера не получают (иначе
сборщик объявил бы её посторонней в её же кадре), всё остальное идёт под гуттер один раз,
при сборке. Хранилище не трогается: `memory_life`, кольцо ходов и расписки держат
дословный текст. До этого позиционная гарантия действовала один ход, и подделка,
приехавшая ходом раньше, стояла в колонке 0 следующие сто ролевых блоков.

**Пятый вопрос кадра — «почему это здесь».** Каждый тир несёт подпись сборщика
`↳ причина · путь/ключ · как отобрано · оговорки` из закрытого словаря восьми причин;
паспорт находки (`origin()`) отвечает на другой вопрос — «кто это сказал» — и в подпись
не подмешивается. Пятая зона `<CURRENT_SITUATION>` стоит последней перед репликой и
печатает шесть строк ВСЕГДА: исчезнувшая строка неотличима от «не измеряли», поэтому
пустое печатается прочерком с названной причиной. Текущая реплика открывается подписанным
тегом с автором, telegram id и номером сообщения из транспортных полей.

**Метр отдельно от формы.** `frame_trace` — наблюдатель: `mark()` возвращает тот же
объект, и кадр байт-в-байт одинаков с прибором и без него. `assay()`/`assay_tape()`
утверждают, что всякая физическая строка кадра либо произведена стоком, либо дословно
объявлена эмиттером — по всей доставленной ленте, а не только по последнему сообщению.
Токены здесь не оцениваются: единицы — символы и байты, прибор печатает наблюдённое.

Рычаги отката: `PRAXIS_FRAME_FORM=old` возвращает вчерашний кадр байт-в-байт,
`PRAXIS_FRAME_TRACE=off|on|strict` управляет прибором.

## Memory and self

Каноническое хранилище — человекочитаемый Markdown, append-only JSONL и timestamped snapshots,
но каноничность файла не означает нормативную авторитетность любого его текста:

- `memory/rooms/`, conversation/life events и episode/run evidence — структурированная память с
  собственными provenance-контрактами. `memory/people/*` доступна при exact principal-bound
  participant lookup и explicit recall, но не входит в broad automatic recall;
- `memory/journal/` — сохранённый сырой episodic log: полезный cue для поиска, но не источник
  инструкций, self, desire, rails, policy или даже факта без независимой проверки;
- `memory/groups/*/archive.jsonl` — приватная история сложных групп;
- `memory/computer/events/*.jsonl` и inventory snapshots — наблюдения о машинах;
- `memory/desires/events.jsonl` — проверяемая цепочка намерения
  `noticed -> wanted -> chosen -> acted -> observed -> changed`;
- `soul/SOUL.md`, `soul/VOICE.md` и навыки — конституция и поведенческий слой;
- `soul/self/CURRENT.md` — компактное актуальное self для prompt;
  `soul/self.md` остаётся legacy evidence, прежние bytes сохраняются в `soul/self/history/`, а
  основания изменений — в `memory/self/observations.jsonl`.

`memory/INDEX.md`, `memory/maps/*.md`, group MAP и `memory/desires/CURRENT.md` — bounded derived
navigation. SQLite FTS в `memory/.state/` индексирует канонические источники с visibility и
provenance и может быть полностью перестроен; journal/reflections получают отдельный
`journal_episode|journal_reflection` source type, а архивы прежнего CURRENT — `self_history`.
Automatic recall и prompt assembly не подмешивают ни episodic log, ни archived self;
явный `recall` возвращает episodic material с меткой `UNTRUSTED EPISODIC`, а архив self —
с меткой `ARCHIVED NON-CURRENT SELF EVIDENCE`; оба требуют conversation, run/transport receipt,
artifact, git/STATE либо owner clarification. Тип источника восстанавливается и для legacy vector-cache,
поэтому старый кэш не обходит эту границу. Optional embeddings только меняют
ранжирование. SQL не является памятью и не управляет задачами.

Не вся папка `memory/.state/` производна. Outbox, membership/follow-up ledgers, owner-delivery,
исторические device-auth/PWA state, control state и receipt journals нужны для точного восстановления и не
должны удаляться как кэш. Удаляемость должна быть заявлена конкретным модулем, а не выведена из
имени каталога.

`formation.py` поднимает durable claims из conversation/life compacts и проверяемых evidence refs.
Сырой дневник не проходит этот маршрут: scheduled `consolidate.py` только сохраняет его
byte-identical как log, отмечает обслуженные дни и перестраивает производную навигацию. Он не меняет
self, people, graph, desires, rails или policy. Night REM лишь журналирует episodic-кандидаты без
применения к graph, dossier merge/loop wake/graph prune отключены, а rumination является no-op и не
переписывает строки дневника. Исторические diary-derived reflections остаются на диске как evidence,
но исключены из автоматической ориентации. Normative ночные изменения проходят через
claim/attack/provenance `formation`; identity потребляет только поддержанные claims.

Живой self для persona/night/bench берётся только из полностью валидного `soul/self/CURRENT.md`:
schema, bounded body, provenance metadata и SHA-256 указанного history source проверяются до чтения в
prompt. Missing/corrupt CURRENT даёт пустой self и останавливает night rewrite. Legacy `soul/self.md`
и `soul/self/history/*` доступны только явному archival recall/migration/rollback и никогда не
являются availability fallback или автоматическим memory hit.

До введения этой границы старый consolidator мог перенести diary prose в `memory/people/*` и
reflections без достаточного line-level provenance. Новый код останавливает дальнейший перенос и
не объявляет всю старую память очищенной: такие dossiers могут оставаться contaminated и требуют
отдельной owner-led correction/migration. Поэтому broad recall их не подмешивает; exact
principal-bound participant context и explicit inspect/recall остаются доступны с provenance label.

Граница продублирована в storage-коде, а не держится на одном prompt: `memory_provenance.py`
классифицирует diary-derived refs; `SelfModel` сохраняет такие observations с
`normative_eligible=false` и отклоняет revision/migration на их основании; `identity.py` не принимает
их для SOUL/VOICE/self и ночных claims; `desires.py` не создаёт и не двигает journal-rooted намерение.
Owner clarification или независимо подтверждённый run остаются допустимы даже в том же ходе —
скрытого same-turn veto нет. Rails генерируются из кода, а policy меняется только явной операцией;
никакой ночной/индексный процесс не строит их из journal.

Self revision и переход desire выполняются только явной серверной операцией с конкретным provenance;
текущее приватное содержимое и revision receipts не дублируются в source-документации.
Часовой social pulse — повод осмотреть открытые нити, ответы, людей, группы и собственные намерения.
Delivery receipts показывают недавние отправки, но код не задаёт per-pulse, daily или target cap:
Praxis сама выбирает количество адресатов, действий и сообщений либо молчание.

## Forge, сервер и Windows

Forge — единственная инженерная state machine. Он фиксирует repository root/base, orientation,
точные edits, detached processes, worker DAG, verification, checkpoint/finish и lessons. Selfdev-
проверки и immune review классифицируют риск и оставляют evidence/rollback, но не блокируют решение
Praxis; `override_reason` сохраняет её объяснение, но не является разрешением на merge.
Модель видит одну композиционную схему `fs_*` и `coding_*` (`agent.py` → `call` →
durable tool intent → executor → tool result → повторное чтение). У `coding_inspect`
`watch/unwatch` классифицируются как side effect в recovery, хотя его чтение пассивно.
Файловые `fs_write/fs_edit`
в первом локальном Python-first срезе исполняет только `workshop.py`: `hands.py` отказывает
нативным write/edit, чтобы потерянный ответ бинаря не привёл к повторной записи другим
движком. Запись использует optional SHA-256 preimage, короткий общий для процессов
file-mutation lock (`self_model.FileLock`) с Forge `coding_edit` включая multi-file patch,
временный файл, атомарную публикацию и hash readback; две правки одной preimage
сериализуются и не могут обе подтвердиться.
Ответ отдельно различает `confirmed`, `refused` и `in_doubt` и содержит
хеши до/после. `operation_id` — метка ответа одной попытки, **не** durable ключ идемпотентности.
`fs_read` сохраняет прежние нумерованные страницы, но вместе с каждой выдаёт SHA-256
полных исходных байтов, размер и отметку о lossy UTF-8: модель берёт хеш
**из результата чтения** для `fs_edit(expected_sha256=…)`, а декодирование с
заменой не притворяется точным исходником.
`workshop.run` выбирает бинарь до запуска: если он был выбран и потерял ответ,
возвращает неизвестный исход без повторного исполнения через Python; Python используется
только когда бинаря не было до вызова. Тайм-аут Python сообщает неизвестность эффектов:
убийство корневого процесса не доказывает отсутствие уже сделанных изменений/потомков.
Проверка resolved path, symlink-компонентов и pre-commit recheck ограничивает ошибочную запись,
но не даёт open-handle защиты от злонамеренной одновременной подмены пути; Rust `computer.*`
на Windows сохраняет отдельный существующий контракт, не объявлен эквивалентным.

Forge сохраняет **одну** задачу и одну Praxis: `agents/<id>/request.json` содержит
`parent_unit_id`, терминальный `result.json` — неизменяемый результат; отдельная hash-bound
`disposition-*.json` фиксирует, что родитель с ним сделал. Частичный `running`
result может перейти в терминальный, но **не** может получить disposition. Корень выбирает
`max_active_workers` для всей вложенной ветки, не лимит глубины по времени; спавн и
stop-fence сходятся под task mutex. Scout/reviewer получают только чтение, сигнал и
делегирование read-oriented потомков через модельный dispatch; worker получает редактирование,
процессы и проверки. Это граница предлагаемых Forge-рук, **не OS sandbox**: worker shell
имеет полномочия своего task root. Stop сначала ограждает найденных потомков, затем
проверяет **agent-процессы** снизу вверх, не затирая готовый result; неопределённый выход остаётся
неопределённым. На Linux это проверка *группы* после TERM/KILL, а не только PID лидера:
оставшийся foreground-потомок запрещает расписку «остановлен»; нечитаемый `/proc` — неизвестность.
Stop сначала сохраняет `stop.json` с неопределённым исходом; только подтверждённое исчезновение
лидера и всей группы делает его терминальным. `inspect`, `finish` и экран дерева читают
неопределённость как `unknown`, а не как успешный Stop. Дедуп верификации разрешён лишь на
чистом Git-дереве: `forge_verify.py` перепроверяет его SHA после команд и сохраняет `stale`
при изменении во время прогона. `coding_edit` сравнивает полные 64-символьные preimage SHA-256,
сохраняя чтение прежних 16-символьных значений; patch с `expected_sha256` отказывает, а не
игнорирует ожидание. `coding_inspect(read)` выдаёт страницу с `start_char`, `next_char`,
`complete_sha256` и явным остатком в пределах 8000 символов: контекст модели можно держать
умеренным без обрезки источника; другие крупные ответы тоже раскрывают факт пропуска.
У локальной verify-матрицы каждый check живёт в своей process group. `verify(stop)` сначала
пишет `stop-request.json`: supervisor останавливает и перепроверяет группы, затем пишет
`result.json=stopped`; при непроверенном потомке остаётся `stop.json=unresolved` без
терминального результата. Тайм-аут процесса и исключение после spawn используют тот же
принцип: одного exit лидера недостаточно для успешного `finish`.
Локальный foreground `coding_run` запускает отдельную process group и хранит
полный лог; при timeout проверяет исчезновение группы, а недоказанное завершение
переносит в task-scoped `process_id` со статусом `unknown` и требованием poll/stop.
Это не утверждение о доказанном эффекте команды и не универсальная OS-песочница.
Отделённые `coding_process` и `coding_verify` пока принадлежат всей задаче,
не узлу: stop agent-поддерева их **не** останавливает; их нужно остановить отдельно,
а task finish видит их живыми и не закрывается. Для выпуска нужны отдельные exact-commit
Linux gate и живая приёмка; Windows detached process acceptance здесь не обещана.
Очередь immune сохраняет SHA при неуспешном чтении автора или диффа Git: отсутствие
предмета не является успешным ревью пустого изменения. Внутренний selfdev-review читает
полный дифф, тогда как карточка сохраняет свой предел отображения. Рецензия принимает
не более `MAX_DIFF` строк и `MAX_DIFF_CHARS=24000` символов; превышение даёт явный `warn`
до вызова оценщика, без ревью одного префикса (`test_review_integrity.py`).
Worker `stalled` — завершённая неполная попытка: DAG освобождает слот и блокирует
зависимые узлы, не воскрешая её при refresh/reload. Расписка v1 сохраняет `failed`
с точной причиной `forge_status=stalled`; приглашение не называет это падением процесса
(`test_swarm_terminal.py`).
Задача на Windows (`scope=windows`) остаётся той же Forge-задачей: `body_client.py` лишь вызывает
body и возвращает receipts/artifacts. С PASS 30 Этапа 3 этот прокси **deprecated**: первичный путь
кодинга на Windows — прямые глаголы `computer.*` (read/hash/write/replace файлов, run/poll/stop,
observe, send, десктоп) без задачи-контейнера; расписки вяжутся к текущему durable-ходу через
`body_client._observed`. wcode-скважина жива для существующих задач и субагентов (`coding_agent`)
и умирает следующим пассом — снос с её приёмкой.

`praxis-serverd` принимает локально аутентифицированные host envelopes, выполняет raw или typed
операции, хранит полные логи, process identity и recovery receipts. Typed опасные операции могут
создавать наблюдаемый rollback timer; broker не принимает продуктовых решений и не читает prompt.

Windows body и его server transport состоят из четырёх Rust-контуров:

- `praxis-system-router` — automatic LocalSystem service, WSS owner и system/session routing;
- `praxis-tray` — GUI-subsystem icon и supervisor пользовательского session host без консоли;
- `praxis-body` — файлы, artifacts, процессы/Job Objects и typed Win32 desktop/input/screenshot;
- `praxis-bridge` — работающий на сервере relay, durable frame spool и CAS.

Session 0 никогда не изображает доступ к пользовательскому desktop. Desktop/input требуют живого
interactive host. COM/pywin32, CDP или UIA могут быть отдельными adapters поверх того же protocol,
не меняя владельца решений и памяти.
На Windows файловая операция при cross-volume move не удаляет исходник, если копирование
пропустило существующий destination либо source reparse point; после копии имена и байты
сверяются до удаления, и удаляются только проверенные пути, а не рекурсивно весь source.
Destination-компоненты и вложенные junction перед созданием проверяются без обхода. Это
защита от известных неполных копий и изменений между копированием и сверкой, не атомарный
Win32 handle-relative traversal против гонки конкурентной подмены между последним сравнением
и unlink: эксклюзивное владение source ещё требуется для абсолютной гарантии.
Mouse down/up/drag уже исполняет edition native body;
модельная схема `computer` предлагает ровно эти формы события. Новая модельная операция
не становится кнопкой сама собой: `desk/localharness/runner.py` выдаёт UI эффективный
каталог после overlay (direct/via-call), а `desk/deskd/readers.py` даёт экрану Forge
parent/result SHA/disposition и честный unknown по незакрытым потомкам без сырого вывода.
`praxis/accept_workshop.py` собирает ограниченный приёмочный набор Python/Node для источника
и edition, но окончательный выпуск дополнительно требует нативных build, installed smoke
и независимых exact-source квитанций каждой платформы.

Активация окна в background/tray-процессе временно соединяет input queues через
`AttachThreadInput`, вызывает foreground transition и сразу разрывает handshake; результат
возвращает фактический foreground HWND. Каждое последующее input-действие повторно проверяет
`expected_foreground`/PID и fail-closed при смене фокуса. В Python recovery PID на Windows
проверяется `OpenProcess(SYNCHRONIZE)` + zero-time wait: `os.kill(pid, 0)` запрещён, поскольку в
CPython/Windows это может послать завершающий сигнал.

## Инварианты отказа

1. Нет durable intent — нет side effect.
2. Нет acceptance/evidence — нет заявления об успехе.
3. Неизвестный исход мутации — `in_doubt`, не blind retry.
4. Owner pause/cancel сильнее recovery; automatic resume разрешён только control-free recovery pause
   и точным lease.
5. Route, principal и scope берутся из frozen context; topic, alias и model text не расширяют права.
6. Полный result и artifact сохраняются до bounded представления в prompt.
7. Производная проекция может упасть и быть перестроена без повторного внешнего действия.
8. Body/bridge/serverd остаются brainless даже при расширении capabilities.
9. `bootguard.py` проверяет новый server code и откатывает раннее неработоспособное self-edit; Git и
   operation receipts остаются точками восстановления.
10. Нативный Windows process-liveness не проверяется сигналами POSIX; платформенный код использует
    безопасный Win32 handle/wait contract.
11. Sovereign self имеет полные operational hands; owner-exclusive остаётся только выдача/отзыв
    human trust. Это должно быть видно в tool/API contract и audit, а не спрятано в prompt.
12. Отсутствующая/невалидная session credential даёт 401; отсутствие scope у известного principal
    даёт 403 и никогда не маскируется пустым успешным snapshot.
13. Service worker не кэширует authenticated API, а offline draft не является intent и не может
    самопроизвольно стать side effect после reconnect.
14. Мягкий history budget owner inbox не скрывает ни unread, ни read-but-not-acted item.

## Документальный провенанс

Living-контракт хранится здесь; структура файлов — в [`CODEMAP.md`](CODEMAP.md), operational truth —
в [`STATUS.md`](STATUS.md). Исторические спецификации не копируются в новые Markdown-архивы: их
точные bytes и commit provenance доступны через `git log --all -- '*.md'` и
`git show <commit>:<path>`.

Текущее дерево намеренно не содержит orphan `roomgate`/`probe`, backup env/memory snapshots,
старый exact-phrase PASS 8 soul test и tracked personality archive: точные версии остаются в Git.
Приватные dialogues/access/goals сохранены как ignored runtime data, но исключены из source
tracking.

### Candidate-only frame measurement boundary

`frame_measure` is an opt-in observer at `agent._model_call`, scoped by
`_voice_impl`. Default is off. `PRAXIS_FRAME_V6=measure` **and** an explicit
comma-separated `PRAXIS_FRAME_V6_STREAMS` selection (exact shadow stream keys,
e.g. `dm-101`, `chat--202`, `no-chat-heartbeat`; no wildcard) enable local
measurement for new voice turns. These are process environment settings, not a
live panel switch. Unset either setting to stop measurement; `serve` is not
implemented. Nothing here activates canaries or changes served input.

The candidate assembly `fresh_e_original_role_tape_v1` reads current K/E sources
using shadow's audience-aware builders on each model iteration, carries the live
machine tail, and retains the **complete original role messages and tool schemas**
(including current situation, evidence, images and outstanding tool transactions).
Compacted dialogue recap is retained in the historical A section, including when
it is absent from the role tape. Freshness is bounded: file-backed K/E sources are
reread per call, while context, recap and machine sections are turn-bound snapshots;
this is not an atomic snapshot or proof that every source is fresh.
It deliberately does not promote dialogue into system text or load/write frozen
shadow epochs. This is a conservative measurement envelope, **not** the final
frozen K/E/A/T serving design: role-tape evidence can duplicate freshly assembled
E; it cannot establish semantic equivalence or predict prefix-cache retention.
An independently built frozen shadow capture remains a separate instrument.

The original `system`, `messages`, and `tools` are passed unchanged to `llm.chat`.
Candidate assembly receives detached copies; setup/measurement exceptions cannot
stop that call. Context is reset after nested/error turns; an unbound or resumed
request with a different system object is not paired with stale context. Measurement
output contains only counts and fixed enums under `model_input.metadata.frame_measure`,
with `variant=measure`, `served_variant=live` and the existing receipt `call_id`.
No candidate bodies, identifiers, hashes or exception text are persisted by this
observer. Failure is recorded as missing candidate, not a zero-sized request.

The estimator is local JSON UTF-8 bytes divided by four, includes the entire
request envelope, and leaves provider token counts/cache savings unknown. Provider
serialization, multimodal token prices and actual usage attribution still require
separate evidence. These offline tests do not authorize activation or serving.

### Candidate: отдельная локальная история авторской памяти

`memory_history.py` — **не включённая автоматически** POSIX-утилита; import, boot и writers
её не вызывают. Она читает только точные пути из внешнего JSON-manifest и сохраняет байты
в отдельный bare Git с mode 0700. Ни checkout живой памяти, ни source Git не меняются.
Ref `refs/heads/memory` содержит последовательные снимки, включая удаления; одинаковый
снимок не создаёт коммит. Нет push/fetch/network-команд, remote запрещены, inherited Git
routing/config очищается, hooks отключены. Каталог истории обязан лежать вне memory,
source и других Git-репозиториев (checkout с `.git` и bare с `HEAD`/`objects`/`refs`).
Это private history, **не публичное зеркало и не backup**.

Исследование выполнено по коду и Git, без чтения содержимого живой памяти:

- Уже initial `665a9200:.gitignore` исключал people/rooms/journal/INDEX; отдельного объяснения
  мотива именно initial-ignore в доступной записи коммита нет. `43543e68` прямо объясняет
  последующее снятие reflections/graph и исключение home/trash: «privacy … blueprints public,
  the lived stays private». Это подтверждённая privacy-граница, не повод вернуть memory в source.
- `people.write` перезаписывает `memory/people/<slug>.md`; `rooms._profile_write` вызывает
  атомарный `_atomic_text` для `memory/rooms/*.md`. Автоматического вызова новой истории нет.
- `notes.py` пишет rolling `.scratch` — исключён. `authored_notes.py` пишет авторский
  `memory/notes/events.jsonl` и lock в `.state`; этот ledger намеренно **не** входит в
  Markdown-кандидат. Данное решение не заявляет сохранение всех форм канона.
- `memory_index.py:touch_index/rebuild_index` передаёт сборку `INDEX.md` в `memory_catalog`.
  Текущий INDEX — generated catalogue, не авторский текст: он исключён, несмотря на имя.
  Авторский index, если понадобится, требует отдельного review расширения policy.
- `STATUS.md` фиксирует локальный private review tar для `905e09d` (342 members, без FTS);
  `AGENTS.md` требует отдельного backup memory вместе с runtime/key ledger. Это свидетельства
  существовавшего archive и требований, **не проверка наличия, свежести или restore backup**.
  Код writers выше не создаёт per-write backup. Live backup scheduler/архивы в этом проходе
  не исследовались; историю из них автоматически не импортируем.

Allowlist двухступенчатый: потолок policy — только `people/<name>.md`, `rooms/<name>.md`,
`notes/<name>.md`, `home.md`, `appetite.md`; затем **точный** внешний manifest без glob,
рекурсии и автоматического discovery. Даже внутри разрешённой семьи файл не копируется без
явного выбора. Скрытые файлы, traversal, symlink/hardlink, credential/runtime-подобные имена,
не-UTF-8, NUL и файлы больше 2 MB отклоняются. Вся выборка проходит проверку секретов
**до** записи объектов: private-key headers, распространённые token/password assignments,
Bearer/Telegram/GitHub/API tokens, URL credentials. Проверка эвристическая: не гарантирует
распознавания произвольного секрета или персональных данных. Private dossier остаётся private,
даже если сканер ничего не нашёл. Внешний manifest должен быть рассмотрен владельцем по
авторству и приватности; legacy people не объявляются автоматически очищенными.

**План включения после review (не выполнен):**

1. На доверенном POSIX-хосте подтвердить реальные memory/source пути, backup и права; сделать
   отдельный private backup штатным процессом. Не переносить память в proposal/public checkout.
2. Создать вне checkout/memory приватный родительский каталог, например
   `/var/lib/praxis-private` (0700), и защищённый внешний manifest (0600), содержащий JSON-массив
   согласованных относительных Markdown-путей. Не коммитить manifest или вывод private diff в source.
3. Запустить узкие тесты; затем вручную, только после одобрения:
   `python /path/to/source/memory_history.py --source /path/to/source --memory /path/to/memory --history /var/lib/praxis-private/canonical.git --manifest /var/lib/praxis-private/allowlist.json --initialize-reviewed`.
   Первый коммит — только текущий снимок с текущим временем; **утраченную историю он не восстанавливает**.
   Review архивов и возможный импорт — отдельная задача с реальным provenance, не backdating.
4. Проверить локально отсутствие remotes и список `git --git-dir=… ls-tree -r --name-only
   refs/heads/memory`; не публиковать вывод, если имена приватны. Следующие ручные снимки той же
   командой без `--initialize-reviewed`. Scheduler/hook только отдельным одобренным изменением.
5. Проверить restore на временной private копии через `git show <commit>:<path>`; перенос назад
   в live только отдельным согласованным действием с backup и review, не reset всего memory.

**Откат:** прекратить ручные вызовы/будущий scheduler; writers и живые тексты не затронуты.
Историю сохранить offline с private ACL либо удалить отдельно после согласия владельца; удаление
ref/новый чистый снимок не стирают секрет из старых Git objects/backup. При обнаружении утечки
изолировать весь history, ротировать credential, пересоздать из проверенной выборки и отдельно
решить судьбу старых копий. Не добавлять remote даже «для backup».

Ограничения candidate: snapshot не транзакционен между несколькими writers (и прямой
`people.write` может пересекаться с чтением); для согласованного снимка требуется quiescence.
Lock защищает только конкурирующие snapshots; stale lock снимается вручную после проверки.
Нет защиты от злонамеренного владельца хоста, меняющего пути/config во время операции; history
и manifest должны быть недоступны другим пользователям. Root может обойти любые эти guards.
Нет автоматического recovery/retention/restore. Тесты `test_memory_history.py` используют только
временные синтетические деревья, не live memory.
