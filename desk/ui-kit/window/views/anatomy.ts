import {localizeMode} from "../mode-owned";
import {agentStatePhrase} from '../../status';
import { t as uiText, locale } from "../../../lang/lang";
// Устройство: как этот агент работает, простыми словами, с разбором живого
// хода и живым списком рук — снимок кода, не пересказ.
import { api, post } from "../api";
import { esc, fmtK, fmtN, fmtTime, humanError, md, plural, q, safeRender, toast } from "../lib";
import { stepsHTML, type RunDetail } from "../panel";
import { loadMode, type ModeState } from "../mode";
import { S } from "../state";
import { isMacPlatform } from "../../platform";
import { mountSupervisor } from "./supervisor";
import type { AgentState } from "../state";
import type { FoldState } from "../../memory-fold";
import { inspectionHTML } from "./inspection-status";
import { computerOverview } from "./system-status";
import { machineCaption } from "../../machine-status";

/** Запись леджера ретенции (GET /api/retention). */
interface RetentionEntry {
  class: string;
  path: string;
  bytes: number;
  files: number;
  age_days: number;
  sweepable?: boolean;
}

interface RetentionReport {
  entries?: RetentionEntry[];
  removed?: Array<{ path: string; bytes: number }>;
}

const RETENTION_CLASS_RU: Record<string, string> = {
  get project() { return uiText("ui.ui.kit.window.views.anatomy.dfb992e6d0"); },
  get update_staging() { return uiText("ui.ui.kit.window.views.anatomy.e1e7dee879"); },
  get spool_cache() { return uiText("ui.ui.kit.window.views.anatomy.2e8ccad816"); },
  get models() { return uiText("ui.ui.kit.window.views.anatomy.1cd610c0e5"); },
  get run_artifacts() { return uiText("ui.ui.kit.window.views.anatomy.29c988f689"); },
  get backups() { return uiText("ui.ui.kit.window.views.anatomy.0f3229e75e"); },
};

function fmtMB(bytes: number): string {
  return bytes >= 1048576 ? uiText("ui.ui.kit.window.views.anatomy.fb360df45f", {p0: (bytes / 1048576).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1})}) : uiText("ui.ui.kit.window.views.anatomy.a949d23a84", {p0: fmtN(Math.max(1, Math.round(bytes / 1024)))});
}

/**
 * Материалы — диаграммой на полосках в бумажном стиле (слово владельца 05.10:
 * «не выглядит диаграммой — можно было бы красивые полоски в общем бумажном
 * стиле»). Длина полоски — доля веса среди материалов, тон — чернильная
 * заливка поверх бумаги. Щелчок разворачивает подробности и действия: у
 * удаляемых классов — «Удалить папку» с подтверждением вторым щелчком (слово
 * владельца 01.10 и есть политика удаления, 05.10 — «никак не чистится»);
 * staging убирает общая кнопка по возрасту.
 */
const DELETABLE_CLASSES = new Set(["project", "spool_cache", "run_artifacts"]);

function materialsHTML(r: RetentionReport | null): string {
  if (!r || !r.entries?.length) {
    return uiText("ui.ui.kit.window.views.anatomy.dea9d17425");
  }
  const total = r.entries.reduce((s, e) => s + (e.bytes || 0), 0);
  const heaviest = Math.max(...r.entries.map((e) => e.bytes || 0), 1);
  const strips = r.entries
    .slice()
    .sort((a, b) => (b.bytes || 0) - (a.bytes || 0))
    .map((e) => {
      const name = e.path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || e.path;
      const cls = RETENTION_CLASS_RU[e.class] || e.class;
      const fill = Math.max(2, Math.round(((e.bytes || 0) / heaviest) * 100));
      const deletable = DELETABLE_CLASSES.has(e.class);
      const policy =
        deletable ? uiText("ui.ui.kit.window.views.anatomy.2575df5015")
        : e.class === "models" ? uiText("ui.ui.kit.window.views.anatomy.4966d64b4f")
        : e.class === "backups" ? uiText("ui.ui.kit.window.views.anatomy.8e793d7992")
        : e.sweepable ? uiText("ui.ui.kit.window.views.anatomy.a9cdae1fce") : uiText("ui.ui.kit.window.views.anatomy.b0ef51309c");
      return uiText("ui.ui.kit.window.views.anatomy.3a33c0d71c", {p0: String(e.sweepable ? " sweep" : ""), p1: String(esc(e.path)), p2: String(fill), p3: String(esc(name)), p4: String(esc(cls)), p5: String(fmtMB(e.bytes || 0)), p6: String(fmtN(e.files || 0)), p7: String(fmtN(Math.round(e.age_days || 0))), p8: String(esc(e.path)), p9: String(esc(policy)), p10: String(deletable ? uiText("ui.ui.kit.window.views.anatomy.9f66770bfe", {p0: String(esc(e.path))}) : "")});
    })
    .join("");
  const sweepable = r.entries.filter((e) => e.sweepable).length;
  return uiText("ui.ui.kit.window.views.anatomy.a543c77342", {p0: String(fmtMB(total)), p1: String(fmtN(r.entries.length)), p2: String(strips), p3: String(sweepable ? uiText("ui.ui.kit.window.views.anatomy.00f3bba603", {p0: String(fmtN(sweepable))}) : "")});
}

