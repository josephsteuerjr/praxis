import { t as uiText, locale } from "../../../lang/lang";
// Раздел «Что поручить» — знакомство с агентом: кто он, как идёт один ход, что ему
// поручить и как он живёт между ходами.
//
// ЗАЧЕМ ОН ЕСТЬ. Свежая установка открывается на пустом «Сейчас»: агент живой,
// рук у него больше девяноста, и ни одна нигде не названа словами, которыми
// говорят люди. Человек видит поле ввода и не знает, что в него писать. Это
// дыра настоящая, а не украшение: у неё есть цена — программа, которой не
// пользуются.
//
// 1.0.1 (слово Егора: «вкладка-онбординг должна быть дизайнерским чудом»). Раздел
// перестал быть справкой и стал знакомством: живая схема хода, где каждый блок
// ведёт в раздел окна, в котором это видно вживую («фигма для агента» с лёгким
// обучающим слоем — его же слова 31.08); рамки задач заполняются прямо в карточке;
// сутки агента нарисованы по его НАСТОЯЩИМ ходам за сегодня, а не картинкой.
//
// ⚠ ЗАДАЧКИ — ТОЛЬКО ШАБЛОНЫ С ДЫРАМИ. Готовый текст вроде «Вика — моя сестра,
// живёт в Казани, двое детей» — это выдуманный факт за подписью владельца, а
// руки `remember` и `connections` запишут его в память агента НАВСЕГДА. Здесь
// стоят только рамки с пропусками, которые человек заполняет сам; незаполненный
// пропуск уезжает в поле ввода рамкой `<…>`, а не догадкой. Условие приёмки.
//
// ⚠ Ни строки про кэш, ни одной выдуманной цифры: «K, E и A отдаются из кэша,
// платишь только за T» — по замерам 16.09 неправда. Всё живое на странице (модель,
// связь, ходы за сегодня, следующее пробуждение) читается из канала; нет данных —
// так и написано, а не нарисовано «для примера».
//
// Кегли ≥ 12px и ничего бледнее `--ink-2` в тексте: `--muted` на белом даёт 2,75:1
// при норме 4,5. Движение — только CSS и один requestAnimationFrame по кнопке
// «Проиграть ход»; при `prefers-reduced-motion` ход проигрывается шагами, без полёта.
import { voiceHelp } from "../voice-preferences";
import { cfg } from "../api";
import { api } from "../api";
import { esc } from "../lib";
import { runTitle } from "../../text";
import { LOCAL_AGENT, S, foreignHarness, type Run, type View } from "../state";
import { isMacPlatform } from "../../platform";
import { copyText } from "../../dom";
import { mountConnectionGuide } from "../connection-guide";

// ------------------------------------------------------------------ задачки

type Cat = "people" | "time" | "know" | "pc" | "words";

/** Задачка: рамка с пропусками, которые заполняет владелец. */
interface Task {
  id: string;
  cat: Cat;
  /** Заголовок карточки — человеческим языком, без имён рук. */
  title: string;
  /** Одна строка: что агент сделает и чем это кончится. */
  what: string;
  /** Шаблон с дырами в угловых скобках. */
  template: string;
  /** Только у издания с агентом на этой машине (у издания к серверу такого нет). */
  local?: boolean;
  /** Нужно тело тула `computer`. Оно есть на Windows и macOS (0.8.0) и включается
   *  владельцем — карточка про это не решает, только помечает. */
  computer?: boolean;
}

/** Линейные значки 20×20 — тем же пером, что значки полки. */
const CAT_ICON: Record<Cat, string> = {
  people: '<circle cx="10" cy="6.8" r="2.9"/><path d="M4.4 16.2c.8-3 3-4.6 5.6-4.6s4.8 1.6 5.6 4.6"/>',
  time: '<circle cx="10" cy="10.4" r="6.4"/><path d="M10 6.9v3.5l2.4 1.5"/>',
  know: '<circle cx="8.8" cy="8.8" r="4.9"/><path d="m12.4 12.4 4 4"/>',
  pc: '<rect x="3" y="4" width="14" height="9.4" rx="1.6"/><path d="M7.4 16.4h5.2M10 13.4v3"/>',
  words: '<path d="M4.2 15.8 5.1 12l8.3-8.3a1.5 1.5 0 0 1 2.1 0l.8.8a1.5 1.5 0 0 1 0 2.1L8 14.9Z"/><path d="m12.3 4.8 2.9 2.9"/>',
};

const CATS: Array<{ id: Cat; label: string }> = [
  { id: "people", get label() { return uiText("ui.ui.kit.window.views.learn.be68fdc328"); } },
  { id: "time", get label() { return uiText("ui.ui.kit.window.views.learn.6711f073ab"); } },
  { id: "know", get label() { return uiText("ui.ui.kit.window.views.learn.0a2beacf97"); } },
  { id: "pc", get label() { return uiText("ui.ui.kit.window.views.learn.24355708a2"); } },
  { id: "words", get label() { return uiText("ui.ui.kit.window.views.learn.b024ccfdb8"); } },
];

const TASKS: Task[] = [
  {
    id: "meet",
    cat: "people",
    get title() { return uiText("ui.ui.kit.window.views.learn.8749b052aa"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.a4dcbf674d"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.1d8e288977"); },
  },
  {
    id: "remind",
    cat: "time",
    get title() { return uiText("ui.ui.kit.window.views.learn.9eb0c10ab4"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.827bb20a9f"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.d92ba37582"); },
  },
  {
    id: "duty",
    cat: "time",
    get title() { return uiText("ui.ui.kit.window.views.learn.7466722102"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.8b5b661016"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.bc9b45a8e1"); },
  },
  {
    id: "search",
    cat: "know",
    get title() { return uiText("ui.ui.kit.window.views.learn.fb36050959"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.ea11bd6e91"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.00e1c89a70"); },
  },
  {
    id: "follow",
    cat: "time",
    get title() { return uiText("ui.ui.kit.window.views.learn.be312d29ba"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.3ddc9912e2"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.7bdcadfe07"); },
  },
  {
    id: "read",
    cat: "know",
    get title() { return uiText("ui.ui.kit.window.views.learn.b29fadaf15"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.9d8a876c11"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.3581f4313e"); },
    local: true,
  },
  {
    id: "folder",
    cat: "pc",
    get title() { return uiText("ui.ui.kit.window.views.learn.575d5a4fd6"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.9f0e73a0c6"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.ba6347d986"); },
    local: true,
  },
  {
    id: "write",
    cat: "words",
    get title() { return uiText("ui.ui.kit.window.views.learn.707a3668be"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.7f1cd97327"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.fce10d03ec"); },
  },
  {
    id: "diary",
    cat: "people",
    get title() { return uiText("ui.ui.kit.window.views.learn.06221c3309"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.9eb686b385"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.dfab547b63"); },
  },
  {
    id: "explain",
    cat: "know",
    get title() { return uiText("ui.ui.kit.window.views.learn.672430403b"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.e899dfb568"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.20d59f72c4"); },
  },
  {
    id: "computer",
    cat: "pc",
    get title() { return uiText("ui.ui.kit.window.views.learn.f4869f7c93"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.d497343e1e"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.a47babe3b0"); },
    local: true,
    computer: true,
  },
  {
    id: "obstacles",
    cat: "words",
    get title() { return uiText("ui.ui.kit.window.views.learn.87f3da2202"); },
    get what() { return uiText("ui.ui.kit.window.views.learn.8ee03fa140"); },
    get template() { return uiText("ui.ui.kit.window.views.learn.a115605167"); },
  },
];

