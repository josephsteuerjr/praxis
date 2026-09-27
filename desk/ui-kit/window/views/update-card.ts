// Карточка «Обновление» на экране «Система» — окно к серверу (27.09).
//
// Жалоба Дмитрия К: агент на сервере не мог обновить свой контейнер изнутри, и каждый
// шаг шёл кругом через человека. Теперь рядом с контейнером живёт исполнитель
// (`server/updater`), агент кладёт ему план, а человек здесь видит, ЧТО именно
// поставится — версия, архив, сумма, что уйдёт в копию, — и говорит «да» или «нет».
//
// Модуль чистый (без импортов): его рисует `supervisor.ts`, а проверяет стенд
// `app/test/update-card.test.mjs` в node без сборки.
//
// ⚠ Чего здесь нет намеренно. Кнопки «обновить сейчас» без показа: «Подготовить»
// только кладёт план, а «Подтвердить» появляется, когда исполнитель уже сверил выпуск и
// выдал расписке одноразовый ключ, — «да» относится к показанному, а не к плану вообще.
//
// Испытание (27.09): новая версия поднята и прошла механику, и теперь агент сам проверяет
// себя в ней. Владельцу здесь видно, что стало с правками агента в его коде, и есть два
// слова поверх агентского — «Принять» и «Откатить».

export interface UpdateCheck {
  name: string;
  title?: string;
  ok: boolean;
  note?: string;
}

export interface UpdateReceipt {
  id: string;
  state: string;
  note?: string;
  step?: string;
  steps?: Array<{ at: string; step: string; ok: boolean; note?: string }>;
  from_version?: string;
  to_version?: string;
  release?: { size?: number; sha256?: string; notes?: string; tag?: string; html_url?: string };
  backup?: { mode?: string; words?: string; data_bytes?: number; free_bytes?: number };
  checks?: UpdateCheck[];
  nonce?: string;
  awaiting_until_utc?: string;
  confirmed?: { by?: string; words?: string; at_utc?: string };
  plan?: { reason?: string; asked_by?: string; chat?: string; version?: string; backup?: string };
  finished_utc?: string;
  rollback?: { ok?: boolean; notes?: string[] };
  code_preview?: { mounted?: boolean; base?: boolean; edited?: number; files?: string[]; note?: string };
  agent_code?: {
    mounted?: boolean;
    edited?: string[];
    carried?: string[];
    merged?: string[];
    conflicts?: Array<{ path: string; why: string }>;
    skipped?: Array<{ path: string; why: string }>;
    folder?: string;
    summary?: string;
    no_base?: string;
    note?: string;
  };
  trial?: {
    key?: string;
    until_utc?: string;
    minutes?: number;
    extended?: number;
    verdict?: { verdict?: string; by?: string; words?: string };
  };
}

export interface UpdaterBeat {
  present: boolean;
  alive: boolean;
  ok: boolean;
  why: string;
  busy?: string;
  current?: string;
  latest?: { version?: string; checked_utc?: string; why?: string };
  newer?: boolean;
  command?: string;
}

export interface UpdateState {
  updater: UpdaterBeat;
  receipt: UpdateReceipt | null;
  history?: Array<{ id?: string; state?: string; from_version?: string; to_version?: string; finished_utc?: string; note?: string }>;
}

const ACTIVE = ["checking", "awaiting", "confirmed", "running", "trial"];

const STATE_WORDS: Record<string, string> = {
  checking: "исполнитель сверяет план",
  awaiting: "ждёт твоего «да»",
  confirmed: "«да» получено — начинается",
  running: "идёт обновление",
  trial: "испытание: агент проверяет себя в новой версии",
  refused: "план не принят",
  declined: "отклонено",
  expired: "истёк без ответа",
  superseded: "заменён новым планом",
  done: "прошло",
  rolled_back: "не прошло — откачено",
  failed: "не прошло",
};

const WHO: Record<string, string> = {
  agent: "агент",
  "owner-window": "ты, из окна",
  window: "ты, кнопкой в окне",
  "owner-words": "ты, словами агенту",
};