const INTRO = (): Array<[string, string]> => [
  [
    uiText("ui.ui.kit.window.views.anatomy.694d830810"),
    uiText("ui.ui.kit.window.views.anatomy.bddd6c421b"),
  ],
  [
    uiText("ui.ui.kit.window.views.anatomy.e703266e5b"),
    uiText("ui.ui.kit.window.views.anatomy.8d3b844bcd"),
  ],
  [
    uiText("ui.ui.kit.window.views.anatomy.e69eac00e3"),
    uiText("ui.ui.kit.window.views.anatomy.05f95acc9e"),
  ],
  [
    uiText("ui.ui.kit.window.views.anatomy.3972116405"),
    uiText("ui.ui.kit.window.views.anatomy.fe907ab20f"),
  ],
  [
    uiText("ui.ui.kit.window.views.anatomy.29db437a92"),
    uiText("ui.ui.kit.window.views.anatomy.2d81343226"),
  ],
];

/** Имена слоёв на macOS: оболочка — бандл, тело и мост — без `.exe`. */
function layerNameOnMac(h: string): string {
  return h.replace("helene.exe", "Helene.app").replace(/helene-(body|bridge)\.exe/g, "helene-$1");
}

/** Текст слоя ТЕЛА на macOS.
 *
 * Отдельный текст, а не подмена слов: на Mac у тела другой родитель. Под окном
 * его поднимает код агента, а под СЛУЖБОЙ — окно: у процесса вне графической
 * сессии нет ни рабочего стола, ни разрешений системы, и написать здесь общее
 * «код агента поднимает обоих» значило бы соврать половине владельцев.
 */
const BODY_LAYER_MACOS = () =>
  uiText("ui.ui.kit.window.views.anatomy.7c85d586e2");

/** Слой на macOS: имя без `.exe`, а у тела — и свой текст. */
function layerOnMac(h: string, t: string): [string, string] {
  return [layerNameOnMac(h), h.startsWith("helene-body") ? BODY_LAYER_MACOS() : t];
}

function layerOnLinux(h: string, t: string): [string, string] {
  if (h.startsWith("helene.exe")) return [uiText("ui.ui.kit.window.views.anatomy.85390dd687"), uiText("ui.ui.kit.window.views.anatomy.058390d4d0")];
  return [h.replace(/helene-(body|bridge)\.exe/g, "helene-$1"), h.startsWith("helene-body")
    ? uiText("ui.ui.kit.window.views.anatomy.f58691862f") : t];
}

const LAYERS = (): Array<[string, string]> => [
  [uiText("ui.ui.kit.window.views.anatomy.f527da850c"), uiText("ui.ui.kit.window.views.anatomy.324f14a37f")],
  [uiText("ui.ui.kit.window.views.anatomy.ba8f8523f0"), uiText("ui.ui.kit.window.views.anatomy.49a850aee8")],
  [uiText("ui.ui.kit.window.views.anatomy.203103825a"), uiText("ui.ui.kit.window.views.anatomy.ff13a12d0d")],
  [uiText("ui.ui.kit.window.views.anatomy.e52bfae419"), uiText("ui.ui.kit.window.views.anatomy.287baee410")],
  [uiText("ui.ui.kit.window.views.anatomy.687d9db893"), uiText("ui.ui.kit.window.views.anatomy.33b62db1fc")],
  [uiText("ui.ui.kit.window.views.anatomy.9781548dcd"), uiText("ui.ui.kit.window.views.anatomy.e4fe11f777")],
];

const LESSON: Record<string, string> = {
  get think_first() { return uiText("ui.ui.kit.window.views.anatomy.2487a7644e"); },
  get think() { return uiText("ui.ui.kit.window.views.anatomy.4e5b478e46"); },
  get hand() { return uiText("ui.ui.kit.window.views.anatomy.870dc153d1"); },
  get reply() { return uiText("ui.ui.kit.window.views.anatomy.1f7e820542"); },
  get end_turn() { return uiText("ui.ui.kit.window.views.anatomy.15eaf17ca9"); },
  get terminal() { return uiText("ui.ui.kit.window.views.anatomy.6070ab61cb"); },
};

