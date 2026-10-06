// Карточка «Агенты» 1.4.0: конституция при заведении, дефолт, гашение, удаление.
//
// Что здесь стережётся (по заданию волны B1):
//   * канон — молча: agent_add уходит БЕЗ ключа soul;
//   * «унаследовать»/«свой текст» читают soul/SOUL.md ТЕКУЩЕГО агента через
//     /api/md (живой файл, не копия в окне) и едут как soul:{kind,text};
//   * пустой свой текст не пускает заведение — тост, а не тихая потеря;
//   * строка агента: «Открывать по умолчанию» (agent_default_set),
//     «Погасить»/«Поднять» (agent_enabled_set), «Удалить…» с вводом id
//     (agent_remove) — и НИ у текущего, НИ у корневого кнопки удаления нет;
//   * «Открыть» на снятом агенте остаётся: отказ switch_agent показывается
//     тостом, а не молчанием;
//   * «Данные агента»: вторая моно-строка <tree>/soul/SOUL.md и «Показать
//     файл» (open_path) — здесь по образцу settings-remote: состав экрана
//     читается из исходника, поведение кнопки — тем же гребнем.
//
// Стенд исполняет НАСТОЯЩИЙ agentscard.ts (stripTypeScriptTypes + data-URL,
// как actions/update-card), с подставными document/window/fetch и мостом
// Electron (`window.__HELENE__.invoke`) вместо оболочки — как voice-refresh.
//
// Запуск: node app/test/agents-card.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

// ---------------------------------------------------------------- DOM-подстава
// Ровно столько DOM, сколько нужно форм-киту (ui-kit/dom.ts):createElement,
// append/setAttribute/addEventListener/click, classList, style, value, hidden.
const stubEl = (tag) => {
  const node = {
    tag,
    children: [],
    listeners: {},
    attrs: {},
    dataset: {},
    style: {},
    hidden: false,
    disabled: false,
    value: "",
    textContent: "",
    type: "",
    id: "",
    _html: "",
    get className() {
      return this._cls || "";
    },
    set className(v) {
      this._cls = String(v);
    },
    classList: null,
    append(...cs) {
      for (const c of cs) this.children.push(c);
    },
    setAttribute(k, v) {
      this.attrs[k] = String(v);
    },
    getAttribute(k) {
      return this.attrs[k];
    },
    addEventListener(ev, fn) {
      (this.listeners[ev] ||= []).push(fn);
    },
    click() {
      for (const fn of this.listeners.click || []) fn();
    },
    fire(ev) {
      for (const fn of this.listeners[ev] || []) fn();
    },
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
    focus() {},
  };
  node.classList = {
    add: (...cs) => (node._cls = (node._cls ? node._cls + " " : "") + cs.join(" ")),
    contains: (c) => (" " + (node._cls || "") + " ").includes(" " + c + " "),
  };
  Object.defineProperty(node, "innerHTML", {
    get() {
      return node._html;
    },
    set(v) {
      node._html = String(v);
      node.children = []; // реальный innerHTML заменяет содержимое — и в подставе тоже
    },
  });
  return node;
};

const byId = (root, id) => {
  for (const c of root.children || []) {
    if (c.id === id) return c;
    const deep = byId(c, id);
    if (deep) return deep;
  }
  return null;
};
const body = stubEl("body");
globalThis.document = { createElement: stubEl, body, getElementById: (id) => byId(body, id) };

// ------------------------------------------------------------- оболочка и канал
// Мост Electron — единственный путь shell() в тесте: invoke пишет каждый вызов
// в журнал, отказы эмулируются таблицей errors.
const shellCalls = [];
const shellErrors = {};
let agentsFixture = { agents: [], current: "" };
const timers = new Map();
let timerSerial = 0;
globalThis.window = {
  __HELENE__: {
    shell: "electron",
    platform: "test",
    invoke: async (cmd, args) => {
      shellCalls.push([cmd, args]);
      if (shellErrors[cmd]) throw shellErrors[cmd];
      if (cmd === "agents_list") return agentsFixture;
      return { ok: true };
    },
  },
  // Таймеры — подменные (как voice-refresh): тост живёт в карте, а не в event
  // loop, и не стреляет после конца сценария.
  setTimeout: (fn) => {
    let serial = ++timerSerial;
    timers.set(serial, fn);
    return serial;
  },
  clearTimeout: (id) => timers.delete(id),
  getSelection: () => null,
};

