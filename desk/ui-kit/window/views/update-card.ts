// Карточка «Обновление» на экране «Система» — окно к серверу (27.09).
//
// Жалоба Дмитрия К: агент на сервере не мог обновить свой контейнер изнутри, и каждый
// шаг шёл кругом через человека. Теперь рядом с контейнером живёт исполнитель
// (`server/updater`), а здесь — одна кнопка.
//
// «Всё максимально просто» (Егор, 27.09): владелец — не обязательно айтишник. Поэтому:
//   * «Обновить до X» — одно нажатие: оно и есть «да» (план уходит с согласием окна,
//     исполнитель сверяет выпуск и начинает сам). Второй кнопки нет;
//   * агент просит обновиться сам — «Обновить» / «Не сейчас» на его просьбу;
//   * итог — одной фразой простыми словами (`summary` расписки); версии, архив, сумма,
//     проверки, правки агента в его коде — под «Подробнее»;
//   * испытание агентом идёт без владельца: «Всё хорошо» / «Вернуть прежнюю» — если хочется
//     сказать своё слово поверх.
//
// Модуль чистый (без импортов): его рисует `supervisor.ts`, а проверяет стенд
// `app/test/update-card.test.mjs` в node без сборки.

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
  summary?: string;
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
  plan?: { reason?: string; asked_by?: string; chat?: string; version?: string; backup?: string; consent?: string };
  finished_utc?: string;
  rollback?: { ok?: boolean; notes?: string[] };
  code_preview?: { mounted?: boolean; base?: boolean; layer?: boolean; edited?: number; files?: string[]; note?: string };
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
  /** Окно новее сервера: у сервера (Hélène до 1.1.1) ручки обновления нет вовсе. */
  old?: boolean;
}

export interface UpdateState {
  updater: UpdaterBeat;
  receipt: UpdateReceipt | null;
  history?: Array<{ id?: string; state?: string; from_version?: string; to_version?: string; finished_utc?: string; note?: string }>;
}

const ACTIVE = ["checking", "awaiting", "confirmed", "running", "trial"];

const STATE_WORDS: Record<string, string> = {
  checking: "проверяю новую версию",
  awaiting: "ждёт согласия",
  confirmed: "начинается",
  running: "идёт обновление",
  trial: "агент проверяет себя в новой версии",
  refused: "не начато",
  declined: "отменено",
  expired: "истекло без ответа",
  superseded: "заменено новой просьбой",
  done: "готово",
  rolled_back: "не получилось — возвращена прежняя версия",
  failed: "не получилось",
};