const template = (id: string): string => TASKS.find((t) => t.id === id)?.template ?? "";

/**
 * Четыре коротких начала для пустой переписки.
 *
 * Берутся из тех же рамок: человек, нажавший «Познакомить с человеком» в пустом
 * чате и в разделе, обязан получить один и тот же текст — иначе это два разных
 * обещания об одном.
 */
export const STARTERS: Array<{ label: string; template: string }> = [
  { get label() { return uiText("ui.ui.kit.window.views.learn.8b3cdf2b3d"); }, get template() { return template("meet"); } },
  { get label() { return uiText("ui.ui.kit.window.views.learn.d1f1f38d8d"); }, get template() { return template("remind"); } },
  { get label() { return uiText("ui.ui.kit.window.views.learn.fb36050959"); }, get template() { return template("search"); } },
  { get label() { return uiText("ui.ui.kit.window.views.learn.0ec83b845c"); }, get template() { return template("obstacles"); } },
];

/** Рамка «представься» первого шага — те же правила: только пропуски. */
const INTRO = () => uiText("ui.ui.kit.window.views.learn.646d179c95");

// ------------------------------------------------- рамка с пропусками в строке

type Piece = { text: string } | { hole: string };

function pieces(tpl: string): Piece[] {
  const out: Piece[] = [];
  let last = 0;
  for (const m of tpl.matchAll(/<([^<>]+)>/g)) {
    const at = m.index ?? 0;
    if (at > last) out.push({ text: tpl.slice(last, at) });
    out.push({ hole: m[1] });
    last = at + m[0].length;
  }
  if (last < tpl.length) out.push({ text: tpl.slice(last) });
  return out;
}

/** Предложение с полями на месте пропусков: пишешь прямо в карточке. */
function sentence(tpl: string, key: string): string {
  return pieces(tpl)
    .map((p, i) =>
      "hole" in p
        ? `<input class="blank" type="text" data-key="${esc(key)}" data-hole="${i}"
             placeholder="${esc(p.hole)}" aria-label="${esc(p.hole)}"
             size="${Math.max(4, p.hole.length)}" autocomplete="off" spellcheck="true">`
        : `<span>${esc(p.text)}</span>`,
    )
    .join("");
}

/** Текст для поля ввода: заполненное — как есть, пустой пропуск — рамкой `<…>`. */
function assemble(tpl: string, root: ParentNode, key: string): string {
  return pieces(tpl)
    .map((p, i) => {
      if (!("hole" in p)) return p.text;
      const input = root.querySelector<HTMLInputElement>(`input.blank[data-key="${key}"][data-hole="${i}"]`);
      const value = (input?.value ?? "").replace(/\s+/g, " ").trim();
      return value || `<${p.hole}>`;
    })
    .join("");
}

// ------------------------------------------------------------ схема хода

interface Stage {
  id: string;
  title: string;
  sub: string;
  body: string;
  go?: { view: View; label: string };
}

function stages(): Stage[] {
  const model = (S.agentState?.brain?.model || "").trim();
  return [
    {
      id: "word",
      title: uiText("ui.ui.kit.window.views.learn.e4e6c28250"),
      sub: uiText("ui.ui.kit.window.views.learn.52f1de4b10"),
      body:
        uiText("ui.ui.kit.window.views.learn.b3e6250100"),
      go: { view: "talk", label: uiText("ui.ui.kit.window.views.learn.917bb52293") },
    },
    {
      id: "memory",
      title: uiText("ui.ui.kit.window.views.learn.3972116405"),
      sub: uiText("ui.ui.kit.window.views.learn.7b210c99c4"),
      body:
        uiText("ui.ui.kit.window.views.learn.81d67938db"),
      go: { view: "files", label: uiText("ui.ui.kit.window.views.learn.9088ca9c0d") },
    },
    {
      id: "frame",
      title: uiText("ui.ui.kit.window.views.learn.0216bc169a"),
      sub: uiText("ui.ui.kit.window.views.learn.8964cd8c03"),
      // Ревью 26.09 (W4 S1): «Контекст» читает тень кадра (`frame_shadow`), которая в модель
      // не уходит, снимается раз на ход и хранится для последних ходов. Обещать здесь
      // «ровно то, что видела модель, вызов за вызовом» — неправда.
      body:
        uiText("ui.ui.kit.window.views.learn.d4cb61bf26"),
      go: { view: "frame", label: uiText("ui.ui.kit.window.views.learn.ec399d9949") },
    },
    {
      id: "think",
      title: uiText("ui.ui.kit.window.views.learn.e497673800"),
      sub: model ? uiText("ui.ui.kit.window.views.learn.cbbb56cc98", {p0: String(model)}) : uiText("ui.ui.kit.window.views.learn.266c1ac833"),
      body:
        uiText("ui.ui.kit.window.views.learn.f45f5747f1") +
        (model ? uiText("ui.ui.kit.window.views.learn.b026d8ae90", {p0: String(model)}) : uiText("ui.ui.kit.window.views.learn.dec9824180")),
      go: { view: "settings", label: uiText("ui.ui.kit.window.views.learn.985b5e0f2c") },
    },
    {
      id: "hand",
      title: uiText("ui.ui.kit.window.views.learn.f6d53ecab1"),
      sub: uiText("ui.ui.kit.window.views.learn.ace0775cbc"),
      body:
        uiText("ui.ui.kit.window.views.learn.44922c91c8"),
      go: { view: "anatomy", label: uiText("ui.ui.kit.window.views.learn.6da215b1bd") },
    },
    {
      id: "reply",
      title: uiText("ui.ui.kit.window.views.learn.ce2d0c682d"),
      sub: uiText("ui.ui.kit.window.views.learn.de7f970c3b"),
      body:
        uiText("ui.ui.kit.window.views.learn.996269e2e0"),
      go: { view: "talk", label: uiText("ui.ui.kit.window.views.learn.917bb52293") },
    },
    {
      id: "close",
      title: uiText("ui.ui.kit.window.views.learn.f478f6b4db"),
      sub: "done · wait · blocked",
      body:
        uiText("ui.ui.kit.window.views.learn.e8002b693e"),
      go: { view: "now", label: uiText("ui.ui.kit.window.views.learn.460b59b99f") },
    },
  ];
}