// /api/md: таблица документов; error => !ok, как у канала.
const mdDocs = {};
const fetches = [];
globalThis.fetch = async (u) => {
  const path = decodeURIComponent(String(u).split("path=")[1] || "");
  fetches.push(path);
  const doc = mdDocs[path] ?? { text: "" };
  return { ok: !doc.error, json: async () => doc, text: async () => String(doc.error || "") };
};

const toastText = () => byId(body, "toast")?.textContent || "";
const tick = () => new Promise((r) => setTimeout(r, 0));

// --------------------------------------------------------------- модуль под тест
const kit = new URL("../../ui-kit/", import.meta.url);
const moduleURL = (source) => "data:text/javascript;base64," + Buffer.from(stripTypeScriptTypes(source)).toString("base64");
const read = (u) => readFileSync(u, "utf8");
const textURL = moduleURL(read(new URL("text.ts", kit)));
const domURL = moduleURL(read(new URL("dom.ts", kit)).replace('"./text"', JSON.stringify(textURL)));
const libURL = moduleURL(
  read(new URL("window/lib.ts", kit)).replace('"../text"', JSON.stringify(textURL)).replace('"../dom"', JSON.stringify(domURL)),
);
const apiURL = moduleURL(read(new URL("window/api.ts", kit)));
const cardSource = read(new URL("../src/agentscard.ts", import.meta.url))
  .replace('"../../ui-kit/window/api"', JSON.stringify(apiURL))
  .replace('"../../ui-kit/window/lib"', JSON.stringify(libURL))
  .replace('"../../ui-kit/dom"', JSON.stringify(domURL));
const { agentsCard } = await import(moduleURL(cardSource));

/** Свежая карточка + дожидшись первой отрисовки списка. */
async function freshCard() {
  shellCalls.length = 0; // сценарии независимы: хвост прошлого не отвечает за настоящее
  const made = agentsCard();
  await tick();
  await tick();
  return made;
}
/** Ввод в поле форм-кита: children[1] — input (после подписи). */
const type = (fieldWrap, text) => {
  const input = fieldWrap.children[1];
  input.value = text;
  input.fire("input");
};
const addAgentCall = () => shellCalls.find(([cmd]) => cmd === "agent_add");
const btnByText = (node, text) => node.children.find((c) => c.textContent === text);

// Каркас карточки: card()оборачивает [h3, box]; box.children = [list, nameBox,
// soulBox, add, made].
const parts = (made) => made.el.children[1].children;
const soulParts = (soulBox) => soulBox.children; // [label, pick, textarea, warn, receipt]

// --------------------------------------------------------------------------
// 1. Канон — молча: agent_add без ключа soul.
// --------------------------------------------------------------------------
{
  agentsFixture = { agents: [{ id: "main", name: "Hélène", tree: "C:\\d", port: 8094, enabled: true, base: true, conflict: "", raised: true, current: true }], current: "main" };
  const made = await freshCard();
  const [list, nameBox, soulBox, add] = parts(made);
  type(nameBox, "Мира");
  add.children[0].click();
  await tick();
  await tick();
  assert.ok(addAgentCall(), "agent_add не вызван");
  assert.deepEqual(addAgentCall()[1], { name: "Мира" }, "канон обязан уезжать без ключа soul");
  const [, , area] = soulParts(soulBox);
  assert.equal(area.hidden, true, "редактор при каноне свёрнут");
  assert.equal(list.children.length, 1, "список отрисован");
}

