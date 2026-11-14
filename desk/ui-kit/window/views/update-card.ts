import { t as uiText, locale } from "../../../lang/lang";
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
// Pure rendering with the shared language catalog; used by `supervisor.ts` and
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
  /** 1.2.5: расписку пишет установщик ПК (испытание после обновления), не исполнитель сервера. */
  desktop?: boolean;
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
  /** 1.2.5: бьётся сторож испытания ПК, а не исполнитель сервера. */
  desktop?: boolean;
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
  get checking() { return uiText("ui.ui.kit.window.views.update.card.807a238756"); },
  get awaiting() { return uiText("ui.ui.kit.window.views.update.card.f96e1ddddc"); },
  get confirmed() { return uiText("ui.ui.kit.window.views.update.card.bac6321d73"); },
  get running() { return uiText("ui.ui.kit.window.views.update.card.36ae10a82c"); },
  get trial() { return uiText("ui.ui.kit.window.views.update.card.cf64ad3731"); },
  get refused() { return uiText("ui.ui.kit.window.views.update.card.9c67e9156f"); },
  get declined() { return uiText("ui.ui.kit.window.views.update.card.8a09723017"); },
  get expired() { return uiText("ui.ui.kit.window.views.update.card.0332e42295"); },
  get superseded() { return uiText("ui.ui.kit.window.views.update.card.ce3547c44b"); },
  get done() { return uiText("ui.ui.kit.window.views.update.card.57778825e1"); },
  get rolled_back() { return uiText("ui.ui.kit.window.views.update.card.b0a4c941c4"); },
  get failed() { return uiText("ui.ui.kit.window.views.update.card.8ae757e4b8"); },
};

const WHO: Record<string, string> = {
  get agent() { return uiText("ui.ui.kit.window.views.update.card.8f3bd8553c"); },
  get "owner-window"() { return uiText("ui.ui.kit.window.views.update.card.1227843ec9"); },
  get window() { return uiText("ui.ui.kit.window.views.update.card.01290e5934"); },
  get "owner-words"() { return uiText("ui.ui.kit.window.views.update.card.856f9d0c56"); },
  get host() { return uiText("ui.ui.kit.window.views.update.card.b86258cca5"); },
};

function esc(text: unknown): string {
  return String(text ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] as string,
  );
}

/** Сколько ещё до срока — словами («через 24 ч», «через 18 мин»), не часами без даты. */
/** Сколько ещё просьба ждёт ответа — словами (истекает через сутки). */
function expires(until: string | undefined, now: number): string {
  const at = Date.parse(until || "");
  if (!Number.isFinite(at)) return "";
  const hours = Math.round((at - now) / 3600_000);
  return hours >= 1 ? uiText("ui.ui.kit.window.views.update.card.eed4b12989", {p0: String(hours)}) : uiText("ui.ui.kit.window.views.update.card.6f5b7c4597");
}

function size(bytes: number | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024 ** 3) return Math.round(bytes / 1024 ** 2) + uiText("ui.ui.kit.window.views.update.card.883817f199");
  return (bytes / 1024 ** 3).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1}) + uiText("ui.ui.kit.window.views.update.card.9fd219660f");
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
  return uiText("ui.ui.kit.window.views.update.card.cc87cfe687", {p0: String(bad ? "receipt err" : "muted"), p1: String(esc(code.note || "")), p2: String(code.edited ? files : "")});
}

