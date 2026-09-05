// Устройство: как этот агент работает, простыми словами, с разбором живого
// хода и живым списком рук — снимок кода, не пересказ.
import { api } from "../api";
import { esc, fmtN, fmtTime, md, q, safeRender } from "../lib";
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
  ["труба frame.desk.v1 → deskapp.py", "Всё, что окно показывает, приезжает по одной трубе (запросы и живые события). deskapp — читатель дерева: только файлы, никаких замков раннера. Тот же протокол работает с удалённым харнессом."],
  ["runner.py — локальный харнесс", "Слушает записки окна и Telegram и запускает ход агента (voice_turn_envelope из дерева). Своей логики хода у руннера нет: только транспорт и конфиг."],
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
  // `windows` — строка самой ограды (`fence.WINDOWS_TRUTH`): что на самом деле
  // с управлением окнами. Раньше про окна на экранах владельца стояла
  // выдумка окна, и она пережила исправление в харнессе именно потому, что
  // была копией. Здесь копии нет — только то, что прислал снимок.
  sandbox?: { enabled?: boolean; container?: boolean; reason?: string; windows?: string };
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

export async function render(container: HTMLElement): Promise<void> {
  const [aR, pR, mR] = await Promise.allSettled([api<Anatomy>("/api/anatomy"), api("/api/pulse"), loadMode()]);
  if (aR.status === "rejected") throw aR.reason;
  const a = aR.value;
  const spend = spendHTML(pR.status === "fulfilled" ? pR.value : null);
  const modeBox = modeHTML(mR.status === "fulfilled" ? mR.value : null);
  const modeName = mR.status === "fulfilled" && mR.value.name ? `режим: <b>${esc(mR.value.title)}</b> · ` : "";
  const tools = a.tools || [];
  const intro = INTRO.map(
    ([h, t]) => `<details class="fold" open><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`,
  ).join("");
  const layers = LAYERS.map(
    ([h, t]) => `<details class="fold"><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`,
  ).join("");
  const meta = tools.length
    ? `<p class="muted">Транспорты: ${esc((a.transports || []).join(" + "))} · ${modeName}песочница: ${esc(a.sandbox ? (a.sandbox.container ? "shell в контейнере" : a.sandbox.enabled ? "без контейнера" : "выключена") : "?")}${a.sandbox?.reason ? " · " + esc(a.sandbox.reason) : ""}${a.sandbox?.windows ? " · " + esc(a.sandbox.windows) : ""} · мозг: <b>${esc(a.model?.model || "?")}</b> (${esc(a.model?.framework || "?")})
       · рук предложено: <b>${tools.length}</b> · снято ${esc(fmtTime(a.written_at))}. Живой список сборщика, не пересказ.</p>`
    : '<p class="muted">Снимка ещё нет: руннер пишет его при старте.</p>';
  container.innerHTML = `<div class="center">
    ${meta}${modeBox}${spend}${intro}
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
  const d = await api("/api/run/" + encodeURIComponent(pick.id));
  const m = d.manifest || {};
  const rows: string[] = [];
  const lesson = (key: string, html: string) =>
    rows.push(`<div class="ev-step">${html}<div class="lesson">${esc(LESSON[key])}</div></div>`);
  rows.push(`<div class="row"><b>${esc((m.goal || "").split("\n")[0].slice(0, 110))}</b> <span class="muted mono">${esc(pick.id)} · ${fmtTime(m.created_at)}</span></div>`);
  let thought = 0;
  for (const it of d.iterations || []) {
    const u = it.usage || {};
    thought += 1;
    lesson(
      thought === 1 ? "think_first" : "think",
      `думает · ${esc(it.model || "")}${it.ms != null ? ` · ${(it.ms / 1000).toFixed(1)} с` : ""}${u.in != null ? ` · вход ${fmtN(u.in)}${u.cache_read ? ` (из кэша ${fmtN(u.cache_read)})` : ""} → ${fmtN(u.out || 0)}` : ""}`,
    );
    for (const t of it.tools || []) {
      const args = t.args ? JSON.stringify(t.args) : "";
      const head = (t.result && (t.result.head || t.result.tail)) || "";
      const key = t.tool === "reply" ? "reply" : t.tool === "end_turn" ? "end_turn" : "hand";
      lesson(key, `<b class="hand mono">${esc(t.tool || "?")}</b> ${args ? `<span class="muted">${esc(args.slice(0, 90))}${args.length > 90 ? "…" : ""}</span>` : ""}${head ? `<div class="muted">→ ${esc(String(head).slice(0, 130))}</div>` : ""}`);
    }
  }
  const term = m.terminal || {};
  lesson("terminal", `<b>${esc(m.status || "")}</b>${term.reason ? ` <span class="muted">· ${esc(String(term.reason).slice(0, 100))}</span>` : ""}`);
  box.innerHTML = rows.join("");
}