/** Одна рука глазами ограды: что трогает, накрыта ли и почему. */
interface HandFence {
  name: string;
  touches: string;
  fence: "path" | "container" | "outside" | "unknown";
  why: string;
  display_touches?: unknown;
  display_why?: unknown;
}

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
  // `hands` — поимённо, какая рука в ограде, а какая нет. Присылает харнесс
  // (`fence.hands_report`), считая это из живого набора рук, а не из списка на
  // экране: своя копия здесь означала бы, что окно рассказывает про ограду то,
  // чего в ограде нет. Пусто = старый харнесс рядом с новым окном.
  sandbox?: {
    enabled?: boolean; container?: boolean; reason?: string; windows?: string;
    display_reason?: unknown; display_windows?: unknown;
    hands?: HandFence[];
  };
  // Тело руки `computer` (localharness/body.py): включено ли владельцем, есть
  // ли exe в поставке, подключилось ли на момент снимка, какие права выданы.
  computer?: { enabled?: boolean; available?: boolean; reason?: string; port?: number; scopes?: string[]; connected?: boolean | null };
  tools?: Array<{ name: string; desc?: string; params?: string[]; required?: string[]; invocation?: "direct" | "via_call" }>;
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
  const parts = [uiText("ui.ui.kit.window.views.anatomy.8983f553c8", {p0: String(fmtN(p.calls_day))})];
  if (p.cache_day != null) parts.push(uiText("ui.ui.kit.window.views.anatomy.5915099c3e", {p0: String(p.cache_day)}));
  if (p.cache_now != null) parts.push(uiText("ui.ui.kit.window.views.anatomy.47d453222b", {p0: String(p.cache_now)}));
  const win = p.cache_day_hours ? uiText("ui.ui.kit.window.views.anatomy.570d54c075", {p0: String(fmtN(p.cache_day_hours))}) : uiText("ui.ui.kit.window.views.anatomy.4804254a58");
  return uiText("ui.ui.kit.window.views.anatomy.0889b8ba57", {p0: String(esc(win)), p1: String(parts.join(" · "))});
}

/**
 * Режим агента — рядом с песочницей, потому что это про одно и то же: в каких
 * правах живёт агент. Тексты приходят готовыми из `/api/mode`; свой пересказ
 * здесь означал бы, что в Настройках владелец выбирает одно, а тут читает другое.
 * Снимок анатомии говорит, что ВЫШЛО (ограда встала или нет), режим — что было
 * ВЫБРАНО; расхождение этих двоих и есть то, что стоит увидеть.
 */
function modeHTML(m: ModeState | null): string {
  m=localizeMode(m);
  if (!m || !m.name) return "";
  const svc = m.service_installed === null ? uiText("ui.ui.kit.window.views.anatomy.cee7ccc4d3") : m.service_installed ? uiText("ui.ui.kit.window.views.anatomy.e842608049") : uiText("ui.ui.kit.window.views.anatomy.1149e6f59b");
  // Служба есть на обеих системах, а нулевой сессии и брандмауэра на macOS
  // нет: строк про них там нет тоже. Имя механизма — своё у каждой.
  const mac = isMacPlatform(S.platform);
  const facts = [
    uiText("ui.ui.kit.window.views.anatomy.de1c8788db", {p0: String(m.sandbox ? uiText("ui.ui.kit.window.views.anatomy.a861092dcf") : uiText("ui.ui.kit.window.views.anatomy.36735a542b"))}),
    m.service_here !== false ? `${mac ? uiText("ui.ui.kit.window.views.anatomy.2e6c5d556b") : S.platform === "linux" ? uiText("ui.ui.kit.window.views.anatomy.0fca0cba58") : uiText("ui.ui.kit.window.views.anatomy.aa8af3a475")}: ${svc}` : '',
    // Две галочки службы — разные вопросы, и на экране они стоят порознь.
    // `session0` — права системы агенту; `firewall` — правило брандмауэра для
    // кнопки «Телефон». Про брандмауэр говорим только когда он ВЫКЛЮЧЕН: это
    // выбор владельца против умолчания, и по нему кнопка «Телефон» под службой
    // ведёт себя иначе.
    !mac && m.session0 ? uiText("ui.ui.kit.window.views.anatomy.a1169268ac") : "",
    !mac && m.firewall_set === false ? uiText("ui.ui.kit.window.views.anatomy.5a44154c9d") : "",
    m.legacy_service ? uiText("ui.ui.kit.window.views.anatomy.04491191a3") : "",
    m.source ? uiText("ui.ui.kit.window.views.anatomy.24120e6265", {p0: String(m.source)}) : "",
  ].filter(Boolean);
  const warn = (m.session0 && m.session0_warning ? [m.session0_warning] : []).concat(m.notes);
  return uiText("ui.ui.kit.window.views.anatomy.1fd203fdcc", {p0: String(esc(m.title)), p1: String(esc(m.text)), p2: String(esc(facts.join(" · "))), p3: String(warn.map((n) => `<p class="receipt err">${esc(n)}</p>`).join(""))});
}