// --------------------------------------------------------------------------
// 2. Наследование: читает soul/SOUL.md текущего, едет как soul:{kind:inherit}.
// --------------------------------------------------------------------------
{
  mdDocs["soul/SOUL.md"] = { text: "Текст конституции Hélène" };
  const made = await freshCard();
  const [, nameBox, soulBox, add] = parts(made);
  const [label, pick, area, warn] = soulParts(soulBox);
  // Имя текущего агента подъехало в подпись опции — наследника видно, от кого.
  assert.equal(pick.children[1].children[0].textContent, "Унаследовать от „Hélène“", "опция не назвала донора");
  pick.children[1].click(); // унаследовать
  await tick();
  await tick();
  assert.deepEqual(fetches, ["soul/SOUL.md"], "текст читается из /api/md, а не из окна");
  assert.equal(area.value, "Текст конституции Hélène");
  assert.equal(area.hidden, false, "редактор раскрыт");
  assert.equal(warn.hidden, false, "предупреждение про имена донора показано");
  type(nameBox, "Мира");
  add.children[0].click();
  await tick();
  await tick();
  assert.deepEqual(addAgentCall()[1], { name: "Мира", soul: { kind: "inherit", text: "Текст конституции Hélène" } });
}

// --------------------------------------------------------------------------
// 3. Свой текст: стартовая точка — тот же канон, kind: custom.
// --------------------------------------------------------------------------
{
  mdDocs["soul/SOUL.md"] = { text: "Канон" };
  const made = await freshCard();
  const [, nameBox, soulBox, add] = parts(made);
  const [, pick, area, warn] = soulParts(soulBox);
  pick.children[2].click(); // свой текст
  await tick();
  await tick();
  assert.equal(area.value, "Канон", "свой текст начинается с канона");
  assert.equal(warn.hidden, true, "предупреждение донора — только про наследование");
  area.value = "Моя конституция";
  area.fire("input");
  // Правленый текст переживает смену режима: труд владельца дороже копии файла.
  // (Проверяем ДО заведения: после успешного «Завести» форма честно чистится.)
  pick.children[1].click(); // назад к наследованию
  await tick();
  await tick();
  assert.equal(area.value, "Моя конституция", "смена режима затёрла правку владельца");
  // и обратно в «свой текст» — правка по-прежнему на месте
  pick.children[2].click();
  await tick();
  await tick();
  assert.equal(area.value, "Моя конституция", "возврат в режим тоже затёр правку");
  type(nameBox, "Втора");
  add.children[0].click();
  await tick();
  await tick();
  assert.deepEqual(addAgentCall()[1], { name: "Втора", soul: { kind: "text", text: "Моя конституция" } });
  // Успешное заведение чистит форму: следующий агент не получит чужой текст.
  assert.equal(area.value, "", "форма не очистилась после заведения");
}

// --------------------------------------------------------------------------
// 4. Пустой свой текст не пускает: тост, agent_add нет.
// --------------------------------------------------------------------------
{
  mdDocs["soul/SOUL.md"] = { text: "" };
  const made = await freshCard();
  const [, nameBox, soulBox, add] = parts(made);
  const [, pick] = soulParts(soulBox);
  pick.children[2].click();
  await tick();
  await tick();
  type(nameBox, "Третья");
  add.children[0].click();
  await tick();
  await tick();
  assert.ok(!addAgentCall(), "пустая своя конституция обязана остановить заведение");
  assert.match(toastText(), /пуст/, "тост не сказал про пустоту");
}