/** Что стало с правками агента в его коде — строкой, для испытания и итога. */
function agentCodeHTML(r: UpdateReceipt): string {
  const code = r.agent_code;
  if (!code) return "";
  if (code.mounted === false) return code.note ? uiText("ui.ui.kit.window.views.update.card.cca129520f", {p0: String(esc(code.note))}) : "";
  if (code.no_base) {
    return uiText("ui.ui.kit.window.views.update.card.a8802675e4", {p0: String(esc(code.no_base)), p1: String(esc(code.folder || "workspace"))});
  }
  if (!code.edited || !code.edited.length) return uiText("ui.ui.kit.window.views.update.card.823ce290f7");
  const conflicts = code.conflicts || [];
  const counts = uiText("ui.ui.kit.window.views.update.card.5459ae9538", {p0: String(code.edited.length), p1: String((code.carried || []).length), p2: String((code.merged || []).length), p3: String(conflicts.length)});
  return uiText("ui.ui.kit.window.views.update.card.661d5c26be", {p0: String(conflicts.length ? "receipt err" : "muted"), p1: String(esc(counts)), p2: String(conflicts.length
      ? uiText("ui.ui.kit.window.views.update.card.6da5e83b59", {p0: String(esc(conflicts.slice(0, 6).map((c) => c.path).join(", "))), p1: String(conflicts.length > 6 ? " …" : ""), p2: String(esc(code.folder || ""))})
      : "")});
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
    uiText("ui.ui.kit.window.views.update.card.03f2949fef", {p0: String(esc(size(rel.size) || "?")), p1: String(esc(
      (rel.sha256 || "").slice(0, 16),
    ))}),
    uiText("ui.ui.kit.window.views.update.card.c6d5191662", {p0: String(esc(backup.words || "?")), p1: String(backup.data_bytes ? ` (${esc(size(backup.data_bytes))})` : "")}),
    uiText("ui.ui.kit.window.views.update.card.84f3e223aa", {p0: String(esc(who)), p1: String(left ? ` · ${esc(left)}` : "")}),
    codePreviewHTML(r),
  ].join("");
  return uiText("ui.ui.kit.window.views.update.card.6260e57b29", {p0: String(plan.asked_by === "agent" ? uiText("ui.ui.kit.window.views.update.card.2a9898574e") : uiText("ui.ui.kit.window.views.update.card.20f927c4d9")), p1: String(esc(r.to_version || "?")), p2: String(plan.reason ? `<p>«${esc(plan.reason)}»</p>` : ""), p3: String(esc(r.id)), p4: String(esc(r.nonce || "")), p5: String(esc(r.id)), p6: String(esc(r.nonce || "")), p7: String(details(uiText("ui.ui.kit.window.views.update.card.59589fe2a5"), more))});
}

function trialHTML(r: UpdateReceipt, _now: number): string {
  return uiText("ui.ui.kit.window.views.update.card.cb8d0b3de3", {p0: String(agentCodeHTML(r))});
}

function runningHTML(r: UpdateReceipt): string {
  const head = r.state === "checking" ? uiText("ui.ui.kit.window.views.update.card.376266929f") : uiText("ui.ui.kit.window.views.update.card.3b668cd6d3", {p0: String(esc(r.to_version || "?"))});
  return uiText("ui.ui.kit.window.views.update.card.919df5bcd4", {p0: String(head), p1: String(esc(r.step || STATE_WORDS[r.state] || r.state)), p2: String(details(uiText("ui.ui.kit.window.views.update.card.5573628e3b"), `<p>${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}</p>` + stepsHTML(r) + checksHTML(r.checks)))});
}

function finalHTML(r: UpdateReceipt, fmt: (s: string) => string): string {
  const good = r.state === "done";
  const bad = ["failed", "rolled_back", "refused"].includes(r.state);
  const span = r.from_version || r.to_version ? ` ${esc(r.from_version || "?")} → ${esc(r.to_version || "?")}` : "";
  const back = r.rollback?.notes?.length
    ? uiText("ui.ui.kit.window.views.update.card.02d7867a65", {p0: String(esc(r.rollback.notes.join("; "))), p1: String(r.rollback.ok ? uiText("ui.ui.kit.window.views.update.card.db52eac1d4") : uiText("ui.ui.kit.window.views.update.card.6c1dfc2d2c"))})
    : "";
  const verdict = r.trial?.verdict;
  const said = verdict?.verdict
    ? uiText("ui.ui.kit.window.views.update.card.ac707b9776", {p0: String(verdict.verdict === "timeout"
          ? uiText("ui.ui.kit.window.views.update.card.99d5c31ccb")
          : `${verdict.by === "agent" ? uiText("ui.ui.kit.window.views.update.card.8f3bd8553c") : uiText("ui.ui.kit.window.views.update.card.bca5722b57")} — ${verdict.verdict === "accept" ? uiText("ui.ui.kit.window.views.update.card.f8a968c958") : uiText("ui.ui.kit.window.views.update.card.588f22b30a")}${
              verdict.words ? `: ${esc(verdict.words)}` : ""
            }`)})
    : "";
  const more =
    uiText("ui.ui.kit.window.views.update.card.b863f3a5a5", {p0: String(span), p1: String(r.finished_utc ? ` (${esc(fmt(r.finished_utc))})` : ""), p2: String(esc(STATE_WORDS[r.state] || r.state)), p3: String(r.summary && r.note && r.note !== r.summary ? esc(r.note) : "")}) +
    checksHTML(r.checks) +
    said +
    (good ? agentCodeHTML(r) : "") +
    back;
  return `<p class="${good ? "receipt ok" : bad ? "receipt err" : "receipt"}">${esc(r.summary || r.note || STATE_WORDS[r.state] || r.state)}</p>${details(
    uiText("ui.ui.kit.window.views.update.card.59589fe2a5"),
    more,
  )}`;
}