/** Прямоугольники узлов в координатах схемы: по ним полёт ищет, какой блок зажечь. */
interface Box {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

const BOXES: Box[] = [
  { id: "word", x: 24, y: 44, w: 296, h: 64 },
  { id: "memory", x: 24, y: 140, w: 296, h: 64 },
  { id: "frame", x: 24, y: 236, w: 296, h: 172 },
  { id: "think", x: 424, y: 44, w: 280, h: 64 },
  { id: "hand", x: 424, y: 140, w: 280, h: 64 },
  { id: "reply", x: 424, y: 236, w: 280, h: 64 },
  { id: "close", x: 424, y: 332, w: 280, h: 64 },
];

/** Путь светлячка: слово → память → кадр → мост → думает → рука → петля → reply → конец. */
const ROUTE =
  "M172 76 L172 172 L172 322 L320 322 C372 322 372 76 424 76 L564 76 L564 172 " +
  "L704 172 C748 172 748 76 704 76 L564 76 L564 172 L564 268 L564 364";

/** Шаги проигрыша без полёта (узкое окно, reduced-motion): та же петля, что у светлячка. */
const SEQUENCE = ["word", "memory", "frame", "think", "hand", "think", "hand", "reply", "close"];

function flowSvg(list: Stage[]): string {
  const byId = new Map(list.map((s) => [s.id, s]));
  const node = (b: Box, accent = false, inner = "") => {
    const s = byId.get(b.id);
    if (!s) return "";
    return `<g class="fnode${accent ? " is-accent" : ""}" data-stage="${b.id}" tabindex="0" role="button"
        aria-label="${esc(s.title)}: ${esc(s.sub)}">
      <rect class="fnode-card" x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="14"/>
      <text class="fnode-title" x="${b.x + 18}" y="${b.y + 27}">${esc(s.title)}</text>
      <text class="fnode-sub" x="${b.x + 18}" y="${b.y + 47}">${esc(s.sub)}</text>
      ${inner}
    </g>`;
  };
  const keat = [
    ["K", uiText("ui.ui.kit.window.views.learn.8c3f192849")],
    ["E", uiText("ui.ui.kit.window.views.learn.699f887e86")],
    ["A", uiText("ui.ui.kit.window.views.learn.0c3f47dd2f")],
    ["T", uiText("ui.ui.kit.window.views.learn.c4497f4fd0")],
  ]
    .map(
      ([letter, text], i) => `
      <g class="keat-row">
        <circle cx="${24 + 30}" cy="${236 + 78 + i * 27}" r="10.5"/>
        <text class="keat-letter" x="${24 + 30}" y="${236 + 82 + i * 27}" text-anchor="middle">${letter}</text>
        <text class="keat-text" x="${24 + 50}" y="${236 + 82 + i * 27}">${esc(text)}</text>
      </g>`,
    )
    .join("");
  const down = (x: number, y1: number, y2: number) =>
    `<path class="flink" d="M${x} ${y1} L${x} ${y2 - 2}" marker-end="url(#lrn-head)"/>`;
  return uiText("ui.ui.kit.window.views.learn.adcbdc3a91", {p0: String(down(172, 108, 140)), p1: String(down(172, 204, 236)), p2: String(down(564, 108, 140)), p3: String(down(564, 204, 236)), p4: String(down(564, 300, 332)), p5: String(node(BOXES[0])), p6: String(node(BOXES[1])), p7: String(node(BOXES[2], false, keat)), p8: String(node(BOXES[3])), p9: String(node(BOXES[4])), p10: String(node(BOXES[5], true)), p11: String(node(BOXES[6])), p12: String(ROUTE)});
}

/** Та же схема узкой колонкой: ниже ~720px подписи SVG налезали бы друг на друга. */
function flowList(list: Stage[]): string {
  return `<ol class="flow-list">
    ${list
      .map(
        (s, i) => `<li>
          <button class="flow-step" type="button" data-stage="${s.id}">
            <span class="flow-num">${i + 1}</span>
            <span class="flow-step-text"><b>${esc(s.title)}</b><span>${esc(s.sub)}</span></span>
          </button>
        </li>`,
      )
      .join("")}
  </ol>`;
}

function detailHTML(s: Stage | undefined): string {
  if (!s) {
    return uiText("ui.ui.kit.window.views.learn.aad4d64c8f");
  }
  return `<div class="flow-detail-card">
    <b>${esc(s.title)}</b>
    <p>${esc(s.body)}</p>
    ${s.go ? uiText("ui.ui.kit.window.views.learn.7238647c1b", {p0: String(s.go.view), p1: String(esc(s.go.label))}) : ""}
  </div>`;
}

// ------------------------------------------------------------ между ходами

interface Rhythm {
  title: string;
  body: string;
  go: { view: View; label: string };
  icon: string;
}

function rhythms(): Rhythm[] {
  return [
    {
      title: uiText("ui.ui.kit.window.views.learn.b779a91f41"),
      body:
        uiText("ui.ui.kit.window.views.learn.dedca85b9a"),
      go: { view: "wakes", label: uiText("ui.ui.kit.window.views.learn.9643f15ecb") },
      icon: CAT_ICON.time,
    },
    {
      title: uiText("ui.ui.kit.window.views.learn.1c947e774a"),
      // Ревью 26.09 (W3 S1/S2, W4 S2/S5): окно сна — по часам машины (`boot.local_tz_name`),
      // сон ждёт тишины владельца, и пока он идёт, агент не отвечает. Закрытое окно сну не
      // мешает — оно уходит в трей; мешает спящий или выключенный компьютер.
      body: LOCAL_AGENT
        ? uiText("ui.ui.kit.window.views.learn.8c087eb7b0")
        : uiText("ui.ui.kit.window.views.learn.4a90472127"),
      go: { view: "files", label: uiText("ui.ui.kit.window.views.learn.9088ca9c0d") },
      icon: '<path d="M14.8 12.6A6.2 6.2 0 0 1 7.4 5.2a6.2 6.2 0 1 0 7.4 7.4Z"/>',
    },
    {
      title: uiText("ui.ui.kit.window.views.learn.627d85c45f"),
      body:
        uiText("ui.ui.kit.window.views.learn.34119292c6"),
      go: { view: "files", label: uiText("ui.ui.kit.window.views.learn.9088ca9c0d") },
      icon: '<path d="M5 3.5h7l3 3V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M11.8 3.8v3h3"/>',
    },
  ];
}

/** Полоса сна — по настоящему окну и выключателю из ручек движка (ревью 26.09, W4 S2).
 *  Окно к серверу ручек её сна не знает — полосы там нет, а не выдуманные 4–6. */
function sleepBand(): string {
  const sleep = S.agentState?.sleep;
  if (!sleep || !sleep.on) return "";
  const m = /^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$/.exec(String(sleep.window || ""));
  if (!m) return "";
  const from = Math.min(24, Number(m[1]));
  const to = Math.min(24, Number(m[2]));
  const width = Math.min((to > from ? to - from : 24 - from + to) / 24, 1 - from / 24);
  if (!(width > 0)) return "";
  return uiText("ui.ui.kit.window.views.learn.fa84a5da2d", {p0: String((from / 24) * 100), p1: String(width * 100)});
}

/** Сутки полосой: ночное окно сна, «сейчас» и точки — настоящие ходы за сегодня. */
function dayStrip(): string {
  const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24];
  return uiText("ui.ui.kit.window.views.learn.21cd8b0cc0", {p0: String(sleepBand()), p1: String(hours.map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}</span>`).join(""))});
}

const RUNS_LIMIT = 200;

function hhmm(d: Date): string {
  return d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit" });
}

function dayFraction(d: Date): number {
  return (d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds()) / 86400;
}

function turnsWord(n: number): string {
  const form = new Intl.PluralRules(locale()).select(n);
  if (form === "one") return uiText("ui.ui.kit.window.views.learn.a4beab58fa");
  if (form === "few") return uiText("ui.ui.kit.window.views.learn.975cdac6f6");
  return uiText("ui.ui.kit.window.views.learn.78417374ef");
}

async function fillDay(root: HTMLElement): Promise<void> {
  const box = root.querySelector<HTMLElement>(".lrn-day");
  if (!box) return;
  const now = new Date();
  const start = new Date(now);
  start.setHours(0, 0, 0, 0);
  const nowMark = box.querySelector<HTMLElement>(".lrn-day-now");
  if (nowMark) {
    nowMark.style.left = `${dayFraction(now) * 100}%`;
    nowMark.hidden = false;
  }
  let runs: Run[] | null = null;
  try {
    runs = await api<Run[]>(`/api/runs?limit=${RUNS_LIMIT}`);
  } catch {
    runs = null;
  }
  if (!box.isConnected) return;
  const note = box.querySelector<HTMLElement>(".lrn-day-note");
  const dots = box.querySelector<HTMLElement>(".lrn-day-dots");
  if (!runs) {
    if (note) note.textContent = uiText("ui.ui.kit.window.views.learn.0ede46e233");
    return;
  }
  const today = runs
    .map((r) => ({ r, at: new Date(r.created_at || r.updated_at || "") }))
    .filter(({ at }) => !isNaN(at.getTime()) && at >= start && at <= now)
    .sort((a, b) => a.at.getTime() - b.at.getTime());
  if (dots) {
    dots.innerHTML = today
      .map(({ r, at }) => {
        // В издании ход по будильнику — обычный chat_turn: отличить его в списке нечем, и
        // акцентная точка здесь была бы обещанием, которого окно не держит (ревью 26.09, W4 S8).
        const wake = !LOCAL_AGENT && r.kind === "wake";
        const label = `${hhmm(at)} — ${wake ? uiText("ui.ui.kit.window.views.learn.0f3c3b6b95") : uiText("ui.ui.kit.window.views.learn.a4beab58fa")}${runTitle(r) ? ": " + runTitle(r).slice(0, 90) : ""}`;
        return `<span class="lrn-day-dot${wake ? " is-wake" : ""}" style="left:${dayFraction(at) * 100}%" title="${esc(label)}"></span>`;
      })
      .join("");
  }
  const wakes = LOCAL_AGENT ? 0 : today.filter(({ r }) => r.kind === "wake").length;
  // Канал отдаёт последние 200 прогонов: если и самый старый из них сегодняшний,
  // ходов было больше, и точное число здесь было бы неправдой.
  const capped = runs.length >= RUNS_LIMIT && today.length === runs.length;
  let line = today.length
    ? uiText("ui.ui.kit.window.views.learn.b43744eda0", {p0: String(capped ? uiText("ui.ui.kit.window.views.learn.d034ffcdfd") : ""), p1: String(today.length), p2: String(turnsWord(today.length))}) +
      (wakes ? uiText("ui.ui.kit.window.views.learn.244a0dc82b", {p0: String(wakes)}) : ".")
    : uiText("ui.ui.kit.window.views.learn.ee66c80e10");
  const next = S.agentState?.next_wake ? new Date(S.agentState.next_wake) : null;
  const wakeMark = box.querySelector<HTMLElement>(".lrn-day-wake");
  if (next && !isNaN(next.getTime()) && next > now) {
    const sameDay = next.toDateString() === now.toDateString();
    line += uiText("ui.ui.kit.window.views.learn.ad6c2a8a4f", {p0: String(sameDay ? uiText("ui.ui.kit.window.views.learn.97e580dc07") : next.toLocaleDateString(locale(), { day: "numeric", month: "long" })), p1: String(hhmm(next))});
    if (sameDay && wakeMark) {
      wakeMark.style.left = `${dayFraction(next) * 100}%`;
      wakeMark.title = uiText("ui.ui.kit.window.views.learn.06b8cfec30", {p0: String(hhmm(next))});
      wakeMark.hidden = false;
    }
  }
  if (note) note.textContent = line;
}

// ------------------------------------------ договорённости, экономия, границы
//
// Слово Егора об онбординге (22.09): «как работает, что
// умеет, как правильно обращаться — и честные ограничения/возможные неудобства; контракты
// прямо там и объяснить с их плюсами, всё в одной вкладке». ⚠ Каждая строка ниже — правда
// об ИЗДАНИИ, сверенная с кодом и HELENE-MAP; ни одной цифры «для примера».

/** Договорённость и её плюс: правило, которое агент держит, и зачем оно владельцу. */
// Ревью 26.09 (W4 S5): строки с `local` — правда только об издании. У Praxis на сервере
// граница доставки — её (мимо руки текст не уходит), а настроек компьютера в окне нет.
const CONTRACTS: Array<{ rule: string; body: string; plus: string; local?: boolean; hereBody?: string }> = [
  {
    get rule() { return uiText("ui.ui.kit.window.views.learn.1a0a9ad1d3"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.ca8ee53a75"); },
    get hereBody() { return uiText("ui.ui.kit.window.views.learn.d8bc3ec9ab"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.a531f541fd"); },
  },
  {
    get rule() { return uiText("ui.ui.kit.window.views.learn.651dde029d"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.dbb5c6b32a"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.c540efd226"); },
  },
  {
    get rule() { return uiText("ui.ui.kit.window.views.learn.67dd95ab1f"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.e858c875be"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.92b2f89487"); },
  },
  {
    get rule() { return uiText("ui.ui.kit.window.views.learn.ab3a845373"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.983000f89c"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.b8163c650b"); },
  },
  {
    local: true,
    get rule() { return uiText("ui.ui.kit.window.views.learn.bdb5c984c5"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.741ee97c21"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.7ebf634462"); },
  },
  {
    get rule() { return uiText("ui.ui.kit.window.views.learn.475089fe94"); },
    get body() { return uiText("ui.ui.kit.window.views.learn.f3f8d4dc84"); },
    get plus() { return uiText("ui.ui.kit.window.views.learn.d4a7943568"); },
  },
];

/** Рычаги расхода: где деньги и что с этим можно сделать, без обещаний про кэш. */
function economy(): Array<{ title: string; body: string; go?: { view: View; label: string } }> {
  const rows: Array<{ title: string; body: string; go?: { view: View; label: string }; local?: boolean }> = [
  {
    title: uiText("ui.ui.kit.window.views.learn.348a9d7828"),
    body: uiText("ui.ui.kit.window.views.learn.2c2abaaeb6"),
  },
  {
    title: uiText("ui.ui.kit.window.views.learn.eef782a7b0"),
    body: uiText("ui.ui.kit.window.views.learn.21cf603705"),
    go: { view: "settings", label: uiText("ui.ui.kit.window.views.learn.985b5e0f2c") },
  },
  {
    local: true,
    title: uiText("ui.ui.kit.window.views.learn.7d283cdaba"),
    body: uiText("ui.ui.kit.window.views.learn.4c9d8ec883"),
    go: { view: "settings", label: uiText("ui.ui.kit.window.views.learn.985b5e0f2c") },
  },
  {
    title: uiText("ui.ui.kit.window.views.learn.9019130687"),
    body: uiText("ui.ui.kit.window.views.learn.fdee560a23") +
      (LOCAL_AGENT ? uiText("ui.ui.kit.window.views.learn.d091744bd5") : ""),
    go: { view: "wakes", label: uiText("ui.ui.kit.window.views.learn.9643f15ecb") },
  },
  ];
  return rows.filter((r) => LOCAL_AGENT || !r.local);
}

/** Честно о границах: что неудобно сегодня и что с этим делать. */
function limits(): Array<{ what: string; todo: string }> {
  // У агента на сервере (Praxis) свой харнесс: откат кода, ночное обслуживание индекса,
  // узкий взгляд для картинок. Местные неудобства издания там были бы неправдой о ней.
  const rows: Array<{ what: string; todo: string; local?: boolean }> = [
    {
      local: true,
      what: uiText("ui.ui.kit.window.views.learn.95a5464bbb"),
      todo: uiText("ui.ui.kit.window.views.learn.8706dccb15"),
    },
    {
      local: true,
      what: uiText("ui.ui.kit.window.views.learn.bd2e9745d5"),
      todo: uiText("ui.ui.kit.window.views.learn.8caed11fb2"),
    },
    {
      local: true,
      what: uiText("ui.ui.kit.window.views.learn.e3f068e9f1"),
      todo: uiText("ui.ui.kit.window.views.learn.d2edf7a378"),
    },
    {
      local: true,
      // Ревью 26.09 (W4 S11): шапка ловит ошибку ключа — но только после первого вызова.
      what: uiText("ui.ui.kit.window.views.learn.60c37c4fc3"),
      todo: uiText("ui.ui.kit.window.views.learn.292c8d4b85"),
    },
    {
      local: true,
      what: uiText("ui.ui.kit.window.views.learn.dfecec2b35"),
      todo: uiText("ui.ui.kit.window.views.learn.2da03dfd2b"),
    },
    {
      local: true,
      what: uiText("ui.ui.kit.window.views.learn.e873729a16"),
      todo: uiText("ui.ui.kit.window.views.learn.8705143011"),
    },
    {
      what: uiText("ui.ui.kit.window.views.learn.c035f1a50b"),
      todo: uiText("ui.ui.kit.window.views.learn.71f71ce18f"),
    },
  ];
  // Ревью 26.09 (W4 S7): на Mac строка своя — там нет ни сессии Windows, ни повышения прав.
  rows.splice(2, 0, isMacPlatform(S.platform)
    ? {
        local: true,
        what: uiText("ui.ui.kit.window.views.learn.cafdb20510"),
        todo: uiText("ui.ui.kit.window.views.learn.f37405b832"),
      }
    : S.platform === "linux" ? {
        local: true,
        what: uiText("ui.ui.kit.window.views.learn.956fc9893d"),
        todo: uiText("ui.ui.kit.window.views.learn.f201be721d"),
      } : {
        local: true,
        what: uiText("ui.ui.kit.window.views.learn.60a74c06a6"),
        todo: uiText("ui.ui.kit.window.views.learn.6dc575d4b3"),
      });
  return rows.filter((r) => LOCAL_AGENT || !r.local);
}

// ------------------------------------------------------------ как говорить

const HOW = (): Array<[string, string]> => [
  [
    uiText("ui.ui.kit.window.views.learn.5f5eed5ebc"),
    uiText("ui.ui.kit.window.views.learn.78bd2ec09f"),
  ],
  [
    uiText("ui.ui.kit.window.views.learn.42b094453f"),
    uiText("ui.ui.kit.window.views.learn.60ccd0501d"),
  ],
  [
    uiText("ui.ui.kit.window.views.learn.4ccb4c5c8d"),
    uiText("ui.ui.kit.window.views.learn.0f12f4a2f0"),
  ],
  [
    uiText("ui.ui.kit.window.views.learn.17d228a295"),
    uiText("ui.ui.kit.window.views.learn.e9d8872bd1"),
  ],
  [
    uiText("ui.ui.kit.window.views.learn.1d426a1f1c"),
    uiText("ui.ui.kit.window.views.learn.cd3562a174"),
  ],
];

// ------------------------------------------------------------ сборка страницы

const svgIcon = (paths: string, cls = "lrn-ico") =>
  `<svg class="${cls}" viewBox="0 0 20 20" aria-hidden="true">${paths}</svg>`;

function hero(): string {
  const st = S.agentState;
  // Чужой харнесс (Praxis на сервере) сердцебиения раннера не шлёт: там «на связи» —
  // это живой канал, а не пульс, которого нет по построению.
  const alive = foreignHarness() ? S.connected : !!st?.runner?.alive;
  const model = (st?.brain?.model || "").trim();
  const lead = LOCAL_AGENT
    ? uiText("ui.ui.kit.window.views.learn.719a53bcde")
    : uiText("ui.ui.kit.window.views.learn.33d456f302");
  const status = st
    ? `<span class="hero-pill${alive ? " is-live" : ""}"><i></i>${alive ? uiText("ui.ui.kit.window.views.learn.16ea738d75") : uiText("ui.ui.kit.window.views.learn.0929573961")}</span>` +
      (model ? uiText("ui.ui.kit.window.views.learn.c27771a87a", {p0: String(esc(model))}) : "")
    : "";
  return uiText("ui.ui.kit.window.views.learn.32f4a8887c", {p0: String(esc(lead)), p1: String(cfg.needs_local_setup ? uiText("ui.ui.kit.window.views.learn.0258588f30") : uiText("ui.ui.kit.window.views.learn.0ef3c22af4")), p2: String(status ? `<div class="hero-pills">${status}</div>` : '')});
}

function toc(): string {
  const items: Array<[string, string]> = [
    ["lrn-start", uiText("ui.ui.kit.window.views.learn.c160bf6084")],
    ["lrn-connect", uiText("ui.ui.kit.window.views.learn.5191b4529e")],
    ["lrn-map", uiText("ui.ui.kit.window.views.learn.857dd6280a")],
    ["lrn-tasks", uiText("ui.ui.kit.window.views.learn.db683a6a58")],
    ["lrn-flow", uiText("ui.ui.kit.window.views.learn.397b732abe")],
    ["lrn-how", uiText("ui.ui.kit.window.views.learn.9a8c7492c5")],
    ["lrn-rhythm", uiText("ui.ui.kit.window.views.learn.c7a6257079")],
    ["lrn-contracts", uiText("ui.ui.kit.window.views.learn.cc25e2cd1c")],
    ["lrn-economy", uiText("ui.ui.kit.window.views.learn.7bb229c56e")],
    ["lrn-limits", uiText("ui.ui.kit.window.views.learn.c86ee1bca3")],
  ];
  return uiText("ui.ui.kit.window.views.learn.6f80a33824", {p0: String(items.map(([id, label]) => `<button type="button" data-jump="${id}">${esc(label)}</button>`).join(""))});
}

function firstSteps(): string {
  const steps = [
    uiText("ui.ui.kit.window.views.learn.406520e4ee", {p0: String(sentence(INTRO(), "intro")), p1: String(takeLabel())}),
    uiText("ui.ui.kit.window.views.learn.9bba467ac9"),
    uiText("ui.ui.kit.window.views.learn.f78f24fdc2"),
  ];
  return `<ol class="lrn-steps">${steps.join("")}</ol>`;
}

function windowMap(): string {
  const places: Array<[View, string, string]> = [
    ['talk', uiText("ui.ui.kit.window.views.learn.917bb52293"), uiText("ui.ui.kit.window.views.learn.8ca5e4633d")],
    ['now', uiText("ui.ui.kit.window.views.learn.460b59b99f"), uiText("ui.ui.kit.window.views.learn.f9856ab2e5")],
    ['plans', uiText("ui.ui.kit.window.views.learn.03cc1c2d5a"), uiText("ui.ui.kit.window.views.learn.8c3d0aedbf")],
    ['wakes', uiText("ui.ui.kit.window.views.learn.9643f15ecb"), uiText("ui.ui.kit.window.views.learn.872b775161")],
    ['frame', uiText("ui.ui.kit.window.views.learn.ec399d9949"), uiText("ui.ui.kit.window.views.learn.a7ef148cca")],
    ['files', uiText("ui.ui.kit.window.views.learn.9088ca9c0d"), uiText("ui.ui.kit.window.views.learn.b51f21107b")],
    ['journal', uiText("ui.ui.kit.window.views.learn.97c459a6bb"), uiText("ui.ui.kit.window.views.learn.d99fd50cc7")],
    ['anatomy', uiText("ui.ui.kit.window.views.learn.6da215b1bd"), uiText("ui.ui.kit.window.views.learn.896ab156b0")],
  ];
  return `<div class="lrn-map">${places.map(([view,title,note]) => `<button type="button" class="lrn-map-item" data-go="${view}"><span><b>${title}</b><small>${note}</small></span><span aria-hidden="true">↗</span></button>`).join('')}</div>`;
}

/** Читает ли агент это окно. Окно к серверу (её харнесс) записок окна не читает:
 *  звать туда «В поле ввода» — обещать ответ, которого не будет (ревью 26.09, W4 S5). */
function windowIsRead(): boolean {
  return !(foreignHarness() || S.agentState?.runner?.ever === false);
}

function takeLabel(): string {
  return windowIsRead() ? uiText("ui.ui.kit.window.views.learn.7fc4df4166") : uiText("ui.ui.kit.window.views.learn.6e6029bc8e");
}

function taskCard(t: Task): string {
  const badges = [
    t.local ? uiText("ui.ui.kit.window.views.learn.473d65b6cf") : "",
    t.computer ? uiText("ui.ui.kit.window.views.learn.04d4856883") : "",
  ].join("");
  return `<article class="task" data-cat="${t.cat}" data-card="${t.id}">
    <div class="task-head">
      <span class="task-ico">${svgIcon(CAT_ICON[t.cat])}</span>
      <div><b>${esc(t.title)}</b><p>${esc(t.what)}</p></div>
    </div>
    <div class="frame-line">${sentence(t.template, t.id)}</div>
    <div class="task-foot">
      ${badges}
      <button class="task-take" type="button" data-take="${t.id}">${takeLabel()} <span aria-hidden="true">→</span></button>
    </div>
  </article>`;
}

// Живое между перерисовками: только запрошенный проигрыш и наблюдатель оглавления.
let stopPlay: () => void = () => {};
let spy: IntersectionObserver | null = null;

export async function render(container: HTMLElement): Promise<void> {
  stopPlay();
  spy?.disconnect();
  spy = null;

  const local = LOCAL_AGENT;
  const tasks = TASKS.filter((t) => local || !t.local);
  const cats = CATS.filter((c) => tasks.some((t) => t.cat === c.id));
  const list = stages();
  const motion = matchMedia("(prefers-reduced-motion: no-preference)").matches;

  container.innerHTML = uiText("ui.ui.kit.window.views.learn.9c5cbfebb4", {p0: String(hero()), p1: String(toc()), p2: String(firstSteps()), p3: String(local ? uiText("ui.ui.kit.window.views.learn.4e2da57e1c") : ''), p4: String(windowMap()), p5: String(tasks.length), p6: String(cats.map((c) => `<button type="button" class="lrn-chip" data-cat="${c.id}" aria-pressed="false">${esc(c.label)}</button>`).join("")), p7: String(tasks.map(taskCard).join("")), p8: String(flowSvg(list)), p9: String(flowList(list)), p10: String(detailHTML(undefined)), p11: String(HOW().map(([h, p]) => `<li class="note"><b>${esc(h)}</b><p>${esc(S.platform === "linux" && h === uiText("ui.ui.kit.window.views.learn.1d426a1f1c")
          ? uiText("ui.ui.kit.window.views.learn.66464be3a1") : p)}</p></li>`).join("")), p12: String(dayStrip()), p13: String(rhythms()
          .map(
            (r) => `<div class="rhythm">
              <span class="task-ico">${svgIcon(r.icon)}</span>
              <b>${esc(r.title)}</b>
              <p>${esc(r.body)}</p>
              <button class="flow-go" type="button" data-go="${r.go.view}">${esc(r.go.label)} <span aria-hidden="true">→</span></button>
            </div>`,
          )
          .join("")), p14: String(CONTRACTS.filter((c) => LOCAL_AGENT || !c.local).map(
          (c) => uiText("ui.ui.kit.window.views.learn.7ea4f5b58e", {p0: String(esc(c.rule)), p1: String(esc(c.body + (LOCAL_AGENT && c.hereBody ? c.hereBody : ""))), p2: String(esc(c.plus))}),
        ).join("")), p15: String(economy()
          .map(
            (e) => `<li class="lrn-econ-row">
              <div>
                <b>${esc(e.title)}</b>
                <p>${esc(e.body)}</p>
              </div>
              ${e.go ? `<button class="flow-go" type="button" data-go="${e.go.view}">${esc(e.go.label)} <span aria-hidden="true">→</span></button>` : ""}
            </li>`,
          )
          .join("")), p16: String(limits()
          .map(
            (l) => uiText("ui.ui.kit.window.views.learn.c532a89653", {p0: String(esc(l.what)), p1: String(esc(l.todo))}),
          )
          .join(""))});

  container.querySelectorAll('[data-voice-help]').forEach(button=>button.addEventListener('click',voiceHelp));
  container.querySelector('[data-onboarding-done]')?.addEventListener('click',()=>{
    if(!cfg.needs_local_setup)localStorage.setItem('helene.onboarding.210','done');
    dispatchEvent(new CustomEvent('frame-go',{detail:cfg.needs_local_setup?'settings':'talk'}));
  });
  const root = container.querySelector<HTMLElement>(".learn");
  if (!root) return;
  const byId = new Map(list.map((s) => [s.id, s]));
  const detail = root.querySelector<HTMLElement>(".flow-detail");
  const playBtn = root.querySelector<HTMLButtonElement>(".flow-play");

  // ---- оглавление: какой раздел сейчас перед глазами
  const tocButtons = [...root.querySelectorAll<HTMLButtonElement>(".lrn-toc [data-jump]")];
  if ("IntersectionObserver" in window) {
    spy = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          for (const b of tocButtons) b.setAttribute("aria-current", b.dataset.jump === e.target.id ? "true" : "false");
        }
      },
      { rootMargin: "-35% 0px -60% 0px" },
    );
    for (const b of tocButtons) {
      const sec = root.querySelector(`#${b.dataset.jump}`);
      if (sec) spy.observe(sec);
    }
  }

