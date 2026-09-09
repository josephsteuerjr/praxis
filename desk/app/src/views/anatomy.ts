// Устройство: как этот агент работает, простыми словами, с разбором живого
// хода и живым списком рук — снимок кода, не пересказ.
import { api } from "../api";
import { esc, fmtK, fmtN, fmtTime, md, plural, q, safeRender } from "../lib";
import { stepsHTML, type RunDetail } from "../panel";
import { loadMode, type ModeState } from "../mode";
import { S } from "../state";

const INTRO: Array<[string, string]> = [
  [
    "Что происходит, когда ты пишешь",
    "Сообщение сначала ложится в память (событие жизни и архив комнаты): восприятие пишет раньше, чем думается. " +
      "Потом собирается кадр — всё, что модель увидит этим ходом. Потом модель думает и действует руками, по очереди, " +
      "сколько нужно. Каждый шаг оставляет расписку; ход закрывается решением агента (рука end_turn), а не тем, что ему нечего сказать.",
  ],
  [
    "Кадр — K, E, A, T",
    "K — конституция (soul/SOUL.md: кто я, зачем, чего не делаю). E — эпоха: замороженный слепок знаний о себе, " +
      "меняется только на названных границах. A — накопитель: разговор, только дописывается. T — текущее: кто говорит " +
      "и что пришло сейчас. K, E и A стабильны байт в байт, провайдер кэширует их как префикс, поэтому повторный ход стоит " +
      "в разы дешевле. Живые слепки — в разделе «Кадр».",
  ],
  [
    "Руки",
    "Рука — функция с именем, описанием и схемой аргументов. Список ниже — те же байты, что видит модель: описание руки " +
      "и есть условие её вызова, другого правила нет. Расписка каждого вызова видна в ходах справа от разговора.",
  ],
  [
    "Память",
    "Файлы, не база: события жизни, архивы комнат, дневник, досье людей, леджер желаний. Рука recall ищет по всему " +
      "этому; что она находит, видно в шагах хода.",
  ],
  [
    "Навыки",
    "Навыки — собственные заметки-инструкции агента (soul/skills/*.md): он пишет их сам рукой write_skill, когда чему-то " +
      "научился. Это память о том, как делать, в отличие от памяти о том, что было.",
  ],
];

const LAYERS: Array<[string, string]> = [
  ["helene.exe — оболочка (Rust, Tauri)", "Окно, значок у часов, уведомления. Сама не думает и не переписывается: собрана один раз и поднимает всё остальное тихими дочерними процессами по helene.json."],
  ["канал frame.desk.v1 → deskapp.py", "Всё, что окно показывает, приезжает по одному каналу (запросы и живые события). deskapp — читатель дерева: только файлы, никаких замков раннера. Тот же протокол работает с удалённым харнессом."],
  ["runner.py — локальный харнесс", "Слушает записки окна и Telegram и запускает ход агента (voice_turn_envelope из дерева). Своей логики хода у руннера нет: только транспорт и конфиг."],
  ["helene-body.exe и helene-bridge.exe — тело", "Окна, экран, клавиатура и мышь, файлы и процессы для руки computer. Харнесс поднимает обоих рядом с собой в сессии владельца, снаружи ограды; включает и выдаёт права владелец в Настройках («Управление компьютером»). Мозга внутри нет: тело исполняет то, что прислала рука, и возвращает расписку."],
  ["transport.py и botapi.py — двери", "Оба наполняют один словарь крючков, тот же, которым агент держит Telegram. Окно — одна дверь, бот — вторая. Организм один: общая память, общий кадр."],
  ["дерево агента", "agent.py — ход и руки; frame_shadow.py — кадр; memory_life.py — события жизни; desires.py — желания; llm.py — мозг, любой OpenAI- или Anthropic-совместимый адрес."],
];