const WHO: Record<string, string> = {
  agent: "агент",
  "owner-window": "ты, из окна",
  window: "ты, кнопкой в окне",
  "owner-words": "ты, словами агенту",
  host: "команда на сервере",
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

/** Сколько ещё просьба ждёт ответа — словами (истекает через сутки). */
function expires(until: string | undefined, now: number): string {
  const at = Date.parse(until || "");
  if (!Number.isFinite(at)) return "";
  const hours = Math.round((at - now) / 3600_000);
  return hours >= 1 ? `без ответа просьба истечёт через ${hours} ч` : "без ответа просьба вот-вот истечёт";
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

/** Технические подробности — свёрнутыми: владельцу, который не айтишник, они не нужны. */
function details(title: string, inner: string): string {
  return inner.trim()
    ? `<details class="muted" style="margin-top:6px"><summary>${esc(title)}</summary>${inner}</details>`
    : "";
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

function stepsHTML(r: UpdateReceipt, last = 12): string {
  const steps = (r.steps || []).slice(-last);
  return steps.length
    ? `<ol class="muted" style="margin:6px 0 0;padding-left:18px">${steps
        .map((s) => `<li${s.ok ? "" : ' class="receipt err"'}>${esc(s.step)}${s.note ? ` — ${esc(s.note)}` : ""}</li>`)
        .join("")}</ol>`
    : "";
}

/** До «да»: правил ли агент свой код и что с этим будет. */
function codePreviewHTML(r: UpdateReceipt): string {
  const code = r.code_preview;
  if (!code) return "";
  const bad = code.base === false;
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

/** Агент просит обновиться сам — владелец соглашается одной кнопкой. */
function awaitingHTML(r: UpdateReceipt, now: number): string {
  const rel = r.release || {};
  const backup = r.backup || {};
  const plan = r.plan || {};
  const who = WHO[plan.asked_by || ""] || plan.asked_by || "?";
  const left = expires(r.awaiting_until_utc, now);
  const more = [
    `<p><b>${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}</b>${rel.notes ? ` · ${esc(rel.notes)}` : ""}</p>`,
    `<p>Архив ${esc(size(rel.size) || "?")}, sha256 <span class="mono">${esc(
      (rel.sha256 || "").slice(0, 16),
    )}…</span> — исполнитель сверит его после скачивания.</p>`,
    `<p>Перед подменой в копию уйдёт: ${esc(backup.words || "?")}${
      backup.data_bytes ? ` (${esc(size(backup.data_bytes))})` : ""
    }.</p>`,
    `<p>Просит: ${esc(who)}${left ? ` · ${esc(left)}` : ""}</p>`,
    codePreviewHTML(r),
  ].join("");
  return `<div class="card" style="border-color:var(--accent)">
    <h4>${plan.asked_by === "agent" ? "Агент просит обновиться" : "Обновление ждёт согласия"} до ${esc(r.to_version || "?")}</h4>
    ${plan.reason ? `<p>«${esc(plan.reason)}»</p>` : ""}
    <p class="muted">Агент пропадёт на несколько минут. Если что-то пойдёт не так, вернётся прежняя версия;
      память агента не трогается.</p>
    <div class="actions" style="margin-top:10px">
      <button class="btn" data-update-confirm="yes" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Обновить</button>
      <button class="btn quiet" data-update-confirm="no" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Не сейчас</button>
      <span class="receipt" id="update-note"></span>
    </div>${details("Подробнее", more)}</div>`;
}

function trialHTML(r: UpdateReceipt, now: number): string {
  const trial = r.trial || {};
  const left = inTime(trial.until_utc, now);
  const more =
    `<p>Механика прошла (${esc((r.checks || []).filter((c) => c.ok).map((c) => c.name).join(", "))}). Теперь агент
      проверяет, думает ли, помнит ли, живы ли его руки и перенесённые правки, — и говорит «принимаю» или «сломано».
      «Сломано» или молчание до срока — откат на ${esc(r.from_version || "прежнюю")}: код и образ; память агента остаётся.</p>` +
    agentCodeHTML(r);
  return `<div class="card" style="border-color:var(--accent)">
    <h4>Обновлено до ${esc(r.to_version || "новой версии")} — агент проверяет себя</h4>
    <p class="muted">Делать ничего не нужно${left ? ` (срок — ${esc(left)})` : ""}: если агент скажет, что что-то сломано,
      или промолчит, вернётся прежняя версия ${esc(r.from_version || "")}.</p>
    <div class="actions" style="margin-top:10px">
      <button class="btn quiet" data-update-verdict="accept" data-id="${esc(r.id)}" data-key="${esc(trial.key || "")}">Всё хорошо</button>
      <button class="btn quiet" data-update-verdict="reject" data-id="${esc(r.id)}" data-key="${esc(trial.key || "")}">Вернуть прежнюю</button>
      <span class="receipt" id="update-note"></span>
    </div>${details("Подробнее", more)}</div>`;
}

function runningHTML(r: UpdateReceipt): string {
  const head = r.state === "checking" ? `Проверяю новую версию` : `Идёт обновление до ${esc(r.to_version || "?")}`;
  return `<div class="card">
    <h4>${head}</h4>
    <p class="muted">${esc(r.step || STATE_WORDS[r.state] || r.state)}. Окно может на несколько минут потерять
      связь с агентом — это нормально.</p>
    ${details("Что уже сделано", `<p>${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}</p>` + stepsHTML(r) + checksHTML(r.checks))}</div>`;
}

function finalHTML(r: UpdateReceipt, fmt: (s: string) => string): string {
  const good = r.state === "done";
  const bad = ["failed", "rolled_back", "refused"].includes(r.state);
  const span = r.from_version || r.to_version ? ` ${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}` : "";
  const back = r.rollback?.notes?.length
    ? `<p>Откат: ${esc(r.rollback.notes.join("; "))}; ${
        r.rollback.ok ? "прежняя версия поднята и прошла проверки" : "прежняя версия проверки не прошла"
      }.</p>`
    : "";
  const verdict = r.trial?.verdict;
  const said = verdict?.verdict
    ? `<p>Испытание: ${
        verdict.verdict === "timeout"
          ? "агент не ответил до срока"
          : `${verdict.by === "agent" ? "агент" : "ты"} — ${verdict.verdict === "accept" ? "«принимаю»" : "«сломано»"}${
              verdict.words ? `: ${esc(verdict.words)}` : ""
            }`
      }.</p>`
    : "";
  const more =
    `<p>Последний раз${span}${r.finished_utc ? ` (${esc(fmt(r.finished_utc))})` : ""}: ${esc(STATE_WORDS[r.state] || r.state)}.
      ${r.summary && r.note && r.note !== r.summary ? esc(r.note) : ""}</p>` +
    checksHTML(r.checks) +
    said +
    (good ? agentCodeHTML(r) : "") +
    back;
  return `<p class="${good ? "receipt ok" : bad ? "receipt err" : "receipt"}">${esc(r.summary || r.note || STATE_WORDS[r.state] || r.state)}</p>${details(
    "Подробнее",
    more,
  )}`;
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
  return `${line}<p class="muted">Последнее, что сказал исполнитель: ${esc(STATE_WORDS[r.state] || r.state)}${
    r.step ? ` — ${esc(r.step)}` : ""
  }.</p>${stepsHTML(r, 6)}`;
}

/**
 * Раздел «Обновление». `inContainer` — надзор сервера в контейнере: только там без
 * исполнителя имеет смысл говорить, как его поднять. На Windows и Mac окно обновляет себя
 * кнопкой в Настройках, и этот раздел пуст. `offline` — канал не ответил: `u` тогда —
 * последний ответ, какой был (или null).
 */
export function updateCardHTML(
  u: UpdateState | null,
  opts: { inContainer: boolean; fmt?: (s: string) => string; now?: number; offline?: boolean },
): string {
  const fmt = opts.fmt || ((s: string) => s);
  const now = opts.now ?? Date.now();
  const section = (body: string) => `<h3 class="section-title">Обновление</h3>${body}`;
  if (opts.offline) {
    if (!opts.inContainer && !u?.updater?.present) return "";
    return section(offlineHTML(u, fmt));
  }
  const up = u?.updater;
  if (!up || !up.present) {
    if (!opts.inContainer) return "";
    if (up?.old) {
      // Пульт на ПК обновился раньше сервера. Обновить сервер отсюда нечем — у него нет
      // исполнителя; команда из НОВОЙ поставки (в старой установке её ещё нет).
      return section(`<p class="muted">На сервере стоит Hélène, которая ещё не умеет обновляться отсюда. Один раз
        нужна одна команда на сервере — её выполняет тот, кто ставил Hélène (или его помощник): скачать
        <code>Helene-&lt;версия&gt;.zip</code>, распаковать и <code>sh Helene/server/install.sh</code>. Правки агента
        в его коде она перенесёт сама; дальше обновления — здесь, одной кнопкой.</p>`);
    }
    return section(`<p class="muted">Чтобы обновлять Hélène отсюда одной кнопкой, на сервере один раз нужна одна
      команда — её выполняет тот, кто ставил Hélène (или его помощник), в папке установки:
      <code>${esc(up?.command || "sh server/install.sh")}</code></p>`);
  }
  const r = u?.receipt || null;
  const latest = up.latest?.version || "";
  let body = up.ok ? "" : `<p class="receipt err">Исполнитель обновлений: ${esc(up.why)}</p>`;
  if (r && r.state === "awaiting") body += awaitingHTML(r, now);
  else if (r && r.state === "trial") body += trialHTML(r, now);
  else if (r && ACTIVE.includes(r.state)) body += runningHTML(r);
  else {
    // Мало места — исполнитель сам предложил копию только кода: та же кнопка, без копии памяти.
    const lowSpace = Boolean(r && r.state === "refused" && /backup: code/.test(r.note || ""));
    if (up.ok && up.newer && latest) {
      body += `<p><b>Есть новая версия ${esc(latest)}</b>${up.current ? ` (стоит ${esc(up.current)})` : ""}.</p>
        <div class="actions" style="margin-top:6px">
          <button class="btn" data-update-plan="${esc(latest)}" data-backup="${lowSpace ? "code" : "full"}">Обновить до ${esc(latest)}${
            lowSpace ? " без копии памяти" : ""
          }</button>
          <span class="receipt" id="update-plan-note"></span></div>
        <p class="muted">Агент пропадёт на несколько минут. Если что-то пойдёт не так, вернётся прежняя версия;
          память агента не трогается.</p>`;
    } else if (up.ok) {
      body += `<p class="muted">Стоит ${esc(up.current || "?")} — это последняя версия${
        up.latest?.checked_utc ? ` (проверено ${esc(fmt(up.latest.checked_utc))})` : ""
      }.${up.latest?.why ? ` <span class="receipt err">Выпуски не проверились: ${esc(up.latest.why)}</span>` : ""}</p>`;
    }
    if (r) body += finalHTML(r, fmt);
  }
  return section(body);
}