function esc(text: unknown): string {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

/** Сколько ещё до срока — словами («через 24 ч», «через 18 мин»), не часами без даты. */
function inTime(until: string | undefined, now: number): string {
  const at = Date.parse(until || "");
  if (!Number.isFinite(at)) return "";
  const minutes = Math.round((at - now) / 60_000);
  if (minutes >= 90) return `через ${Math.round(minutes / 60)} ч`;
  return minutes >= 1 ? `через ${minutes} мин` : "вот-вот";
}

/** Сколько ещё план ждёт ответа — словами, а не часами без даты (истекает через сутки). */
function expires(until: string | undefined, now: number): string {
  const at = Date.parse(until || "");
  if (!Number.isFinite(at)) return "";
  const hours = Math.round((at - now) / 3600_000);
  return hours >= 1 ? `без ответа план истечёт через ${hours} ч` : "без ответа план вот-вот истечёт";
}

function size(bytes: number | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024 ** 3) return Math.round(bytes / 1024 ** 2) + " МБ";
  return (bytes / 1024 ** 3).toFixed(1) + " ГБ";
}

/** Идёт ли сейчас что-то, за чем окну стоит следить часто. */
export function updateActive(u: UpdateState | null): boolean {
  return Boolean(u?.receipt && ACTIVE.includes(u.receipt.state));
}

function checksHTML(rows: UpdateCheck[] | undefined): string {
  if (!rows || !rows.length) return "";
  return `<ul class="muted" style="margin:6px 0 0;padding-left:18px">${rows
    .map(
      (r) =>
        `<li><span class="${r.ok ? "receipt ok" : "receipt err"}">${r.ok ? "✓" : "✗"}</span> ${esc(
          r.title || r.name,
        )}${r.note ? ` — ${esc(r.note)}` : ""}</li>`,
    )
    .join("")}</ul>`;
}

function awaitingHTML(r: UpdateReceipt, now: number): string {
  const rel = r.release || {};
  const backup = r.backup || {};
  const plan = r.plan || {};
  const who = WHO[plan.asked_by || ""] || plan.asked_by || "?";
  const left = expires(r.awaiting_until_utc, now);
  const rows = [
    `<p><b>${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}</b>${rel.notes ? ` · ${esc(rel.notes)}` : ""}</p>`,
    `<p class="muted">Архив ${esc(size(rel.size) || "?")}, sha256 <span class="mono">${esc(
      (rel.sha256 || "").slice(0, 16),
    )}…</span> — исполнитель сверит его после скачивания.</p>`,
    `<p class="muted">Перед подменой в копию уйдёт: ${esc(backup.words || "?")}${
      backup.data_bytes ? ` (${esc(size(backup.data_bytes))})` : ""
    }. Агент будет недоступен несколько минут; не пройдут проверки — вернётся прежняя версия.</p>`,
    `<p class="muted">Просит: ${esc(who)}${plan.reason ? ` — «${esc(plan.reason)}»` : ""}${
      left ? ` · ${esc(left)}` : ""
    }</p>`,
    codePreviewHTML(r),
  ];
  return `<div class="card" style="border-color:var(--accent)">
    <h4>Обновление ждёт твоего подтверждения</h4>${rows.join("")}
    <div class="actions" style="margin-top:10px">
      <button class="btn" data-update-confirm="yes" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Подтвердить</button>
      <button class="btn quiet" data-update-confirm="no" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Отклонить</button>
      <span class="receipt" id="update-note"></span>
    </div></div>`;
}

/** До «да»: правил ли агент свой код и что с этим будет. */
function codePreviewHTML(r: UpdateReceipt): string {
  const code = r.code_preview;
  if (!code) return "";
  const bad = code.mounted === false || code.base === false;
  const files = (code.files || []).length
    ? `: <span class="mono">${esc((code.files || []).join(", "))}${(code.edited || 0) > (code.files || []).length ? " …" : ""}</span>`
    : "";
  return `<p class="${bad ? "receipt err" : "muted"}">Код агента: ${esc(code.note || "")}${code.edited ? files : ""}.</p>`;
}

/** Что стало с правками агента в его коде — строкой, для испытания и итога. */
function agentCodeHTML(r: UpdateReceipt): string {
  const code = r.agent_code;
  if (!code) return "";
  if (code.mounted === false) return code.note ? `<p class="receipt err">Код агента: ${esc(code.note)}</p>` : "";
  if (code.no_base) {
    return `<p class="receipt err">Правки агента не с чем было сравнить (${esc(code.no_base)}) — его прежний код
      целиком лежит у него: <span class="mono">${esc(code.folder || "workspace")}/old-code/</span>.</p>`;
  }
  if (!code.edited || !code.edited.length) return `<p class="muted">Своих правок в коде у агента не было.</p>`;
  const conflicts = code.conflicts || [];
  const counts = `${code.edited.length} файл(ов): перенесено ${(code.carried || []).length}, слито ${
    (code.merged || []).length
  }, не легло ${conflicts.length}`;
  return `<p class="${conflicts.length ? "receipt err" : "muted"}">Правки агента в коде — ${esc(counts)}${
    conflicts.length
      ? `. Не легло: ${esc(conflicts.slice(0, 6).map((c) => c.path).join(", "))}${conflicts.length > 6 ? " …" : ""} — стороны
         и объяснение у агента в <span class="mono">${esc(code.folder || "")}</span>`
      : ""
  }.</p>`;
}