const LESSON: Record<string, string> = {
  think_first: "Модель получила кадр. Вход почти целиком из кэша: это стабильный префикс K, E, A; платим по-настоящему только за хвост T.",
  think: "Ещё один поворот цикла: модель увидела результат руки и решает, что дальше.",
  hand: "Рука. Модель выбрала её сама, прочитав описание. Результат вернётся ей строкой на следующем повороте.",
  reply: "Слово наружу. Реплика уходит рукой, а не «последним текстом»: расписка называет канал и id, это защита от «сказала, но не отправилось».",
  end_turn: "Конец хода — поступок с названным исходом, а не отсутствие действия.",
  terminal: "Терминальная расписка прогона: статус, причина, RECAP. По ней ход можно разобрать и через месяц.",
};

interface Anatomy {
  written_at?: string;
  agent_name?: string;
  model?: { model?: string; framework?: string };
  transports?: string[];
  // `windows` — строка самой ограды (слова ей даёт `body.windows_truth()`):
  // что на самом деле с управлением окнами. Раньше про окна на экранах
  // владельца стояла выдумка окна, и она пережила исправление в харнессе
  // именно потому, что была копией. Здесь копии нет — только то, что прислал
  // снимок.
  sandbox?: { enabled?: boolean; container?: boolean; reason?: string; windows?: string };
  // Тело руки `computer` (localharness/body.py): включено ли владельцем, есть
  // ли exe в поставке, подключилось ли на момент снимка, какие права выданы.
  computer?: { enabled?: boolean; available?: boolean; reason?: string; port?: number; scopes?: string[]; connected?: boolean | null };
  tools?: Array<{ name: string; desc?: string; params?: string[]; required?: string[] }>;
  skills_index?: string;
  knobs?: Record<string, unknown>;
}

/**
 * Расход за сутки. Труба считает calls_day/cache_day на каждый запрос пульса, а
 * окно их не показывало нигде — владелец-непрограммист не мог внутри программы
 * ответить «сколько я сегодня потратил». Это ещё не полный счёт (токены по
 * ролям и моделям лежат в memory/.state/usage.json, ручки на них у трубы нет),
 * но это честные числа вместо молчания.
 */
function spendHTML(p: { calls_day?: number | null; cache_day?: number | null; cache_day_hours?: number; cache_now?: number | null } | null): string {
  if (!p || p.calls_day == null) return "";
  const parts = [`вызовов модели: <b>${fmtN(p.calls_day)}</b>`];
  if (p.cache_day != null) parts.push(`доля кэша: <b>${p.cache_day}%</b>`);
  if (p.cache_now != null) parts.push(`сейчас: <b>${p.cache_now}%</b>`);
  const win = p.cache_day_hours ? ` за последние ${fmtN(p.cache_day_hours)} ч` : " за сутки";
  return `<h3 class="section-title">Расход${esc(win)}</h3>
    <div class="card">${parts.join(" · ")}
      <div class="muted" style="margin-top:6px">Чем больше доля кэша, тем дешевле ход: K, E и A стабильны байт в байт, провайдер считает их как префикс.</div>
    </div>`;
}

/**
 * Режим агента — рядом с песочницей, потому что это про одно и то же: в каких
 * правах живёт агент. Тексты приходят готовыми из `/api/mode`; свой пересказ
 * здесь означал бы, что в Настройках владелец выбирает одно, а тут читает другое.
 * Снимок анатомии говорит, что ВЫШЛО (ограда встала или нет), режим — что было
 * ВЫБРАНО; расхождение этих двоих и есть то, что стоит увидеть.
 */
