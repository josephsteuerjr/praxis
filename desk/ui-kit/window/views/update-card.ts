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

const ACTIVE = ["checking", "awaiting", "confirmed", "running"];

const STATE_WORDS: Record<string, string> = {
  checking: "исполнитель сверяет план",
  awaiting: "ждёт твоего «да»",
  confirmed: "«да» получено — начинается",
  running: "идёт обновление",
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
  ];
  return `<div class="card" style="border-color:var(--accent)">
    <h4>Обновление ждёт твоего подтверждения</h4>${rows.join("")}
    <div class="actions" style="margin-top:10px">
      <button class="btn" data-update-confirm="yes" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Подтвердить</button>
      <button class="btn quiet" data-update-confirm="no" data-id="${esc(r.id)}" data-nonce="${esc(r.nonce || "")}">Отклонить</button>
      <span class="receipt" id="update-note"></span>
    </div></div>`;
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
  return `<p class="${good ? "receipt ok" : bad ? "receipt err" : "receipt"}">Последний план${span}${
    r.finished_utc ? ` (${esc(fmt(r.finished_utc))})` : ""
  }: ${esc(STATE_WORDS[r.state] || r.state)}. ${esc(r.note || "")}</p>${checksHTML(r.checks)}${back}`;
}

/**
 * Раздел «Обновление». `inContainer` — надзор сервера в контейнере: только там без
 * исполнителя имеет смысл говорить, как обновиться руками. На Windows и Mac окно
 * обновляет себя кнопкой в Настройках, и этот раздел пуст.
 */
export function updateCardHTML(
  u: UpdateState | null,
  opts: { inContainer: boolean; fmt?: (s: string) => string; now?: number },
): string {
  const fmt = opts.fmt || ((s: string) => s);
  const now = opts.now ?? Date.now();
  const up = u?.updater;
  if (!up || !up.present) {
    if (!opts.inContainer) return "";
    return `<h3 class="section-title">Обновление</h3>
      <p class="muted">Исполнителя обновлений рядом с агентом нет — обновление пока руками на хосте: распакуй
      новую поставку в ту же папку и <code>docker compose -f server/docker-compose.yml up -d --build</code>
      (<code>data/</code> и <code>helene.json</code> переживают пересборку).</p>
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