/**
 * Ограда поимённо: какая рука накрыта, какая нет и почему.
 *
 * ⚠ Зачем эта карточка вообще есть. До 10.09 экран говорил одну строку —
 * «песочница: shell в контейнере» — и на этом умолкал. А рядом с `shell` стоят
 * руки, которые тоже исполняют команды (`run`, `run_tests`, `pip_install`,
 * `coding_*`), и они шли МИМО ограды: `workshop` держит свой `subprocess`, а
 * `coding_*` исполняет отдельный процесс-надзиратель. Владелец читал молчание
 * как «накрыто всё». Это единственное место, где продукт обещал больше, чем
 * делает, и закрывается оно не молчанием в другую сторону, а списком.
 *
 * Своих слов здесь нет: и приговор, и причина приезжают из харнесса.
 */
function fenceHTML(hands: HandFence[] | undefined, sandboxOn: boolean): string {
  if (!hands || !hands.length) return "";
  const inside = hands.filter((h) => h.fence === "path" || h.fence === "container");
  const outside = hands.filter((h) => h.fence === "outside");
  const stale = hands.filter((h) => h.fence === "unknown");
  const row = (h: HandFence) =>
    `<tr><td><code>${esc(h.name)}</code></td><td class="muted">${esc(machineCaption(h,'touches'))}</td><td class="muted">${esc(machineCaption(h,'why'))}</td></tr>`;
  const table = (rows: HandFence[]) =>
    uiText("ui.ui.kit.window.views.anatomy.ca47b19f9a", {p0: String(rows.map(row).join(""))});
  return uiText("ui.ui.kit.window.views.anatomy.7160c65a6e", {p0: String(inside.length), p1: String(outside.length), p2: String(inside.length ? uiText("ui.ui.kit.window.views.anatomy.a52bcb4808", {p0: String(table(inside))}) : ""), p3: String(outside.length ? uiText("ui.ui.kit.window.views.anatomy.a5ab2e861e", {p0: String(table(outside))}) : ""), p4: String(stale.length ? uiText("ui.ui.kit.window.views.anatomy.d9e14cd775", {p0: String(stale.map((h) => `<code>${esc(h.name)}</code>`).join(", "))}) : ""), p5: String(sandboxOn ? "" : uiText("ui.ui.kit.window.views.anatomy.e1e31ba56f"))});
}

interface CutRow { group: string; calls: number; runs: number; cache_ratio: number | null; output_tokens: number; median_ms: number; p90_ms: number; cuts: number }
interface FrameCuts { days: number; summary: { calls: number; runs: number; cache_ratio: number | null; cached_tokens: number; input_tokens: number; output_tokens: number; cuts: number }; by: Record<string, CutRow[]> }

// Все семь осей deskd/frame_cuts.py: до 0.5.0 экран рисовал три, а role/model/frame_mode/day
// считались и были видны только через `python frame_stats.py --by …` в дереве.
const CUT_AXES = (): Array<[string, string]> => [
  ["iteration", uiText("ui.ui.kit.window.views.anatomy.b41b631f0e")],
  ["hand", uiText("ui.ui.kit.window.views.anatomy.aaba74f766")],
  ["kind", uiText("ui.ui.kit.window.views.anatomy.92cad65982")],
  ["role", uiText("ui.ui.kit.window.views.anatomy.642897b69b")],
  ["model", uiText("ui.ui.kit.window.views.anatomy.b68fadf0c8")],
  ["frame_mode", uiText("ui.ui.kit.window.views.anatomy.cd44ed12bd")],
  ["day", uiText("ui.ui.kit.window.views.anatomy.a4e9e2d1d7")],
];

/** Кэш по группам действий: взвешенная доля (Σ из кэша / Σ входа), не среднее процентов. */
function cutsHTML(c: FrameCuts | null): string {
  if (!c || !c.summary.calls) return "";
  const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);
  const sec = (ms: number) => uiText("ui.ui.kit.window.views.anatomy.baf1ec7874", {p0: (ms / 1000).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1})});
  const table = (rows: CutRow[]) => uiText("ui.ui.kit.window.views.anatomy.07b3e05efe", {p0: String(rows
    .slice(0, 12)
    .map((r) => `<tr><td>${esc(r.group)}</td><td>${r.calls}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td><td>${sec(r.median_ms)}</td><td>${r.cuts || ""}</td></tr>`)
    .join(""))});
  const s = c.summary;
  return uiText("ui.ui.kit.window.views.anatomy.3ac0578d61", {p0: String(c.days), p1: String(s.calls), p2: String(s.runs), p3: String(pct(s.cache_ratio)), p4: String(fmtK(s.cached_tokens)), p5: String(fmtK(s.input_tokens)), p6: String(fmtK(s.output_tokens)), p7: String(s.cuts ? uiText("ui.ui.kit.window.views.anatomy.35e7681979", {p0: String(s.cuts)}) : ""), p8: String(CUT_AXES().map(([axis, title]) => (c.by[axis]?.length ? `<details class="fold" ${axis === "iteration" ? "open" : ""}><summary><b>${esc(title)}</b></summary><div class="fold-body">${table(c.by[axis])}</div></details>` : "")).join(""))});
}