function modeHTML(m: ModeState | null): string {
  if (!m || !m.name) return "";
  const svc = m.service_installed === null ? "спросить не удалось" : m.service_installed ? "установлена" : "не установлена";
  const facts = [
    `ограда песочницы: ${m.sandbox ? "включена" : "выключена"}`,
    `служба Windows: ${svc}`,
    // Две галочки службы — разные вопросы, и на экране они стоят порознь.
    // `session0` — права системы агенту; `firewall` — правило брандмауэра для
    // кнопки «Телефон». Про брандмауэр говорим только когда он ВЫКЛЮЧЕН: это
    // выбор владельца против умолчания, и по нему кнопка «Телефон» под службой
    // ведёт себя иначе.
    m.session0 ? "нулевая сессия разрешена" : "",
    m.firewall_set === false ? "правило брандмауэра служба не ставит" : "",
    m.legacy_service ? "в файле режимом записана служба — старая запись, ограда выведена отдельно" : "",
    m.source ? `источник: ${m.source}` : "",
  ].filter(Boolean);
  const warn = (m.session0 && m.session0_warning ? [m.session0_warning] : []).concat(m.notes);
  return `<h3 class="section-title">Режим</h3>
    <div class="card">
      <p><b>${esc(m.title)}</b> — ${esc(m.text)}</p>
      <p class="muted">${esc(facts.join(" · "))}</p>
      ${warn.map((n) => `<p class="receipt err">${esc(n)}</p>`).join("")}
      <p class="muted">Сменить режим — «Настройки», карточка «Режим».</p>
    </div>`;
}

interface CutRow { group: string; calls: number; runs: number; cache_ratio: number | null; output_tokens: number; median_ms: number; p90_ms: number; cuts: number }
interface FrameCuts { days: number; summary: { calls: number; runs: number; cache_ratio: number | null; cached_tokens: number; input_tokens: number; output_tokens: number; cuts: number }; by: Record<string, CutRow[]> }

// Все семь осей deskd/frame_cuts.py: до 0.5.0 экран рисовал три, а role/model/frame_mode/day
// считались и были видны только через `python frame_stats.py --by …` в дереве.
const CUT_AXES: Array<[string, string]> = [
  ["iteration", "первая итерация против продолжений"],
  ["hand", "какой рукой ответила итерация"],
  ["kind", "род прогона"],
  ["role", "роль вызова"],
  ["model", "модель"],
  ["frame_mode", "режим кадра"],
  ["day", "по дням"],
];

/** Кэш по группам действий: взвешенная доля (Σ из кэша / Σ входа), не среднее процентов. */
function cutsHTML(c: FrameCuts | null): string {
  if (!c || !c.summary.calls) return "";
  const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);
  const sec = (ms: number) => `${(ms / 1000).toFixed(1)} с`;
  const table = (rows: CutRow[]) => `<table class="grid"><tr><th>группа</th><th>вызовов</th><th>кэш</th><th>ответ</th><th>медиана</th><th>обрывов</th></tr>${rows
    .slice(0, 12)
    .map((r) => `<tr><td>${esc(r.group)}</td><td>${r.calls}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td><td>${sec(r.median_ms)}</td><td>${r.cuts || ""}</td></tr>`)
    .join("")}</table>`;
  const s = c.summary;
  return `<h3 class="section-title">Кэш по группам действий <span class="muted">${c.days} дней</span></h3>
    <p class="muted">${s.calls} вызовов в ${s.runs} прогонах, из кэша ${pct(s.cache_ratio)} входа (${fmtK(s.cached_tokens)} из ${fmtK(s.input_tokens)}), ответ ${fmtK(s.output_tokens)}${s.cuts ? `, обрывов потолком ${s.cuts}` : ""}. Доля кэша считается по сумме токенов группы, а не как среднее процентов: первый кадр хода тяжёлый, продолжения лёгкие.</p>
    ${CUT_AXES.map(([axis, title]) => (c.by[axis]?.length ? `<details class="fold" ${axis === "iteration" ? "open" : ""}><summary><b>${esc(title)}</b></summary><div class="fold-body">${table(c.by[axis])}</div></details>` : "")).join("")}`;
}