function trialHTML(r: UpdateReceipt, now: number): string {
  const trial = r.trial || {};
  const left = inTime(trial.until_utc, now);
  return `<div class="card" style="border-color:var(--accent)">
    <h4>Испытание: ${esc(r.to_version || "новая версия")} поднята, агент проверяет себя</h4>
    <p class="muted">Механика прошла (${esc((r.checks || []).filter((c) => c.ok).map((c) => c.name).join(", "))}). Теперь агент
      проверяет, думает ли, помнит ли, живы ли его руки и перенесённые правки, — и говорит «принимаю» или «сломано».
      «Сломано» или молчание ${left ? `(срок — ${esc(left)})` : "до срока"} — откат на ${esc(r.from_version || "прежнюю")}:
      код и образ; память агента остаётся.</p>
    ${agentCodeHTML(r)}
    <div class="actions" style="margin-top:10px">
      <button class="btn quiet" data-update-verdict="accept" data-id="${esc(r.id)}" data-key="${esc(trial.key || "")}">Принять</button>
      <button class="btn quiet" data-update-verdict="reject" data-id="${esc(r.id)}" data-key="${esc(trial.key || "")}">Откатить</button>
      <span class="receipt" id="update-note"></span>
    </div>
    <p class="muted">Твоё слово — поверх слова агента: не дожидаясь его, можно принять или откатить сразу.</p></div>`;
}

function runningHTML(r: UpdateReceipt): string {
  const steps = (r.steps || []).slice(-8);
  return `<div class="card">
    <h4>Идёт обновление ${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}</h4>
    <p class="muted">${esc(r.step || STATE_WORDS[r.state] || r.state)}. Когда агента остановят, окно на несколько
      минут потеряет связь — это ожидаемо.</p>
    ${
      steps.length
        ? `<ol class="muted" style="margin:6px 0 0;padding-left:18px">${steps
            .map((s) => `<li${s.ok ? "" : ' class="receipt err"'}>${esc(s.step)}${s.note ? ` — ${esc(s.note)}` : ""}</li>`)
            .join("")}</ol>`
        : ""
    }${checksHTML(r.checks)}</div>`;
}

function finalHTML(r: UpdateReceipt, fmt: (s: string) => string): string {
  const good = r.state === "done";
  const bad = ["failed", "rolled_back", "refused"].includes(r.state);
  const span = r.from_version || r.to_version ? ` ${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}` : "";
  // Проверки — новой версии; при откате отдельной строкой сказано, поднялась ли прежняя.
  const back = r.rollback?.notes?.length
    ? `<p class="muted">Откат: ${esc(r.rollback.notes.join("; "))}; ${
        r.rollback.ok ? "прежняя версия поднята и прошла проверки" : "прежняя версия проверки не прошла"
      }.</p>`
    : "";
  const verdict = r.trial?.verdict;
  const said = verdict?.verdict
    ? `<p class="muted">Испытание: ${
        verdict.verdict === "timeout"
          ? "агент не ответил до срока"
          : `${verdict.by === "agent" ? "агент" : "ты"} — ${verdict.verdict === "accept" ? "«принимаю»" : "«сломано»"}${
              verdict.words ? `: ${esc(verdict.words)}` : ""
            }`
      }.</p>`
    : "";
  return `<p class="${good ? "receipt ok" : bad ? "receipt err" : "receipt"}">Последний план${span}${
    r.finished_utc ? ` (${esc(fmt(r.finished_utc))})` : ""
  }: ${esc(STATE_WORDS[r.state] || r.state)}. ${esc(r.note || "")}</p>${checksHTML(r.checks)}${said}${
    good ? agentCodeHTML(r) : ""
  }${back}`;
}

/**
 * Канал не отвечает: последнее известное — словами, без кнопок. Посреди подмены это
 * ожидаемо (канал живёт в остановленном контейнере), и совет «обнови руками» в это время
 * ломал бы идущую подмену.
 */