// --------------------------------------------------------------------------
// 5. Строка агента: дефолт, гашение/подъём, удаление с вводом id.
// --------------------------------------------------------------------------
{
  agentsFixture = {
    agents: [
      { id: "main", name: "Hélène", tree: "C:\\d", port: 8094, enabled: true, base: true, conflict: "", raised: true, current: true, default: true },
      { id: "mira", name: "Мира", tree: "C:\\d2", port: 8095, enabled: true, base: false, conflict: "", raised: true, current: false },
      { id: "tihon", name: "Тихон", tree: "C:\\d3", port: 8096, enabled: false, base: false, conflict: "", raised: false, current: false },
    ],
    current: "main",
  };
  const made = await freshCard();
  const [list, , , , ] = parts(made);
  const [mainRow, miraRow, tihonRow] = list.children;

  // Текущий и корневой: без «Открыть» и без «Удалить…».
  const mainActs = mainRow.children[0];
  assert.ok(!btnByText(mainActs, "Открыть"), "у текущего нет кнопки «Открыть»");
  assert.ok(!btnByText(mainActs, "Удалить…"), "у корневого не должно быть удаления");
  assert.equal(mainRow.children.length, 1, "у корневого нет блока удаления вовсе");
  const mainDef = btnByText(mainActs, "Открывается по умолчанию");
  assert.ok(mainDef && mainDef.disabled, "дефолтный отмечен и заперт");
  assert.match(mainRow._html, /открывается по умолчанию/);

  // Сосед: полный набор.
  const miraActs = miraRow.children[0];
  btnByText(miraActs, "Открывать по умолчанию").click();
  await tick();
  assert.deepEqual(shellCalls.find(([c]) => c === "agent_default_set")[1], { id: "mira" });

  btnByText(miraActs, "Погасить").click();
  await tick();
  assert.deepEqual(shellCalls.find(([c]) => c === "agent_enabled_set")[1], { id: "mira", enabled: false });

  // Снятый: «Поднять», а не «Погасить».
  const tihonActs = tihonRow.children[0];
  assert.ok(!btnByText(tihonActs, "Погасить"), "у снятого не «Погасить»");
  btnByText(tihonActs, "Поднять").click();
  await tick();
  assert.deepEqual(shellCalls.find(([c, a]) => c === "agent_enabled_set" && a.id === "tihon")[1], { id: "tihon", enabled: true });

  // Удаление: сначала диалог, id рукой, несовпадение — тост без вызова.
  const kill = miraRow.children[1];
  assert.equal(kill.hidden, true, "диалог удаления свёрнут до нажатия");
  btnByText(miraActs, "Удалить…").click();
  assert.equal(kill.hidden, false);
  const killField = kill.children[1];
  type(killField, "не-тот-id");
  btnByText(kill.children[2], "Удалить навсегда").click();
  await tick();
  assert.ok(!shellCalls.some(([c]) => c === "agent_remove"), "нечёткий id обязан не пустить удаление");
  assert.match(toastText(), /Не совпало/);
  type(killField, "MIRA"); // регистр не должен спасать от опечатки наоборот
  btnByText(kill.children[2], "Удалить навсегда").click();
  await tick();
  assert.deepEqual(shellCalls.find(([c]) => c === "agent_remove")[1], { id: "mira" });
  assert.match(toastText(), /чердак/);
}

// --------------------------------------------------------------------------
// 6. «Открыть» на снятом: отказ switch_agent словами — тостом, не молчанием.
// --------------------------------------------------------------------------
{
  agentsFixture = {
    agents: [
      { id: "main", name: "Hélène", tree: "C:\\d", port: 8094, enabled: true, base: true, conflict: "", raised: true, current: true },
      { id: "tihon", name: "Тихон", tree: "C:\\d3", port: 8096, enabled: false, base: false, conflict: "", raised: false, current: false },
    ],
    current: "main",
  };
  shellErrors.switch_agent = new Error("агент «Тихон» погашен в его настройках");
  const made = await freshCard();
  const [list] = parts(made);
  const tihonRow = list.children[1];
  const open = btnByText(tihonRow.children[0], "Открыть");
  assert.ok(open, "кнопка «Открыть» у снятого агента остаётся");
  open.click();
  await tick();
  await tick();
  assert.match(toastText(), /погашен/, "отказ оболочки не показан владельцу");
  delete shellErrors.switch_agent;
}

// --------------------------------------------------------------------------
// 7. «Данные агента»: конституция — второй строкой, «Показать файл» — open_path.
//    Состав экрана — по образцу settings-remote: чтением исходника.
// --------------------------------------------------------------------------
{
  const src = readFileSync(new URL("../src/settings-agent.ts", import.meta.url), "utf8");
  assert.ok(src.includes('"/soul/SOUL.md"'), "путь конституции не строится в «Данных агента»");
  assert.ok(src.includes('button("Показать файл"'), "нет кнопки «Показать файл»");
  assert.ok(src.includes('{ path: soulPath }'), "«Показать файл» не ведёт на soul/SOUL.md");
  assert.ok(src.includes(".replace(/[\\\\/]+$/, \"\")"), "хвостовой разделитель пути не срезается");
  assert.ok(src.includes('button("Открыть папку"'), "пропала прежняя кнопка дома");
}

console.log("агенты: конституция при заведении, дефолт, гашение и удаление — OK");