interface SpendRow { calls: number; runs: number; input_tokens: number; cached_tokens: number; fresh_tokens: number; output_tokens: number; total_tokens: number; cache_ratio: number | null; seconds: number; errors: number; kinds: Record<string, number>; models: Record<string, number> }
interface SpendChat extends SpendRow { chat_id: string; title: string }
interface SpendPerson extends SpendRow { who: string; name: string; chats: string[] }
interface SpendRun extends SpendRow { run_id: string; kind: string; chat_id: string; chat_title: string; who: string; who_name: string; goal_head: string; first_ts: number; last_ts: number; status: string }
interface Spend { days: number; summary: SpendRow & { bound_calls: number; unbound_calls: number; fields_in_log: Record<string, boolean> }; by_chat: SpendChat[]; by_person: SpendPerson[]; by_run: SpendRun[]; by_kind: Array<SpendRow & { kind: string }>; unbound: Array<SpendRow & { role: string }> }

const KIND_RU: Record<string, string> = { chat_turn: "ход в чате", task_window: "окно задачи", heartbeat: "будильник", forge_event: "Forge", wake: "пробуждение" };

/** Ревизия расхода по чатам, людям и задачам (слово владельца 08.09): токены, не деньги. */
function spendCutsHTML(s: Spend | null): string {
  if (!s || !s.summary.calls) return "";
  const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);
  const cell = (r: SpendRow) => `<td>${r.calls}</td><td>${r.runs || ""}</td><td>${fmtK(r.total_tokens)}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td>`;
  const head = `<tr><th>кто / где</th><th>вызовов</th><th>ходов</th><th>токенов</th><th>кэш</th><th>ответ</th></tr>`;
  // Ключ рядом с именем — чтобы различить двух одинаково названных. Поэтому он
  // показывается, ТОЛЬКО когда что-то добавляет: у комнаты без имени читалка
  // отдаёт заголовком сам id («window-76f00fa5 window-76f00fa5» в таблице 09.09),
  // а у человека принципал бывает заглушкой «unknown» — она не различает никого.
  const idTail = (name: string, id: string) =>
    id && id !== name && !["praxis:self", "unknown", "?", ""].includes(id)
      ? ` <span class="muted mono">${esc(id)}</span>`
      : "";
  const chats = `<table class="grid">${head}${s.by_chat
    .slice(0, 20)
    .map((r) => `<tr><td>${esc(r.title || r.chat_id || "—")}${idTail(r.title || r.chat_id, r.chat_id)}</td>${cell(r)}</tr>`)
    .join("")}</table>`;
  const people = `<table class="grid">${head}${s.by_person
    .slice(0, 20)
    .map((r) => `<tr><td>${esc(r.name || r.who)}${idTail(r.name || r.who, r.who)}${r.chats.length ? `<div class="muted">${esc(r.chats.join(" · "))}</div>` : ""}</td>${cell(r)}</tr>`)
    .join("")}</table>`;
  const when = (r: SpendRun) => {
    const d = new Date((r.first_ts || 0) * 1000);
    return isNaN(d.getTime()) ? "" : d.toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };
  const runs = `<table class="grid"><tr><th>задача (ход)</th><th>вызовов</th><th>токенов</th><th>кэш</th><th>ответ</th></tr>${s.by_run
    .slice(0, 25)
    .map((r) => `<tr><td><b>${esc(KIND_RU[r.kind] || r.kind || "ход")}</b> · ${esc(when(r))}${r.chat_title ? ` · ${esc(r.chat_title)}` : ""}${r.who_name && r.who !== "praxis:self" ? ` · ${esc(r.who_name)}` : ""}${r.goal_head ? `<div class="muted">${esc(r.goal_head)}</div>` : ""}</td><td>${r.calls}</td><td>${fmtK(r.total_tokens)}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td></tr>`)
    .join("")}</table>`;
  const unbound = s.unbound.length
    ? `<p class="muted">Вне прогонов (не приписать ни чату, ни человеку): ${s.unbound.map((u) => `${esc(u.role)} — ${u.calls} ${plural(u.calls, "вызов", "вызова", "вызовов")}, ${fmtK(u.total_tokens)}`).join("; ")}.</p>`
    : "";
  const sum = s.summary;
  const source = sum.fields_in_log.chat ? "чат и человек записаны в самом журнале вызовов" : "чат и человек восстановлены через манифесты прогонов (ядро ещё не пишет их в журнал вызовов)";
  return `<h3 class="section-title">Расход по чатам, людям и задачам <span class="muted">${s.days} дней</span></h3>
    <p class="muted">${sum.calls} вызовов модели, ${fmtK(sum.total_tokens)} токенов (из кэша ${pct(sum.cache_ratio)} входа, ответ ${fmtK(sum.output_tokens)}); ${sum.bound_calls} вызовов внутри ходов, ${sum.unbound_calls} вне. Считаются токены, не деньги: прайс-листа по живым моделям в дереве нет. Источник: ${source}.</p>
    <details class="fold" open><summary><b>по чатам</b></summary><div class="fold-body">${chats}</div></details>
    <details class="fold"><summary><b>по людям</b></summary><div class="fold-body">${people}<p class="muted">«Сама, без человека» — её собственные ходы: будильник, окна задач, Forge. Незнакомый id показан числом: имён в журнале нет и не будет, только Telegram-id.</p></div></details>
    <details class="fold"><summary><b>по задачам</b> <span class="muted">самые дорогие ходы</span></summary><div class="fold-body">${runs}</div></details>
    ${unbound}`;
}