function offlineHTML(u: UpdateState | null, fmt: (s: string) => string): string {
  const r = u?.receipt || null;
  const active = updateActive(u);
  const line = `<p class="receipt err">Канал не отвечает${
    active
      ? " — посреди обновления это ожидаемо: агент остановлен на несколько минут. Окно ждёт и дорисует само; руками ничего делать не нужно."
      : " — раздел дорисуется, когда он вернётся."
  }</p>`;
  if (!r) return line;
  if (!active) return line + finalHTML(r, fmt);
  const steps = (r.steps || []).slice(-6);
  return `${line}<p class="muted">Последнее, что сказал исполнитель: ${esc(STATE_WORDS[r.state] || r.state)}${
    r.step ? ` — ${esc(r.step)}` : ""
  }.</p>${
    steps.length
      ? `<ol class="muted" style="margin:6px 0 0;padding-left:18px">${steps
          .map((s) => `<li${s.ok ? "" : ' class="receipt err"'}>${esc(s.step)}${s.note ? ` — ${esc(s.note)}` : ""}</li>`)
          .join("")}</ol>`
      : ""
  }`;
}

/**
 * Раздел «Обновление». `inContainer` — надзор сервера в контейнере: только там без
 * исполнителя имеет смысл говорить, как обновиться руками. На Windows и Mac окно
 * обновляет себя кнопкой в Настройках, и этот раздел пуст. `offline` — канал не ответил:
 * `u` тогда — последний ответ, какой был (или null).
 */
export function updateCardHTML(
  u: UpdateState | null,
  opts: { inContainer: boolean; fmt?: (s: string) => string; now?: number; offline?: boolean },
): string {
  const fmt = opts.fmt || ((s: string) => s);
  const now = opts.now ?? Date.now();
  if (opts.offline) {
    if (!opts.inContainer && !u?.updater?.present) return "";
    return `<h3 class="section-title">Обновление</h3>${offlineHTML(u, fmt)}`;
  }
  const up = u?.updater;
  if (!up || !up.present) {
    if (!opts.inContainer) return "";
    return `<h3 class="section-title">Обновление</h3>
      <p class="muted">Исполнителя обновлений рядом с агентом нет — обновление пока руками на хосте: распакуй
      новую поставку в ту же папку и <code>docker compose -f server/docker-compose.yml up -d --build</code>
      (<code>data/</code> и <code>helene.json</code> переживают пересборку; правки агента в его коде —
      <code>tree/</code>, <code>app/</code> — распаковка поверх затрёт).</p>
      <p class="muted">Чтобы дальше обновлял агент — с твоего «да», копией и откатом, — подними исполнителя
      один раз, из той же папки: <code>${esc(up?.command || "docker compose -f server/updater/docker-compose.yml up -d --build")}</code>.</p>`;
  }
  const latest = up.latest?.version || "";
  const head = up.ok
    ? `<p class="muted">Исполнитель на связи · стоит <b>${esc(up.current || "?")}</b> · в выпусках ${
        latest ? `<b>${esc(latest)}</b>` : "ещё не проверено"
      }${up.latest?.checked_utc ? ` (проверено ${esc(fmt(up.latest.checked_utc))})` : ""}${
        up.latest?.why ? ` · <span class="receipt err">${esc(up.latest.why)}</span>` : ""
      }</p>`
    : `<p class="receipt err">Исполнитель обновлений: ${esc(up.why)}</p>`;
  const r = u?.receipt || null;
  let body = "";
  if (r && r.state === "awaiting") body = awaitingHTML(r, now);
  else if (r && r.state === "trial") body = trialHTML(r, now);
  else if (r && ACTIVE.includes(r.state)) body = runningHTML(r);
  else if (r) body = finalHTML(r, fmt);
  const canPlan = up.ok && up.newer && latest && !(r && ACTIVE.includes(r.state));
  // Исполнитель отказал «мало места» и сам подсказал копию только кода — предлагаем её
  // той же кнопкой, а не прячем выбор в настройки.
  const lowSpace = Boolean(r && r.state === "refused" && /backup: code/.test(r.note || ""));
  const plan = canPlan
    ? `<div class="actions" style="margin-top:10px">
        <button class="btn quiet" data-update-plan="${esc(latest)}" data-backup="${lowSpace ? "code" : "full"}">Подготовить обновление до ${esc(latest)}${
          lowSpace ? " без копии data/" : ""
        }</button>
        <span class="receipt" id="update-plan-note"></span></div>
       <p class="muted">Исполнитель сверит выпуск и покажет его здесь; ничего не начнётся без твоего «Подтвердить».</p>`
    : "";
  return `<h3 class="section-title">Обновление</h3>${head}${body}${plan}`;
}
