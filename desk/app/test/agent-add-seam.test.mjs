// Шов agent_add (фикс-волна 06.10, F1): конституция обязана доезжать ОБЕИМИ
// формами — вложенной soul:{kind,text} (карточка «Агенты») и плоскими
// soulKind/soulText/soulFrom (второй колер). Стенд сторожит ТРУБУ обеими
// сторонами, обе — настоящим кодом:
//
//   1. agentscard.ts (настоящий) уезжает с вложенной soul — и она доходит
//      до runCommand ДОСЛОВНО;
//   2. commands.ts runCommand (настоящий) пересылает аргументы хосту как есть
//      — и вложенную, и плоскую форму; Electron ничего не знает про
//      конституцию и не имеет права её переиначивать;
//   3. плоская форма — легальный вызов того же глагола (соседний колер):
//      она тоже доезжает нетронутой.
//
// Слияние форм (вложенная сильнее плоской) живёт в Rust — merge_soul_forms,
// стенд soul_forms_both_reach_the_seed в main.rs. Здесь проверяется, что
// ни одна труба по дороге форму не теряет и не перекраивает.
//
// Запуск: node app/test/agent-add-seam.test.mjs (из корня desk/ или из app/).

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { stripTypeScriptTypes } from "node:module";

// ---------------------------------------------------------------- DOM-подстава
// Ровно столько DOM, сколько нужно форм-киту — как agents-card.test.mjs.
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
      return this._html;
    },
    set(v) {
      this._html = String(v);
      this.children = []; // реальный innerHTML заменяет содержимое
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

// ------------------------------------------ часть 2: runCommand — труба Electron
// Хост-подстава пишет каждый вызов; Electron-часть НОВАЯ (не из голосового
// стенда): проверяем дословность пересылки runCommand → host.invoke.
const hostCalls = [];
const hostMod = await (async () => {
  const src = readFileSync(new URL("../../electron/src/commands.ts", import.meta.url), "utf8");
  // commands.ts импортирует electron — в стенде его нет; подменяем на
  // минимальную подставу с теми же полями, что использует runCommand.
  const electronStub = `
export const shell = { openExternal: async () => {}, openPath: async () => "", showItemInFolder: () => {} };
export class Notification { static isSupported() { return false; } constructor() {} show() {} }
`;
  const electronURL = "data:text/javascript," + encodeURIComponent(electronStub);
  const fsStub = `export const existsSync = () => false;`;
  const fsURL = "data:text/javascript," + encodeURIComponent(fsStub);
  const body2 = stripTypeScriptTypes(src)
    .replace('"electron"', JSON.stringify(electronURL))
    .replace('"node:fs"', JSON.stringify(fsURL));
  return import("data:text/javascript," + encodeURIComponent(body2));
})();
const ctx = {
  root: "C:\\install",
  version: "1.4.0-test",
  product: "Hélène",
  relaunch() {},
  host: {
    invoke: async (command, args) => {
      hostCalls.push([command, args ? JSON.parse(JSON.stringify(args)) : undefined]);
      return { ok: true };
    },
  },
};

// -------------------------------------------- часть 1: карточка → runCommand → хост
// Мост окна: shell() идёт в runCommand — как в настоящем окне Electron.
const windowCalls = [];
globalThis.window = {
  __HELENE__: {
    shell: "electron",
    platform: "test",
    invoke: async (cmd, args) => {
      windowCalls.push([cmd, args ? JSON.parse(JSON.stringify(args)) : undefined]);
      if (cmd === "agents_list") {
        return {
          agents: [{ id: "main", name: "Hélène", tree: "C:\\d", port: 8094, enabled: true, base: true, conflict: "", raised: true, current: true }],
          current: "main",
        };
      }
      if (cmd === "agent_add") return { id: "mira", name: "Мира", port: 8095, dir: "C:\\install\\agents\\mira" };
      return { ok: true };
    },
  },
  setTimeout: (fn) => 0,
  clearTimeout: () => {},
  getSelection: () => null,
};
const mdDocs = {};
globalThis.fetch = async (u) => {
  const path = decodeURIComponent(String(u).split("path=")[1] || "");
  const doc = mdDocs[path] ?? { text: "" };
  return { ok: !doc.error, json: async () => doc, text: async () => String(doc.error || "") };
};

const tick = () => new Promise((r) => setTimeout(r, 0));
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

const parts = (made) => made.el.children[1].children;
const soulParts = (soulBox) => soulBox.children;
const type = (fieldWrap, text) => {
  const input = fieldWrap.children[1];
  input.value = text;
  input.fire("input");
};

// ----------------------------------------------------------------------------
// 1. Вложенная форма карточки доезжает до хоста дословно.
// ----------------------------------------------------------------------------
{
  mdDocs["soul/SOUL.md"] = { text: "Текст конституции Hélène" };
  const made = agentsCard();
  await tick();
  await tick();
  const [, nameBox, soulBox, add] = parts(made);
  const [, pick] = soulParts(soulBox);
  pick.children[1].click(); // унаследовать
  await tick();
  await tick();
  type(nameBox, "Мира");
  add.children[0].click();
  await tick();
  await tick();
  const called = windowCalls.find(([cmd]) => cmd === "agent_add");
  assert.ok(called, "карточка не позвала agent_add");
  assert.deepEqual(called[1], { name: "Мира", soul: { kind: "inherit", text: "Текст конституции Hélène" } });
}

// ----------------------------------------------------------------------------
// 2. runCommand (настоящий, electron/commands.ts) пересылает вложенную форму
//    дословно: что уехало из окна, то и приехало в хост.
// ----------------------------------------------------------------------------
{
  hostCalls.length = 0;
  await hostMod.runCommand(
    "agent_add",
    { name: "Мира", soul: { kind: "text", text: "Моя конституция" } },
    ctx,
  );
  const [cmd, args] = hostCalls.find(([c]) => c === "agent_add");
  assert.ok(cmd, "runCommand не довёл agent_add до хоста");
  assert.deepEqual(args, { name: "Мира", soul: { kind: "text", text: "Моя конституция" } });
}

// ----------------------------------------------------------------------------
// 3. Плоская форма (второй колер) доезжает так же дословно — camelCase-ключи,
//    как их посылает прямой invoke-вызов.
// ----------------------------------------------------------------------------
{
  hostCalls.length = 0;
  await hostMod.runCommand(
    "agent_add",
    { name: "Мира", soulKind: "inherit", soulText: "Текст", soulFrom: "main" },
    ctx,
  );
  const [, args] = hostCalls.find(([c]) => c === "agent_add");
  assert.deepEqual(args, { name: "Мира", soulKind: "inherit", soulText: "Текст", soulFrom: "main" });
}

// ----------------------------------------------------------------------------
// 4. Полный пролёт «окно → runCommand → хост» на вложенной форме: труба
//    не теряет и не перекраивает конституцию по дороге.
// ----------------------------------------------------------------------------
{
  hostCalls.length = 0;
  const invoke = globalThis.window.__HELENE__.invoke;
  globalThis.window.__HELENE__.invoke = async (cmd, args) => hostMod.runCommand(cmd, args ?? {}, ctx);
  try {
    mdDocs["soul/SOUL.md"] = { text: "Наследство" };
    const made = agentsCard();
    await tick();
    await tick();
    const [, nameBox, soulBox, add] = parts(made);
    const [, pick] = soulParts(soulBox);
    pick.children[1].click();
    await tick();
    await tick();
    type(nameBox, "Втора");
    add.children[0].click();
    await tick();
    await tick();
    const [, args] = hostCalls.find(([c]) => c === "agent_add");
    assert.ok(args, "сквозной пролёт не дошёл до хоста");
    assert.deepEqual(args, { name: "Втора", soul: { kind: "inherit", text: "Наследство" } });
  } finally {
    globalThis.window.__HELENE__.invoke = invoke;
  }
}

console.log("agent-add-seam: OK — вложенная и плоская формы доезжают до хоста дословно");