export async function render(container: HTMLElement): Promise<void> {
  const [aR, pR, mR, cR, sR] = await Promise.allSettled([api<Anatomy>("/api/anatomy"), api("/api/pulse"), loadMode(), api<FrameCuts>("/api/frame-stats?days=7"), api<Spend>("/api/spend?days=7")]);
  if (aR.status === "rejected") throw aR.reason;
  const a = aR.value;
  const spend = spendHTML(pR.status === "fulfilled" ? pR.value : null);
  const modeBox = modeHTML(mR.status === "fulfilled" ? mR.value : null);
  const cutsBox = cutsHTML(cR.status === "fulfilled" ? cR.value : null) + spendCutsHTML(sR.status === "fulfilled" ? sR.value : null);
  const modeName = mR.status === "fulfilled" && mR.value.name ? `режим: <b>${esc(mR.value.title)}</b> · ` : "";
  const tools = a.tools || [];
  const intro = INTRO.map(
    ([h, t]) => `<details class="fold" open><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`,
  ).join("");
  const layers = LAYERS.map(
    ([h, t]) => `<details class="fold"><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`,
  ).join("");
  const body = a.computer
    ? ` · тело: ${esc(!a.computer.enabled ? "выключено владельцем" : !a.computer.available ? "нет в поставке" : a.computer.connected === true ? `подключено, мост 127.0.0.1:${a.computer.port}` : a.computer.connected === false ? "не отвечает" : "поднималось на старте")}${a.computer.enabled && a.computer.scopes ? ` (права: ${esc(a.computer.scopes.join(", ") || "нет")})` : ""}`
    : "";
  const meta = tools.length
    ? `<p class="muted">Транспорты: ${esc((a.transports || []).join(" + "))} · ${modeName}песочница: ${esc(a.sandbox ? (a.sandbox.container ? "shell в контейнере" : a.sandbox.enabled ? "без контейнера" : "выключена") : "?")}${a.sandbox?.reason ? " · " + esc(a.sandbox.reason) : ""}${a.sandbox?.windows ? " · " + esc(a.sandbox.windows) : ""}${body} · мозг: <b>${esc(a.model?.model || "?")}</b> (${esc(a.model?.framework || "?")})
       · рук предложено: <b>${tools.length}</b> · снято ${esc(fmtTime(a.written_at))}. Живой список сборщика, не пересказ.</p>`
    : '<p class="muted">Снимка ещё нет: руннер пишет его при старте.</p>';
  container.innerHTML = `<div class="center">
    ${meta}${modeBox}${spend}${intro}
    ${cutsBox}
    <h3 class="section-title">Разбор живого хода</h3>
    <p class="muted">Не пример из документации, а последний настоящий ход этого агента, шаг за шагом, с пояснением каждого шага.</p>
    <details class="fold" id="lesson-box"><summary><b>Разобрать последний ход</b></summary><div class="fold-body ev-steps" id="lesson-steps"></div></details>
    <h3 class="section-title">Из чего это собрано</h3>${layers}
    ${tools.length ? `<h3 class="section-title">Руки <span class="muted">${tools.length}</span></h3>
      <input id="tool-filter" class="field-input" placeholder="поиск по рукам…" style="width:100%;margin-bottom:10px">
      <div id="tool-list"></div>` : ""}
    ${a.skills_index ? `<h3 class="section-title">Навыки</h3><div class="card md">${md(a.skills_index)}</div>` : '<p class="muted" style="margin-top:18px">Навыков пока нет: агент напишет их сам, когда чему-то научится.</p>'}
    ${Object.keys(a.knobs || {}).length ? `<h3 class="section-title">Ручки среды</h3><table class="grid">${Object.entries(a.knobs!).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(String(v))}</td></tr>`).join("")}</table>` : ""}
  </div>`;
  const lessonBox = q<HTMLDetailsElement>("#lesson-box", container);
  lessonBox.addEventListener("toggle", () => {
    // Голый `void` оставлял складку в «ищу последний завершённый ход…» навсегда.
    if (lessonBox.open) {
      const box = q<HTMLElement>("#lesson-steps", container);
      safeRender(box, () => renderLesson(box));
    }
  });
  const filter = container.querySelector<HTMLInputElement>("#tool-filter");
  if (filter) {
    const list = q<HTMLElement>("#tool-list", container);
    const draw = () => {
      const needle = filter.value.trim().toLowerCase();
      const rows = tools.filter((t) => !needle || t.name.toLowerCase().includes(needle) || (t.desc || "").toLowerCase().includes(needle));
      list.innerHTML =
        rows
          .map((t) => {
            const first = (t.desc || "").split(/(?<=[.!?])\s/)[0] || "(без описания)";
            const params = (t.params || []).map((p) => `<span class="mono ${(t.required || []).includes(p) ? "" : "muted"}">${esc(p)}</span>`).join(", ");
            return `<details class="fold"><summary><code>${esc(t.name)}</code> <span class="muted">${esc(first.slice(0, 110))}</span></summary>
            <div class="fold-body" style="white-space:pre-wrap">${esc(t.desc || "")}${params ? `<div class="muted" style="margin-top:6px">аргументы: ${params}</div>` : ""}</div></details>`;
          })
          .join("") || '<div class="empty">ничего не нашлось</div>';
    };
    filter.addEventListener("input", draw);
    draw();
  }
}

async function renderLesson(box: HTMLElement) {
  box.innerHTML = '<div class="muted">ищу последний завершённый ход…</div>';
  const runs = S.runs.length ? S.runs : await api<typeof S.runs>("/api/runs?limit=30");
  const pick = runs.find((r) => r.kind === "chat_turn" && r.status === "done");
  if (!pick) {
    box.innerHTML = '<div class="muted">завершённых ходов ещё нет — напиши агенту и вернись сюда</div>';
    return;
  }
  const d = await api<RunDetail>("/api/run/" + encodeURIComponent(pick.id));
  const m = d.manifest || {};
  // Та же разметка шагов, что в панели хода, плюс пояснение под каждым шагом.
  box.innerHTML =
    `<div class="row"><b>${esc((m.goal || "").split("\n")[0].slice(0, 110))}</b> <span class="muted mono">${esc(pick.id)} · ${fmtTime(m.created_at)}</span></div>` +
    stepsHTML(d, { lesson: LESSON });
}
