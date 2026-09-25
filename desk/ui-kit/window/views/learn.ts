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
import { api } from "../api";
import { esc } from "../lib";
import { LOCAL_AGENT, S, foreignHarness, type Run, type View } from "../state";
import { isMacPlatform } from "../../platform";
import { copyText } from "../../dom";

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
  { id: "people", label: "Люди и память" },
  { id: "time", label: "Время" },
  { id: "know", label: "Найти и понять" },
  { id: "pc", label: "Этот компьютер" },
  { id: "words", label: "Слова" },
];

const TASKS: Task[] = [
  {
    id: "meet",
    cat: "people",
    title: "Познакомить с человеком",
    what: "Заведёт досье и будет помнить: кто это, что важно, о чём не спрашивать.",
    template: "Познакомься: <имя> — <кто это мне>, <что важно знать>.",
  },
  {
    id: "remind",
    cat: "time",
    title: "Напомнить вовремя",
    what: "Поставит будильник и придёт сам, даже если окно закрыто.",
    template: "Напомни мне <когда> про <что>.",
  },
  {
    id: "duty",
    cat: "time",
    title: "Дежурить по расписанию",
    what: "Будет просыпаться сам и писать, только когда есть что сказать.",
    template: "Каждый <день и время> проверяй <что> и пиши мне, если <при каком условии>.",
  },
  {
    id: "search",
    cat: "know",
    title: "Поискать и свести",
    what: "Сходит в интернет, прочитает найденное и вернётся со сводкой и ссылками.",
    template: "Найди <что нужно узнать> и сведи в пять пунктов. Ссылки на источники приложи.",
  },
  {
    id: "follow",
    cat: "time",
    title: "Следить за изменением",
    what: "Запомнит, как было, и скажет, когда станет иначе.",
    template: "Раз в <как часто> смотри <адрес или файл> и скажи, когда <что> изменится.",
  },
  {
    id: "read",
    cat: "know",
    title: "Разобрать документ",
    what: "Прочитает целиком и выпишет то, что просили, своими словами.",
    template: "Прочитай <файл или ссылку> и выпиши <что именно нужно>.",
    local: true,
  },
  {
    id: "folder",
    cat: "pc",
    title: "Посмотреть папку",
    what: "Заглянет в открытую ему папку и ответит по тому, что там лежит.",
    template: "Посмотри в <папка> и скажи, <что нужно понять>.",
    local: true,
  },
  {
    id: "write",
    cat: "words",
    title: "Написать за меня",
    what: "Составит текст и покажет; отправляет только по твоему слову.",
    template: "Напиши <кому> про <что>. Тон — <какой>. Длина — <сколько>.",
  },
  {
    id: "diary",
    cat: "people",
    title: "Вести дневник работы",
    what: "Запишет решение так, чтобы через месяц было понятно, почему так решили.",
    template: "Запиши в дневник: по <теме> мы решили <что> — потому что <почему>.",
  },
  {
    id: "explain",
    cat: "know",
    title: "Разобраться в чужом тексте",
    what: "Объяснит простыми словами и честно скажет, чего не понял.",
    template: "Объясни простыми словами: <что непонятно>. Я знаю про это <сколько>.",
  },
  {
    id: "computer",
    cat: "pc",
    title: "Сделать на компьютере",
    what: "Откроет программу и сделает шаги, показывая, что видит на экране.",
    template: "Открой <программа> и <что сделать>. Покажи, что получилось.",
    local: true,
    computer: true,
  },
  {
    id: "obstacles",
    cat: "words",
    title: "Спросить, что ему мешает",
    what: "Ответит про свои ограничения по делу: что не может и почему.",
    template: "Что тебе мешает делать <какая работа>? Чего тебе не хватает?",
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
  { label: "Познакомить", template: template("meet") },
  { label: "Напомнить", template: template("remind") },
  { label: "Поискать и свести", template: template("search") },
  { label: "Что тебе мешает?", template: template("obstacles") },
];

/** Рамка «представься» первого шага — те же правила: только пропуски. */
const INTRO = "Меня зовут <имя>. Я <чем занимаюсь>. Мне важно, чтобы ты <что именно>.";

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
      title: "Твоё слово",
      sub: "сначала ложится в архив",
      body:
        "Сообщение сначала записывается в архив комнаты, и только потом начинается ход. " +
        "Пишешь ли ты в окно или в Telegram — путь один и тот же.",
      go: { view: "talk", label: "Чат" },
    },
    {
      id: "memory",
      title: "Память",
      sub: "досье, дневник, архив, свёртки",
      body:
        "Всё, что агент знает о людях, о работе и о себе, лежит обычными файлами: досье, " +
        "дневник, архив каждой комнаты и свёртки старых разговоров. Их можно открыть и прочитать.",
      go: { view: "files", label: "Файлы" },
    },
    {
      id: "frame",
      title: "Кадр",
      sub: "из чего он исходит",
      // Ревью 26.09 (W4 S1): «Контекст» читает тень кадра (`frame_shadow`), которая в модель
      // не уходит, снимается раз на ход и хранится для последних ходов. Обещать здесь
      // «ровно то, что видела модель, вызов за вызовом» — неправда.
      body:
        "Перед каждым вызовом модели агент собирает кадр: кто он и что обещал, что помнит о людях " +
        "и о себе, прошлое разговора и то, что происходит сейчас. «Контекст» показывает теневую " +
        "сборку этого кадра по слоям — K: конституция, E: эпоха, слепок знаний о себе, A: свёрнутое " +
        "прошлое и свежие реплики, T: кто говорит и что сейчас. Это прибор: сборка снимается раз " +
        "на ход и в модель не уходит, байты самого вызова в ней не показаны.",
      go: { view: "frame", label: "Контекст" },
    },
    {
      id: "think",
      title: "Думает",
      sub: model ? `модель ${model}` : "модель из настроек",
      body:
        "Модель читает кадр и решает, чего не хватает: ответить сразу, сходить за фактами " +
        "или сделать что-то руками." +
        (model ? ` Сейчас это «${model}»; сменить можно в настройках.` : " Какая модель думает, выбирается в настройках."),
      go: { view: "settings", label: "Настройки" },
    },
    {
      id: "hand",
      title: "Зовёт руку",
      sub: "поиск, файл, память, компьютер",
      body:
        "Рука — это тул: поиск, чтение файла, запись в память, будильник, компьютер. Её ответ " +
        "возвращается в тот же ход, и модель думает снова — столько раз, сколько нужно. " +
        "У каждой руки есть описание: когда её звать.",
      go: { view: "anatomy", label: "Система" },
    },
    {
      id: "reply",
      title: "Говорит рукой reply",
      sub: "так слова уходят наружу",
      body:
        "Слова наружу уносит рука reply. Поэтому агент может сказать несколько реплик по ходу " +
        "работы — «смотрю», «нашёл», «вот итог», — а не одну в самом конце.",
      go: { view: "talk", label: "Чат" },
    },
    {
      id: "close",
      title: "Закрывает ход",
      sub: "done · wait · blocked",
      body:
        "Ход закрывает сам агент: done — сделано, wait — ждёт условия, blocked — упёрся в " +
        "препятствие. Если сказать нечего, он может промолчать — это записывается как его " +
        "решение, а не как сбой.",
      go: { view: "now", label: "Сейчас" },
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
    ["K", "конституция — кто он"],
    ["E", "эпоха — знание о себе"],
    ["A", "накопитель — весь разговор"],
    ["T", "текущее — что сейчас"],
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
  return `<svg class="flow-svg" viewBox="0 0 760 420" role="group"
      aria-label="Схема одного хода: слово ложится в память, из памяти собирается кадр, модель думает, зовёт руки и говорит рукой reply">
    <defs>
      <marker id="lrn-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
        <path d="M1 1 L9 5 L1 9" fill="none" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      </marker>
      <radialGradient id="lrn-glow">
        <stop offset="0" stop-color="var(--accent)" stop-opacity=".55"/>
        <stop offset="1" stop-color="var(--accent)" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <text class="flow-col" x="24" y="26">ЧТО ПРИЕЗЖАЕТ МОДЕЛИ</text>
    <text class="flow-col" x="424" y="26">ЧТО ДЕЛАЕТ АГЕНТ</text>
    ${down(172, 108, 140)}
    ${down(172, 204, 236)}
    <path class="flink flink-bridge" d="M320 322 C372 322 372 76 422 76" marker-end="url(#lrn-head)"/>
    ${down(564, 108, 140)}
    <path class="flink flink-loop" d="M704 172 C748 172 748 76 706 76" marker-end="url(#lrn-head)"/>
    <text class="flow-loop" x="580" y="128">↺ и снова, пока нужно</text>
    ${down(564, 204, 236)}
    ${down(564, 300, 332)}
    ${node(BOXES[0])}
    ${node(BOXES[1])}
    ${node(BOXES[2], false, keat)}
    ${node(BOXES[3])}
    ${node(BOXES[4])}
    ${node(BOXES[5], true)}
    ${node(BOXES[6])}
    <path id="lrn-route" d="${ROUTE}" fill="none" stroke="none"/>
    <g class="lrn-fly" aria-hidden="true">
      <circle class="lrn-fly-glow" r="18" cx="-50" cy="-50" fill="url(#lrn-glow)"/>
      <circle class="lrn-fly-dot" r="5" cx="-50" cy="-50"/>
    </g>
  </svg>`;
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
    return `<p class="flow-hint">Нажми на любой блок — расскажу, что в нём происходит и где это видно вживую.
      Или проиграй ход целиком.</p>`;
  }
  return `<div class="flow-detail-card">
    <b>${esc(s.title)}</b>
    <p>${esc(s.body)}</p>
    ${s.go ? `<button class="flow-go" type="button" data-go="${s.go.view}">Где это видно: ${esc(s.go.label)} <span aria-hidden="true">→</span></button>` : ""}
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
      title: "Будильники",
      body:
        "Агент сам ставит себе будильники и приходит к сроку — даже если никто не писал. " +
        "Каждое пробуждение — обычный ход со своей целью.",
      go: { view: "wakes", label: "Пробуждения" },
      icon: CAT_ICON.time,
    },
    {
      title: "Сон",
      // Ревью 26.09 (W3 S1/S2, W4 S2/S5): окно сна — по часам машины (`boot.local_tz_name`),
      // сон ждёт тишины владельца, и пока он идёт, агент не отвечает. Закрытое окно сну не
      // мешает — оно уходит в трей; мешает спящий или выключенный компьютер.
      body: LOCAL_AGENT
        ? "Раз в сутки агент спит: сводит прожитый день в дневник, формулирует выводы о людях и " +
          "о себе, пересобирает карту памяти. Окно сна — с 4 до 6 утра по часам этого компьютера, " +
          "и начинается сон, только когда ты минут двадцать ничего не пишешь. Пока идёт сон — " +
          "обычно несколько минут, — агент не отвечает: сообщения ждут его конца. Если компьютер " +
          "в это время спал или программа была выключена, сон будет в следующую ночь, а после " +
          "двух суток без сна — в первую тихую минуту работы."
        : "Ночью агент спит на своём сервере: сводит прожитый день в дневник, формулирует выводы " +
          "о людях и о себе, пересобирает карту памяти. Пока идёт сон, ответ на сообщение ждёт " +
          "его конца; открыто это окно или закрыто — сну не важно.",
      go: { view: "files", label: "Файлы" },
      icon: '<path d="M14.8 12.6A6.2 6.2 0 0 1 7.4 5.2a6.2 6.2 0 1 0 7.4 7.4Z"/>',
    },
    {
      title: "Между запусками",
      body:
        "Всё записанное переживает закрытие окна и обновление программы: память — это файлы, " +
        "а не состояние процесса.",
      go: { view: "files", label: "Файлы" },
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
  return `<div class="lrn-day-sleep" style="left:${(from / 24) * 100}%;width:${width * 100}%"><span>сон</span></div>`;
}

/** Сутки полосой: ночное окно сна, «сейчас» и точки — настоящие ходы за сегодня. */
function dayStrip(): string {
  const hours = [0, 3, 6, 9, 12, 15, 18, 21, 24];
  return `<div class="lrn-day" aria-describedby="lrn-day-note">
    <div class="lrn-day-track">
      ${sleepBand()}
      <div class="lrn-day-dots"></div>
      <div class="lrn-day-now" hidden><span>сейчас</span></div>
      <div class="lrn-day-wake" hidden></div>
    </div>
    <div class="lrn-day-scale">${hours.map((h) => `<span style="left:${(h / 24) * 100}%">${String(h).padStart(2, "0")}</span>`).join("")}</div>
    <p id="lrn-day-note" class="lrn-day-note">Смотрю, какие ходы были сегодня…</p>
  </div>`;
}

const RUNS_LIMIT = 200;

function hhmm(d: Date): string {
  return d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function dayFraction(d: Date): number {
  return (d.getHours() * 3600 + d.getMinutes() * 60 + d.getSeconds()) / 86400;
}

function turnsWord(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "ход";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "хода";
  return "ходов";
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
    if (note) note.textContent = "Нет связи с агентом — ходы за сегодня появятся здесь, когда он будет на связи.";
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
        const label = `${hhmm(at)} — ${wake ? "пробуждение" : "ход"}${r.goal_head ? ": " + r.goal_head.slice(0, 90) : ""}`;
        return `<span class="lrn-day-dot${wake ? " is-wake" : ""}" style="left:${dayFraction(at) * 100}%" title="${esc(label)}"></span>`;
      })
      .join("");
  }
  const wakes = LOCAL_AGENT ? 0 : today.filter(({ r }) => r.kind === "wake").length;
  // Канал отдаёт последние 200 прогонов: если и самый старый из них сегодняшний,
  // ходов было больше, и точное число здесь было бы неправдой.
  const capped = runs.length >= RUNS_LIMIT && today.length === runs.length;
  let line = today.length
    ? `Сегодня — ${capped ? "не меньше " : ""}${today.length} ${turnsWord(today.length)}` +
      (wakes ? `, из них пробуждений по своему будильнику: ${wakes}.` : ".")
    : "Сегодня ходов ещё не было — первый начнётся с твоего слова.";
  const next = S.agentState?.next_wake ? new Date(S.agentState.next_wake) : null;
  const wakeMark = box.querySelector<HTMLElement>(".lrn-day-wake");
  if (next && !isNaN(next.getTime()) && next > now) {
    const sameDay = next.toDateString() === now.toDateString();
    line += ` Следующее пробуждение — ${sameDay ? "сегодня" : next.toLocaleDateString("ru-RU", { day: "numeric", month: "long" })} в ${hhmm(next)}.`;
    if (sameDay && wakeMark) {
      wakeMark.style.left = `${dayFraction(next) * 100}%`;
      wakeMark.title = `следующее пробуждение — ${hhmm(next)}`;
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
    rule: "Слова наружу — рукой reply",
    body: "Агент говорит, вызывая руку reply; текст, написанный мимо неё, — заметка.",
    hereBody: " Если модель всё же ответила мимо руки, окно доставит этот текст само и пометит ход.",
    plus: "несколько реплик по ходу работы и точная расписка, что именно ушло",
  },
  {
    rule: "Ход закрывается исходом",
    body: "done — сделано, wait — ждёт условия (оно записано), blocked — упёрся в препятствие " +
      "(оно названо).",
    plus: "ничего не висит молча: всегда видно, чем кончился ход и чего он ждёт",
  },
  {
    rule: "Молчание — тоже решение",
    body: "Если сказать нечего, агент не отвечает ради ответа, и это записывается как его выбор, " +
      "а не как сбой.",
    plus: "меньше пустых реплик — и честный след, почему он промолчал",
  },
  {
    rule: "Память — это файлы",
    body: "Досье, дневник, архивы комнат и свёртки лежат обычными файлами в папке данных.",
    plus: "их можно открыть, прочитать и поправить; они переживают перезапуск и обновление",
  },
  {
    local: true,
    rule: "Права на компьютер даёшь ты",
    body: "Файлы, чтение окон, программы — отдельные права; без выданного права рука отказывает " +
      "до того, как что-то тронет.",
    plus: "агент не лезет туда, куда его не звали, а выданное видно в настройках",
  },
  {
    rule: "Ход переживает перезапуск",
    body: "Шаги хода пишутся на диск по мере работы; оборванный ход после перезапуска " +
      "поднимается снова, а не теряется.",
    plus: "закрыть окно посреди работы — не значит потерять поручение",
  },
];

/** Рычаги расхода: где деньги и что с этим можно сделать, без обещаний про кэш. */
function economy(): Array<{ title: string; body: string; go?: { view: View; label: string } }> {
  const rows: Array<{ title: string; body: string; go?: { view: View; label: string }; local?: boolean }> = [
  {
    title: "Каждый вызов модели стоит денег",
    body: "Свой ключ оплачивается по токенам, подписка — расходует свои лимиты. Самый дорогой — " +
      "первый вызов хода: модель читает кадр целиком. Сколько весил последний вызов и какая доля " +
      "пришла из кэша провайдера — в шапке окна.",
  },
  {
    title: "Модель — по делу",
    body: "Модель и глубина размышления выбираются в настройках: для будничных дел хватает " +
      "быстрой и неглубокой, сильную стоит держать для сложного.",
    go: { view: "settings", label: "Настройки" },
  },
  {
    local: true,
    title: "Подписка вместо ключа",
    body: "Если у тебя есть подписка ChatGPT, агент может думать через неё — без оплаты по " +
      "токенам, в пределах лимитов подписки.",
    go: { view: "settings", label: "Настройки" },
  },
  {
    title: "Фон — по твоему слову",
    body: "Пробуждения по расписанию и ночной сон тоже зовут модель. Попроси агента умерить " +
      "фоновую работу — у него для этого своя рука: пока пауза, пробуждения по расписанию " +
      "пропускаются, а сон откладывается; разовый будильник на сегодня срабатывает." +
      (LOCAL_AGENT ? " Сон выключается совсем строкой PRAXIS_SLEEP_CYCLE=off в env файла helene.json." : ""),
    go: { view: "wakes", label: "Пробуждения" },
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
      what: "Текстовая модель не видит картинок и скриншотов — только их описание или ссылку.",
      todo: "Для работы с экраном выбери зрячую модель или пусть агент читает окно текстом (read_window).",
    },
    {
      local: true,
      what: "Бот в Telegram — не аккаунт: в группе он отвечает на обращение, истории до своего " +
        "появления не видит и сам в группы не вступает.",
      todo: "Добавь бота в группу сам и обращайся к нему по имени.",
    },
    {
      local: true,
      what: "Если агент сломает собственный код, сам он из этого не выберется: отката у кода нет " +
        "(снимки ведутся для души, навыков и рабочей папки).",
      todo: "Причина — в data/runner.log; поправить руками или переустановить поверх, данные останутся.",
    },
    {
      local: true,
      // Ревью 26.09 (W4 S11): шапка ловит ошибку ключа — но только после первого вызова.
      what: "Протухший ключ модели виден только после первого неудачного вызова: до него шапка " +
        "говорит «на связи».",
      todo: "После неудачного вызова шапка скажет «Модель отвечает ошибкой» и продержит это " +
        "15 минут; ключ меняется в настройках.",
    },
    {
      local: true,
      what: "Индекс памяти обновляется в фоне: после сбоя его базы поиск по памяти до четверти " +
        "часа может отвечать неполно.",
      todo: "Подождать; сами записи при этом целы — это файлы.",
    },
    {
      local: true,
      what: "Сон и пробуждения случаются, только пока программа запущена (окно при этом может " +
        "быть свёрнуто в трей).",
      todo: "Пропущенный сон будет в следующую ночь, а после двух суток без сна — в первую тихую " +
        "минуту работы программы.",
    },
    {
      what: "Модель может ошибиться — и в словах, и в записях о людях.",
      todo: "Записи — файлы: открой и поправь, агент прочитает исправленное.",
    },
  ];
  // Ревью 26.09 (W4 S7): на Mac строка своя — там нет ни сессии Windows, ни повышения прав.
  rows.splice(2, 0, isMacPlatform(S.platform)
    ? {
        local: true,
        what: "Руки компьютера работают в твоей сессии macOS и только с разрешениями «Запись " +
          "экрана» и «Универсальный доступ» для программы. До входа в систему агент не работает.",
        todo: "Разрешения выдаются в Системных настройках → Конфиденциальность и безопасность.",
      }
    : {
        local: true,
        what: "Руки компьютера работают только в твоей сессии Windows и не достают окна, " +
          "запущенные от администратора. До входа в систему агент не работает.",
        todo: "Нужное окно — запускать без повышения прав.",
      });
  return rows.filter((r) => LOCAL_AGENT || !r.local);
}

// ------------------------------------------------------------ как говорить

const HOW: Array<[string, string]> = [
  [
    "Пиши словами, а не командами",
    "Ему не нужен особый синтаксис: «посмотри, что там с почтой» работает так же, как строгая формулировка. " +
      "Чем понятнее, зачем это нужно, тем меньше он переспрашивает.",
  ],
  [
    "Скажи, чем кончить",
    "«Скажи мне, если что-то не так» — это условие остановки. Без него он не знает, когда работа считается сделанной, " +
      "и спросит сам.",
  ],
  [
    "Он может промолчать",
    "Ход закрывает он, а не пустота: если сказать нечего, он молчит — и это записано как его решение, а не как сбой.",
  ],
  [
    "Память переживает перезапуск",
    "Что он записал о тебе и о работе, останется после закрытия окна и после обновления программы. " +
      "Всё это лежит файлами — раздел «Файлы».",
  ],
];

// ------------------------------------------------------------ сборка страницы

const svgIcon = (paths: string, cls = "lrn-ico") =>
  `<svg class="${cls}" viewBox="0 0 20 20" aria-hidden="true">${paths}</svg>`;

function hero(): string {
  const name = (S.agent || "Агент").trim();
  const st = S.agentState;
  // Чужой харнесс (Praxis на сервере) сердцебиения раннера не шлёт: там «на связи» —
  // это живой канал, а не пульс, которого нет по построению.
  const alive = foreignHarness() ? S.connected : !!st?.runner?.alive;
  const model = (st?.brain?.model || "").trim();
  const lead = LOCAL_AGENT
    ? "Агент живёт на этом компьютере: помнит разговоры, сам приходит по будильникам, ночью спит — " +
      "сводит день в память — и работает руками: ищет, читает, пишет, а с твоего разрешения — и за компьютером."
    : "Агент живёт на сервере, а это окно — дверь к нему: разговор, его память и то, как идёт каждый ход.";
  const status = st
    ? `<span class="hero-pill${alive ? " is-live" : ""}"><i></i>${alive ? "на связи" : "не на связи"}</span>` +
      (model ? `<span class="hero-pill">думает моделью <b>${esc(model)}</b></span>` : "")
    : "";
  return `<header class="lrn-hero reveal" id="lrn-top">
    <div class="lrn-hero-text">
      <p class="lrn-kicker">Знакомство</p>
      <h2 class="lrn-hero-title">Знакомься: <span class="lrn-name">${esc(name)}<svg class="lrn-underline" viewBox="0 0 200 14" preserveAspectRatio="none" aria-hidden="true"><path pathLength="1" d="M3 9 C 40 3, 70 12, 104 7 S 170 4, 197 8"/></svg></span></h2>
      <p class="lrn-lead">${esc(lead)}</p>
      ${status ? `<div class="hero-pills">${status}</div>` : ""}
    </div>
    <svg class="lrn-orbit" viewBox="0 0 240 240" aria-hidden="true">
      <circle class="orbit orbit-1" cx="120" cy="120" r="46"/>
      <circle class="orbit orbit-2" cx="120" cy="120" r="78"/>
      <circle class="orbit orbit-3" cx="120" cy="120" r="108"/>
      <g class="spin spin-1"><circle class="moon" cx="166" cy="120" r="5"/></g>
      <g class="spin spin-2"><circle class="moon moon-soft" cx="120" cy="42" r="6.5"/></g>
      <g class="spin spin-3"><circle class="moon" cx="12" cy="120" r="4"/><circle class="moon moon-soft" cx="228" cy="120" r="3"/></g>
      <circle class="core" cx="120" cy="120" r="18"/>
      <circle class="core-ring" cx="120" cy="120" r="26"/>
    </svg>
  </header>`;
}

function toc(): string {
  const items: Array<[string, string]> = [
    ["lrn-start", "С чего начать"],
    ["lrn-flow", "Как идёт ход"],
    ["lrn-tasks", "Что поручить"],
    ["lrn-how", "Как говорить"],
    ["lrn-rhythm", "Между ходами"],
    ["lrn-contracts", "Договорённости"],
    ["lrn-economy", "Экономно"],
    ["lrn-limits", "Границы"],
  ];
  return `<nav class="lrn-toc" aria-label="Разделы знакомства">
    ${items.map(([id, label]) => `<button type="button" data-jump="${id}">${esc(label)}</button>`).join("")}
  </nav>`;
}

function firstSteps(): string {
  const st = S.agentState;
  const telegram = LOCAL_AGENT && !!st && !st.telegram?.enabled;
  const steps = [
    `<li class="lrn-step">
      <span class="lrn-step-num">1</span>
      <div class="lrn-step-body">
        <b>Представься</b>
        <p>Расскажи, кто ты и что тебе важно, — агент запомнит это о тебе.</p>
        <div class="frame-line" data-card="intro">${sentence(INTRO, "intro")}</div>
        <button class="btn btn-primary lrn-take" type="button" data-take="intro">${takeLabel()} <span aria-hidden="true">→</span></button>
      </div>
    </li>`,
    `<li class="lrn-step">
      <span class="lrn-step-num">2</span>
      <div class="lrn-step-body">
        <b>Поручи первое дело</b>
        <p>Выбери рамку ниже — пропуски заполняются прямо в карточке, отправляешь ты сам.</p>
        <button class="btn btn-quiet" type="button" data-jump="lrn-tasks">К рамкам задач <span aria-hidden="true">↓</span></button>
      </div>
    </li>`,
    `<li class="lrn-step">
      <span class="lrn-step-num">3</span>
      <div class="lrn-step-body">
        <b>Посмотри, как он думал</b>
        <p>После первого хода открой «Контекст»: там по слоям видно, из чего агент собирал кадр этого хода.</p>
        <button class="btn btn-quiet" type="button" data-go="frame">Открыть «Контекст» <span aria-hidden="true">→</span></button>
      </div>
    </li>`,
  ];
  if (telegram) {
    steps.push(`<li class="lrn-step">
      <span class="lrn-step-num">4</span>
      <div class="lrn-step-body">
        <b>Позови его в Telegram</b>
        <p>Тогда говорить с агентом можно и с телефона — в личке или в группе.</p>
        <button class="btn btn-quiet" type="button" data-go="settings">В настройки <span aria-hidden="true">→</span></button>
      </div>
    </li>`);
  }
  return `<ol class="lrn-steps">${steps.join("")}</ol>`;
}

/** Читает ли агент это окно. Окно к серверу (её харнесс) записок окна не читает:
 *  звать туда «В поле ввода» — обещать ответ, которого не будет (ревью 26.09, W4 S5). */
function windowIsRead(): boolean {
  return !(foreignHarness() || S.agentState?.runner?.ever === false);
}

function takeLabel(): string {
  return windowIsRead() ? "В поле ввода" : "Скопировать рамку";
}

function taskCard(t: Task): string {
  const badges = [
    t.local ? '<span class="task-badge">на этом компьютере</span>' : "",
    t.computer ? '<span class="task-badge">нужны руки компьютера</span>' : "",
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

// Живое между перерисовками: полёт по схеме и наблюдатели проявления и оглавления.
let stopPlay: () => void = () => {};
let revealer: IntersectionObserver | null = null;
let spy: IntersectionObserver | null = null;

export async function render(container: HTMLElement): Promise<void> {
  stopPlay();
  revealer?.disconnect();
  revealer = null;
  spy?.disconnect();
  spy = null;

  const local = LOCAL_AGENT;
  const tasks = TASKS.filter((t) => local || !t.local);
  const cats = CATS.filter((c) => tasks.some((t) => t.cat === c.id));
  const list = stages();
  const motion = matchMedia("(prefers-reduced-motion: no-preference)").matches;

  container.innerHTML = `<div class="learn${motion && "IntersectionObserver" in window ? " reveal-on" : ""}">
    ${hero()}
    ${toc()}

    <section class="lrn-sec reveal" id="lrn-start">
      <p class="lrn-kicker">С чего начать</p>
      <h3 class="lrn-h">Три шага, и вы знакомы</h3>
      ${firstSteps()}
    </section>

    <section class="lrn-sec reveal" id="lrn-flow">
      <p class="lrn-kicker">Как идёт ход</p>
      <div class="lrn-h-row">
        <h3 class="lrn-h">От твоего слова до его ответа</h3>
        <button class="btn btn-quiet flow-play" type="button" aria-pressed="false">
          <svg class="lrn-ico" viewBox="0 0 20 20" aria-hidden="true"><path class="ico-play" d="M7 5.2v9.6L15 10Z"/></svg>
          <span>Проиграть ход</span>
        </button>
      </div>
      <div class="flow">
        <div class="flow-canvas">${flowSvg(list)}${flowList(list)}</div>
        <div class="flow-detail" aria-live="polite">${detailHTML(undefined)}</div>
      </div>
    </section>

    <section class="lrn-sec reveal" id="lrn-tasks">
      <p class="lrn-kicker">Что поручить</p>
      <div class="lrn-h-row">
        <h3 class="lrn-h">Рамки задач <span class="lrn-count">${tasks.length}</span></h3>
        <div class="lrn-chips" role="group" aria-label="Какие задачи показать">
          <button type="button" class="lrn-chip" data-cat="all" aria-pressed="true">Все</button>
          ${cats.map((c) => `<button type="button" class="lrn-chip" data-cat="${c.id}" aria-pressed="false">${esc(c.label)}</button>`).join("")}
        </div>
      </div>
      <p class="lrn-sub">Впиши своё прямо в пропуски и нажми «В поле ввода»: рамка уедет в разговор с агентом, а отправишь её ты.
        Пустой пропуск останется рамкой — его можно дописать и там.</p>
      <div class="tasks">${tasks.map(taskCard).join("")}</div>
    </section>

    <section class="lrn-sec reveal" id="lrn-how">
      <p class="lrn-kicker">Как говорить</p>
      <h3 class="lrn-h">Четыре вещи, которые полезно знать</h3>
      <ol class="notes">
        ${HOW.map(([h, p]) => `<li class="note"><b>${esc(h)}</b><p>${esc(p)}</p></li>`).join("")}
      </ol>
    </section>

    <section class="lrn-sec reveal" id="lrn-rhythm">
      <p class="lrn-kicker">Между ходами</p>
      <h3 class="lrn-h">Он живёт и тогда, когда ты не пишешь</h3>
      ${dayStrip()}
      <div class="rhythms">
        ${rhythms()
          .map(
            (r) => `<div class="rhythm">
              <span class="task-ico">${svgIcon(r.icon)}</span>
              <b>${esc(r.title)}</b>
              <p>${esc(r.body)}</p>
              <button class="flow-go" type="button" data-go="${r.go.view}">${esc(r.go.label)} <span aria-hidden="true">→</span></button>
            </div>`,
          )
          .join("")}
      </div>
    </section>

    <section class="lrn-sec reveal" id="lrn-contracts">
      <p class="lrn-kicker">Договорённости</p>
      <h3 class="lrn-h">Правила, которые он держит, — и что они дают тебе</h3>
      <div class="lrn-contracts">
        ${CONTRACTS.filter((c) => LOCAL_AGENT || !c.local).map(
          (c) => `<article class="lrn-contract">
            <b>${esc(c.rule)}</b>
            <p>${esc(c.body + (LOCAL_AGENT && c.hereBody ? c.hereBody : ""))}</p>
            <p class="lrn-plus"><span>плюс</span>${esc(c.plus)}</p>
          </article>`,
        ).join("")}
      </div>
    </section>

    <section class="lrn-sec reveal" id="lrn-economy">
      <p class="lrn-kicker">Экономно</p>
      <h3 class="lrn-h">Куда уходят деньги и как тратить меньше</h3>
      <ol class="lrn-econ">
        ${economy()
          .map(
            (e) => `<li class="lrn-econ-row">
              <div>
                <b>${esc(e.title)}</b>
                <p>${esc(e.body)}</p>
              </div>
              ${e.go ? `<button class="flow-go" type="button" data-go="${e.go.view}">${esc(e.go.label)} <span aria-hidden="true">→</span></button>` : ""}
            </li>`,
          )
          .join("")}
      </ol>
    </section>

    <section class="lrn-sec reveal" id="lrn-limits">
      <p class="lrn-kicker">Честно о границах</p>
      <h3 class="lrn-h">Что сегодня неудобно — и что с этим делать</h3>
      <ul class="lrn-limits">
        ${limits()
          .map(
            (l) => `<li class="lrn-limit">
              <p class="lrn-limit-what">${esc(l.what)}</p>
              <p class="lrn-limit-todo"><span>что делать</span>${esc(l.todo)}</p>
            </li>`,
          )
          .join("")}
      </ul>
      <p class="lrn-foot">Этот раздел открывается из любого места: <kbd>Ctrl</kbd> + <kbd>9</kbd>.</p>
    </section>
  </div>`;

  const root = container.querySelector<HTMLElement>(".learn");
  if (!root) return;
  const byId = new Map(list.map((s) => [s.id, s]));
  const detail = root.querySelector<HTMLElement>(".flow-detail");
  const playBtn = root.querySelector<HTMLButtonElement>(".flow-play");

  // ---- проявление разделов при прокрутке (только когда движение разрешено)
  if (root.classList.contains("reveal-on")) {
    revealer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add("is-in");
            revealer?.unobserve(e.target);
          }
        }
      },
      { threshold: 0.08 },
    );
    for (const sec of root.querySelectorAll(".reveal")) revealer.observe(sec);
  }

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
    if (label) label.textContent = on ? "Остановить" : "Проиграть ход";
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
      const tpl = key === "intro" ? INTRO : TASKS.find((t) => t.id === key)?.template;
      if (!tpl) return;
      // Раздел не пишет в поле сам: он просит каркас открыть переписку с агентом
      // ЭТОГО окна и положить туда текст. Каркас знает и про черновик, который
      // нельзя затирать, и про то, в какую комнату это класть.
      const text = assemble(tpl, root, key);
      if (!windowIsRead()) {
        // Окно к серверу агент не читает: рамка уходит в буфер — отправить её туда, где он
        // слышит (Telegram), а не в поле, откуда записка ляжет в дерево без читателя.
        void copyText(text).then((ok) => {
          take.textContent = ok ? "Скопировано — отправь агенту в Telegram"
            : "Не скопировалось — выдели текст рамки вручную";
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
}