  // ---- выбор блока схемы
  const lit = (id: string | null) => {
    for (const n of root.querySelectorAll<Element>("[data-stage]")) {
      n.classList.toggle("is-lit", !!id && (n as HTMLElement).dataset.stage === id);
    }
  };
  const select = (id: string) => {
    lit(id);
    if (detail) detail.innerHTML = detailHTML(byId.get(id));
  };
  for (const n of root.querySelectorAll<HTMLElement | SVGGElement>("[data-stage]")) {
    const id = (n as HTMLElement).dataset.stage || "";
    n.addEventListener("click", () => {
      stopPlay();
      select(id);
    });
    n.addEventListener("keydown", (e) => {
      const k = (e as KeyboardEvent).key;
      if (k === "Enter" || k === " ") {
        e.preventDefault();
        stopPlay();
        select(id);
      }
    });
  }

  // ---- «Проиграть ход»: светлячок по схеме или шаги без полёта
  const setPlaying = (on: boolean) => {
    if (!playBtn) return;
    playBtn.setAttribute("aria-pressed", on ? "true" : "false");
    const label = playBtn.querySelector("span");
    if (label) label.textContent = on ? uiText("ui.ui.kit.window.views.learn.3396400da0") : uiText("ui.ui.kit.window.views.learn.e3be65a05a");
    playBtn.querySelector(".ico-play")?.setAttribute("d", on ? "M6.5 5.5h7v9h-7Z" : "M7 5.2v9.6L15 10Z");
  };
  const play = () => {
    stopPlay();
    const svg = root.querySelector<SVGSVGElement>("svg.flow-svg");
    const route = svg?.querySelector<SVGPathElement>("#lrn-route");
    const pulse = svg?.querySelector<SVGGElement>(".lrn-fly");
    const flying = !!(svg && route && pulse && motion && svg.getBoundingClientRect().width > 0);
    setPlaying(true);
    if (!flying) {
      let i = 0;
      let timer = 0;
      const tick = () => {
        if (!root.isConnected) return;
        select(SEQUENCE[i]);
        i += 1;
        if (i < SEQUENCE.length) timer = window.setTimeout(tick, 900);
        else setPlaying(false);
      };
      timer = window.setTimeout(tick, 0);
      stopPlay = () => {
        window.clearTimeout(timer);
        setPlaying(false);
        stopPlay = () => {};
      };
      return;
    }
    const total = route!.getTotalLength();
    const dots = pulse!.querySelectorAll("circle");
    const duration = 9000;
    const t0 = performance.now();
    let current = "";
    let raf = 0;
    svg!.classList.add("is-playing");
    const place = (x: number, y: number) => {
      for (const d of dots) {
        d.setAttribute("cx", String(x));
        d.setAttribute("cy", String(y));
      }
    };
    const finish = () => {
      cancelAnimationFrame(raf);
      svg!.classList.remove("is-playing");
      place(-50, -50);
      setPlaying(false);
      stopPlay = () => {};
    };
    const step = (now: number) => {
      if (!root.isConnected) return;
      const k = Math.min(1, (now - t0) / duration);
      const eased = k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      const p = route!.getPointAtLength(total * (0.08 * k + 0.92 * eased));
      place(p.x, p.y);
      const hit = BOXES.find((b) => p.x >= b.x && p.x <= b.x + b.w && p.y >= b.y && p.y <= b.y + b.h);
      if (hit && hit.id !== current) {
        current = hit.id;
        select(current);
      }
      if (k < 1) raf = requestAnimationFrame(step);
      else finish();
    };
    stopPlay = finish;
    raf = requestAnimationFrame(step);
  };
  playBtn?.addEventListener("click", () => {
    if (playBtn.getAttribute("aria-pressed") === "true") stopPlay();
    else play();
  });