/**
 * ПК (1.2.5): испытание после обновления ведёт установщик — тот же протокол, своя расписка
 * (`desktop: true`). Рисуется в Настройках → «О программе»: пока идёт — «Всё хорошо» /
 * «Вернуть прежнюю» поверх слова агента; после — одна фраза итога (три дня). Расписка
 * сервера или давняя — пусто.
 */
export function deskTrialHTML(u: UpdateState | null, opts: { fmt?: (s: string) => string; now?: number } = {}): string {
  const r = u?.receipt || null;
  if (!r || !r.desktop) return "";
  const now = opts.now ?? Date.now();
  if (r.state === "trial") return trialHTML(r, now);
  if (ACTIVE.includes(r.state)) return runningHTML(r);
  const at = Date.parse(r.finished_utc || "");
  if (!Number.isFinite(at) || now - at > 3 * 86_400_000) return "";
  return finalHTML(r, opts.fmt || ((s: string) => s));
}

/**
 * Канал не отвечает: последнее известное — словами, без кнопок. Посреди подмены это
 * ожидаемо (канал живёт в остановленном контейнере), и совет «обнови руками» в это время
 * ломал бы идущую подмену.
 */
function offlineHTML(u: UpdateState | null, fmt: (s: string) => string): string {
  const r = u?.receipt || null;
  const active = updateActive(u);
  const line = uiText("ui.ui.kit.window.views.update.card.a487c00a67", {p0: String(active
      ? uiText("ui.ui.kit.window.views.update.card.838e65a170")
      : uiText("ui.ui.kit.window.views.update.card.a95bdb104f"))});
  if (!r) return line;
  if (!active) return line + finalHTML(r, fmt);
  return uiText("ui.ui.kit.window.views.update.card.5f9b3f5290", {p0: String(line), p1: String(esc(STATE_WORDS[r.state] || r.state)), p2: String(r.step ? ` — ${esc(r.step)}` : ""), p3: String(stepsHTML(r, 6))});
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
  const section = (body: string) => uiText("ui.ui.kit.window.views.update.card.c62498689b", {p0: String(body)});
  // ПК: обновление и его испытание — в Настройках (`deskTrialHTML`); серверный раздел с
  // «исполнителем» здесь говорил бы неправду.
  if (u?.updater?.desktop || u?.receipt?.desktop) return "";
  if (opts.offline) {
    if (!opts.inContainer && !u?.updater?.present) return "";
    return section(offlineHTML(u, fmt));
  }
  const up = u?.updater;
  if (!up || !up.present) {
    if (!opts.inContainer) return "";
    // 11.11 (Yegor): no manual script. The desktop application installs the updater over SSH.
    return section(uiText("ui.ui.kit.window.views.update.card.e712fccae0"));
  }
  const r = u?.receipt || null;
  const latest = up.latest?.version || "";
  let body = up.ok ? "" : uiText("ui.ui.kit.window.views.update.card.b8de1feaa9", {p0: String(esc(up.why))});
  if (r && r.state === "awaiting") body += awaitingHTML(r, now);
  else if (r && r.state === "trial") body += trialHTML(r, now);
  else if (r && ACTIVE.includes(r.state)) body += runningHTML(r);
  else {
    // Мало места — исполнитель сам предложил копию только кода: та же кнопка, без копии памяти.
    const lowSpace = Boolean(r && r.state === "refused" && /backup: code/.test(r.note || ""));
    if (up.ok && up.newer && latest) {
      body += uiText("ui.ui.kit.window.views.update.card.5cf377303a", {p0: String(esc(latest)), p1: String(up.current ? uiText("ui.ui.kit.window.views.update.card.c4e558f239", {p0: String(esc(up.current))}) : ""), p2: String(esc(latest)), p3: String(lowSpace ? "code" : "full"), p4: String(esc(latest)), p5: String(lowSpace ? uiText("ui.ui.kit.window.views.update.card.90cfb3a2b2") : "")});
    } else if (up.ok) {
      body += uiText("ui.ui.kit.window.views.update.card.a14b18487b", {p0: String(esc(up.current || "?")), p1: String(up.latest?.checked_utc ? uiText("ui.ui.kit.window.views.update.card.517036a5c9", {p0: String(esc(fmt(up.latest.checked_utc)))}) : ""), p2: String(up.latest?.why ? uiText("ui.ui.kit.window.views.update.card.5e07f2df73", {p0: String(esc(up.latest.why))}) : "")});
    }
    if (r) body += finalHTML(r, fmt);
  }
  return section(body);
}