interface SpendRow { calls: number; runs: number; input_tokens: number; cached_tokens: number; fresh_tokens: number; output_tokens: number; total_tokens: number; cache_ratio: number | null; seconds: number; errors: number; kinds: Record<string, number>; models: Record<string, number> }
interface SpendChat extends SpendRow { chat_id: string; title: string }
interface SpendPerson extends SpendRow { who: string; name: string; chats: string[] }
interface SpendRun extends SpendRow { run_id: string; kind: string; chat_id: string; chat_title: string; who: string; who_name: string; goal_head: string; first_ts: number; last_ts: number; status: string }
interface Spend { days: number; summary: SpendRow & { bound_calls: number; unbound_calls: number; fields_in_log: Record<string, boolean> }; by_chat: SpendChat[]; by_person: SpendPerson[]; by_run: SpendRun[]; by_kind: Array<SpendRow & { kind: string }>; unbound: Array<SpendRow & { role: string }> }

const KIND_RU: Record<string, string> = { get chat_turn() { return uiText("ui.ui.kit.window.views.anatomy.577b70fd64"); }, get task_window() { return uiText("ui.ui.kit.window.views.anatomy.871470f451"); }, get heartbeat() { return uiText("ui.ui.kit.window.views.anatomy.601efa51bd"); }, forge_event: "Forge", get wake() { return uiText("ui.ui.kit.window.views.anatomy.0f3c3b6b95"); } };