  // ---- переходы в разделы окна и прыжки по странице
  root.addEventListener("click", (e) => {
    const target = e.target as HTMLElement;
    const go = target.closest<HTMLElement>("[data-go]");
    if (go?.dataset.go) {
      stopPlay();
      dispatchEvent(new CustomEvent("frame-go", { detail: go.dataset.go as View }));
      return;
    }
    const jump = target.closest<HTMLElement>("[data-jump]");
    if (jump?.dataset.jump) {
      root.querySelector(`#${jump.dataset.jump}`)?.scrollIntoView({ behavior: motion ? "smooth" : "auto", block: "start" });
      return;
    }
    const take = target.closest<HTMLElement>("[data-take]");
    if (take?.dataset.take) {
      const key = take.dataset.take;
      const tpl = key === "intro" ? INTRO() : TASKS.find((t) => t.id === key)?.template;
      if (!tpl) return;
      // Раздел не пишет в поле сам: он просит каркас открыть переписку с агентом
      // ЭТОГО окна и положить туда текст. Каркас знает и про черновик, который
      // нельзя затирать, и про то, в какую комнату это класть.
      const text = assemble(tpl, root, key);
      if (!windowIsRead()) {
        // Окно к серверу агент не читает: рамка уходит в буфер — отправить её туда, где он
        // слышит (Telegram), а не в поле, откуда записка ляжет в дерево без читателя.
        void copyText(text).then((ok) => {
          take.textContent = ok ? uiText("ui.ui.kit.window.views.learn.77a0134ded")
            : uiText("ui.ui.kit.window.views.learn.f73b255abe");
        });
        return;
      }
      dispatchEvent(new CustomEvent("frame-template", { detail: text }));
      return;
    }
    // Щелчок по карточке мимо полей и кнопок — к первому пустому пропуску.
    const card = target.closest<HTMLElement>(".task");
    if (card && !target.closest("input, button")) {
      const blanks = [...card.querySelectorAll<HTMLInputElement>("input.blank")];
      (blanks.find((b) => !b.value.trim()) ?? blanks[0])?.focus();
    }
  });

  // ---- пропуски: ширина по тексту, Enter — «в поле ввода»
  for (const input of root.querySelectorAll<HTMLInputElement>("input.blank")) {
    const fit = () => {
      input.size = Math.max(4, (input.value || input.placeholder).length + 1);
      input.classList.toggle("is-filled", !!input.value.trim());
    };
    input.addEventListener("input", fit);
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const key = input.dataset.key || "";
      root.querySelector<HTMLButtonElement>(`[data-take="${key}"]`)?.click();
    });
  }

  // ---- фильтр рамок
  const chipButtons = [...root.querySelectorAll<HTMLButtonElement>(".lrn-chip")];
  for (const chip of chipButtons) {
    chip.addEventListener("click", () => {
      const cat = chip.dataset.cat || "all";
      for (const c of chipButtons) c.setAttribute("aria-pressed", c === chip ? "true" : "false");
      for (const card of root.querySelectorAll<HTMLElement>(".task")) {
        card.hidden = cat !== "all" && card.dataset.cat !== cat;
      }
    });
  }

  // ---- сутки: настоящие ходы за сегодня (не блокирует страницу)
  void fillDay(root);
  mountConnectionGuide(root.querySelector<HTMLElement>("#connection-guide")!);
}