/** Ревизия расхода по чатам, людям и задачам (слово владельца 08.09): токены, не деньги. */
function spendCutsHTML(s: Spend | null): string {
  if (!s || !s.summary.calls) return "";
  const pct = (r: number | null) => (r == null ? "—" : `${Math.round(r * 100)}%`);
  const cell = (r: SpendRow) => `<td>${r.calls}</td><td>${r.runs || ""}</td><td>${fmtK(r.total_tokens)}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td>`;
  const head = uiText("ui.ui.kit.window.views.anatomy.9249c1083a");
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
    .map((r) => `<tr><td>${esc(r.chat_id ? r.title || r.chat_id : uiText("ui.system.no_chat"))}${idTail(r.title || r.chat_id, r.chat_id)}</td>${cell(r)}</tr>`)
    .join("")}</table>`;
  const people = `<table class="grid">${head}${s.by_person
    .slice(0, 20)
    .map((r) => `<tr><td>${esc(r.who === "praxis:self" ? uiText("ui.system.autonomous") : r.name || r.who)}${idTail(r.name || r.who, r.who)}${r.chats.length ? `<div class="muted">${esc(r.chats.join(" · "))}</div>` : ""}</td>${cell(r)}</tr>`)
    .join("")}</table>`;
  const when = (r: SpendRun) => {
    const d = new Date((r.first_ts || 0) * 1000);
    return isNaN(d.getTime()) ? "" : d.toLocaleString(locale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };
  const runs = uiText("ui.ui.kit.window.views.anatomy.93f2fb2a9e", {p0: String(s.by_run
    .slice(0, 25)
    .map((r) => `<tr><td><b>${esc(KIND_RU[r.kind] || r.kind || uiText("ui.ui.kit.window.views.anatomy.a4beab58fa"))}</b> · ${esc(when(r))}${r.chat_title ? ` · ${esc(r.chat_title)}` : ""}${r.who_name && r.who !== "praxis:self" ? ` · ${esc(r.who_name)}` : ""}${r.goal_head ? `<div class="muted">${esc(r.goal_head)}</div>` : ""}</td><td>${r.calls}</td><td>${fmtK(r.total_tokens)}</td><td>${pct(r.cache_ratio)}</td><td>${fmtK(r.output_tokens)}</td></tr>`)
    .join(""))});
  const unbound = s.unbound.length
    ? uiText("ui.ui.kit.window.views.anatomy.fa077ac32b", {p0: String(s.unbound.map((u) => `${esc(u.role)} — ${u.calls} ${plural(u.calls, uiText("ui.ui.kit.window.views.anatomy.92034d087d"), uiText("ui.ui.kit.window.views.anatomy.0c492cda7b"), uiText("ui.ui.kit.window.views.anatomy.038d0bde3b"))}, ${fmtK(u.total_tokens)}`).join("; "))})
    : "";
  const sum = s.summary;
  const source = sum.fields_in_log.chat ? uiText("ui.ui.kit.window.views.anatomy.fb44c4d959") : uiText("ui.ui.kit.window.views.anatomy.dace6994a6");
  return uiText("ui.ui.kit.window.views.anatomy.085b04aec5", {p0: String(s.days), p1: String(sum.calls), p2: String(fmtK(sum.total_tokens)), p3: String(pct(sum.cache_ratio)), p4: String(fmtK(sum.output_tokens)), p5: String(sum.bound_calls), p6: String(sum.unbound_calls), p7: String(source), p8: String(chats), p9: String(people), p10: String(runs), p11: String(unbound)});
}

export async function render(container: HTMLElement): Promise<void> {
  const [aR, pR, mR, cR, sR, retR, stateR, memoryR] = await Promise.allSettled([api<Anatomy>("/api/anatomy"), api("/api/pulse"), loadMode(), api<FrameCuts>("/api/frame-stats?days=7"), api<Spend>("/api/spend?days=7"), api<RetentionReport>("/api/retention"), api<AgentState>("/api/state"), api<FoldState>("/api/memory-fold/window")]);
  if (aR.status === "rejected") throw aR.reason;
  const a = aR.value;
  const current = stateR.status === "fulfilled" ? stateR.value : null;
  const memory = memoryR.status === "fulfilled" ? memoryR.value : null;
  const mode = mR.status === "fulfilled" ? mR.value : null;
  const computer = computerOverview(mode?.computer || a.computer, mode?.computer_live);
  const overview = uiText("ui.ui.kit.window.views.anatomy.ad3a306e59", {p0: String(esc(agentStatePhrase(current) || uiText("ui.ui.kit.window.views.anatomy.9f07bc1895"))), p1: String(esc(a.agent_name || "")), p2: String(esc(current?.brain?.model || uiText("ui.ui.kit.window.views.anatomy.9f07bc1895"))), p3: String(esc(computer.status)), p4: String(esc(computer.scopes.join(", "))), p5: String(memory?.hot == null ? uiText("ui.ui.kit.window.views.anatomy.9f07bc1895") : uiText("ui.ui.kit.window.views.anatomy.306eb57ee2", {p0: String(fmtN(memory.hot))})), p6: String(memory?.tape_chars ? uiText("ui.ui.kit.window.views.anatomy.ecee140de3", {p0: String(fmtN(memory.tape_chars))}) : uiText("ui.ui.kit.window.views.anatomy.2968cbf1b3")), p7: String(inspectionHTML(current?.update_inspection))});
  const spend = spendHTML(pR.status === "fulfilled" ? pR.value : null);
  const materials = materialsHTML(retR.status === "fulfilled" ? retR.value : null);
  const modeBox = modeHTML(mR.status === "fulfilled" ? mR.value : null);
  const cutsBox = cutsHTML(cR.status === "fulfilled" ? cR.value : null) + spendCutsHTML(sR.status === "fulfilled" ? sR.value : null);
  const modeName = mR.status === "fulfilled" && mR.value.name ? uiText("ui.ui.kit.window.views.anatomy.b197e00962", {p0: String(esc(localizeMode(mR.value)?.title || ""))}) : "";
  const tools = a.tools || [];
  const handFence = new Map((a.sandbox?.hands || []).map((h) => [h.name, h]));
  const fenceBox = fenceHTML(a.sandbox?.hands, !!a.sandbox?.enabled);
  const outsideCount = (a.sandbox?.hands || []).filter((h) => h.fence === "outside").length;
  // Свёртка (владелец 11.10: «раздел сложно воспринимается»): вступление и разрезы
  // по умолчанию закрыты — разворачиваются по клику; развернутыми остаются
  // мето-инфо, надзор и расход. Руки — сотни строк — свёрнуты с поиском внутри.
  const intro = INTRO().map(
    ([h, t]) => `<details class="fold"><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`,
  ).join("");
  // На macOS оболочка — бандл, тело и мост зовутся без `.exe`, а у слоя тела
  // свой текст: под службой его поднимает окно, а не код агента (`layerOnMac`).
  const mac = isMacPlatform(S.platform);
  const layers = LAYERS()
    .map(([h, t]) => (mac ? layerOnMac(h, t) : S.platform === "linux" ? layerOnLinux(h, t) : [h, t]) as [string, string])
    .map(([h, t]) => `<details class="fold"><summary><b>${esc(h)}</b></summary><div class="fold-body">${esc(t)}</div></details>`)
    .join("");
  const body = a.computer
    ? uiText("ui.ui.kit.window.views.anatomy.cf0988a744", {p0: String(esc(!a.computer.enabled ? uiText("ui.ui.kit.window.views.anatomy.2427553fe6") : !a.computer.available ? uiText("ui.ui.kit.window.views.anatomy.7b484487f2") : a.computer.connected === true ? uiText("ui.ui.kit.window.views.anatomy.701e65cfe0", {p0: String(a.computer.port)}) : a.computer.connected === false ? uiText("ui.ui.kit.window.views.anatomy.c8df8e2991") : uiText("ui.ui.kit.window.views.anatomy.d6f0b5d003"))), p1: String(a.computer.enabled && a.computer.scopes ? uiText("ui.ui.kit.window.views.anatomy.d5ab66e480", {p0: String(esc(a.computer.scopes.join(", ") || uiText("ui.ui.kit.window.views.anatomy.dc9337ff5b")))}) : "")})
    : "";
  const meta = tools.length
    ? uiText("ui.ui.kit.window.views.anatomy.2419f72576", {p0: String(esc(a.model?.model || "?")), p1: String(modeName), p2: String(tools.length), p3: String(esc(fmtTime(a.written_at))), p4: String(esc((a.transports || []).join(" + "))), p5: String(esc(a.sandbox ? (a.sandbox.container ? uiText("ui.ui.kit.window.views.anatomy.1004c7a083") : a.sandbox.enabled ? uiText("ui.ui.kit.window.views.anatomy.ad8e4e4415") : uiText("ui.ui.kit.window.views.anatomy.36735a542b")) : "?")), p6: String(machineCaption(a.sandbox,'reason') ? " · " + esc(machineCaption(a.sandbox,'reason')) : ""), p7: String(outsideCount ? uiText("ui.ui.kit.window.views.anatomy.92ca0350d4", {p0: String(outsideCount)}) : ""), p8: String(machineCaption(a.sandbox,'windows') ? " · " + esc(machineCaption(a.sandbox,'windows')) : ""), p9: String(body), p10: String(esc(a.model?.framework || "?"))})
    : uiText("ui.ui.kit.window.views.anatomy.27c9758de4");
  container.innerHTML = uiText("ui.ui.kit.window.views.anatomy.9193355332", {p0: String(overview), p1: String(meta), p2: String(materials), p3: String(modeBox), p4: String(fenceBox), p5: String(spend), p6: String(intro), p7: String(cutsBox), p8: String(layers), p9: String(tools.length ? uiText("ui.ui.kit.window.views.anatomy.42bd082ad4", {p0: String(tools.length)}) : ""), p10: String(a.skills_index ? uiText("ui.ui.kit.window.views.anatomy.883413271d", {p0: String(md(a.skills_index))}) : uiText("ui.ui.kit.window.views.anatomy.f9d6ae8eb8")), p11: String(Object.keys(a.knobs || {}).length ? uiText("ui.ui.kit.window.views.anatomy.91aa1afded", {p0: String(Object.entries(a.knobs!).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td class="mono">${esc(String(v))}</td></tr>`).join(""))}) : "")});
  // Управление и журналы — отдельным читателем: он ходит в свои ручки и живёт
  // на своих обработчиках, а этот экран без него рисуется как рисовался.
  const supervisorBox = q<HTMLElement>("#supervisor-box", container);
  safeRender(supervisorBox, () => mountSupervisor(supervisorBox));
  const sweepBtn = container.querySelector<HTMLButtonElement>("#retention-sweep");
  // Полоски разворачиваются ЯВНЫМ обработчиком (05.10, «не разворачивается»):
  // нативный toggle details у владельца не срабатывал — теперь открытие и
  // закрытие держим сами, и никакой проглоченный клик их не снимет.
  for (const strip of container.querySelectorAll<HTMLDetailsElement>(".material-strip")) {
    strip.querySelector("summary")?.addEventListener("click", (e) => {
      e.preventDefault();
      strip.open = !strip.open;
    });
  }
  // Удаление папки — слово владельца двумя щелчками: первый взводит и честно
  // переспрашивает, второй исполняет. Отказ канала показывается на месте.
  for (const btn of container.querySelectorAll<HTMLButtonElement>("[data-retention-delete]")) {
    btn.addEventListener("click", () => {
      if (btn.dataset.armed === "1") {
        const receipt = btn.parentElement?.querySelector<HTMLElement>(".receipt");
        if (receipt) receipt.textContent = uiText("ui.ui.kit.window.views.anatomy.51999d659e");
        btn.disabled = true;
        void post<{ ok?: boolean; note?: string }>("/api/retention",
            { action: "delete", path: btn.dataset.retentionDelete || "" })
          .then((r) => {
            if (!r?.ok) throw new Error(r?.note || uiText("ui.ui.kit.window.views.anatomy.8735f485c3"));
            toast(uiText("ui.ui.kit.window.views.anatomy.60e80d39ae"));
            void render(container);
          })
          .catch((e) => {
            btn.disabled = false;
            btn.dataset.armed = "";
            btn.textContent = uiText("ui.ui.kit.window.views.anatomy.36572cd39a");
            if (receipt) receipt.textContent = humanError(e).text;
          });
        return;
      }
      btn.dataset.armed = "1";
      btn.textContent = uiText("ui.ui.kit.window.views.anatomy.b303ffe662");
      window.setTimeout(() => {
        if (btn.dataset.armed === "1") {
          btn.dataset.armed = "";
          btn.textContent = uiText("ui.ui.kit.window.views.anatomy.36572cd39a");
        }
      }, 4000);
    });
  }
  if (sweepBtn) {
    const receipt = container.querySelector<HTMLElement>("#retention-receipt");
    sweepBtn.addEventListener("click", () => {
      sweepBtn.disabled = true;
      if (receipt) receipt.textContent = uiText("ui.ui.kit.window.views.anatomy.731c308f04");
      post("/api/retention", { action: "sweep" })
        .then((r) => {
          const removed = ((r as RetentionReport).removed) || [];
          const freed = removed.reduce((s, x) => s + (x.bytes || 0), 0);
          toast(uiText("ui.ui.kit.window.views.anatomy.d12c29d3c9", {p0: String(fmtN(removed.length)), p1: String(fmtMB(freed))}));
          void render(container);
        })
        .catch((e) => {
          if (receipt) receipt.textContent = uiText("ui.ui.kit.window.views.anatomy.356e6bf4c0") + humanError(e).text;
          sweepBtn.disabled = false;
        });
    });
  }
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
            const first = (t.desc || "").split(/(?<=[.!?])\s/)[0] || uiText("ui.ui.kit.window.views.anatomy.3c0ac254c1");
            const params = (t.params || []).map((p) => `<span class="mono ${(t.required || []).includes(p) ? "" : "muted"}">${esc(p)}</span>`).join(", ");
            // Метка стоит у САМОЙ руки, а не только в карточке ограды: владелец
            // читает этот список поиском («что умеет run?») и до карточки может
            // не дойти. Руки, машину не трогающие, метки не получают — иначе
            // шестьдесят «—» утопили бы те семь, ради которых всё это.
            const f = handFence.get(t.name);
            const mark = f
              ? `<span class="${f.fence === "outside" ? "receipt err" : "muted"}" style="margin-left:6px">${esc(f.fence === "outside" ? uiText("ui.ui.kit.window.views.anatomy.05f6d9ca53") : f.fence === "unknown" ? uiText("ui.ui.kit.window.views.anatomy.50e946e21b") : uiText("ui.ui.kit.window.views.anatomy.17240fdfd7"))}</span>`
              : "";
            const invocation = t.invocation === "via_call" ? uiText("ui.ui.kit.window.views.anatomy.3d40cfc7b2") : t.invocation === "direct" ? uiText("ui.ui.kit.window.views.anatomy.f9efbd7707") : uiText("ui.ui.kit.window.views.anatomy.4029aac3f6");
            return `<details class="fold"><summary><code>${esc(t.name)}</code> <span class="muted">${esc(invocation)}</span>${mark} <span class="muted">${esc(first.slice(0, 110))}</span></summary>
            <div class="fold-body" style="white-space:pre-wrap">${esc(t.desc || "")}${f ? uiText("ui.ui.kit.window.views.anatomy.cdd4110599", {p0: String(f.fence === "outside" ? "receipt err" : "muted"), p1: String(esc(f.why))}) : ""}${params ? uiText("ui.ui.kit.window.views.anatomy.73c126bfcd", {p0: String(params)}) : ""}</div></details>`;
          })
          .join("") || uiText("ui.ui.kit.window.views.anatomy.6ce8f3e106");
    };
    filter.addEventListener("input", draw);
    draw();
  }
}

async function renderLesson(box: HTMLElement) {
  box.innerHTML = uiText("ui.ui.kit.window.views.anatomy.b928b13e2d");
  const runs = S.runs.length ? S.runs : await api<typeof S.runs>("/api/runs?limit=30");
  const pick = runs.find((r) => r.kind === "chat_turn" && r.status === "done");
  if (!pick) {
    box.innerHTML = uiText("ui.ui.kit.window.views.anatomy.e3b47bcafc");
    return;
  }
  const d = await api<RunDetail>("/api/run/" + encodeURIComponent(pick.id));
  const m = d.manifest || {};
  // Та же разметка шагов, что в панели хода, плюс пояснение под каждым шагом.
  box.innerHTML =
    `<div class="row"><b>${esc((m.goal || "").split("\n")[0].slice(0, 110))}</b> <span class="muted mono">${esc(pick.id)} · ${fmtTime(m.created_at)}</span></div>` +
    stepsHTML(d, { lesson: LESSON });
}
