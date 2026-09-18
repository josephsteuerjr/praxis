// Форм-кит — одна копия DOM-обёрток на окно, установщик, телефон и мини-апп.
// Раньше `toggle` жил в четырёх файлах, `button` — в пяти (ревью 06.09, §6).
// Классы те же, что стояли в каждой копии (`field`, `switch`, `btn`, `choice`,
// `card`, `model-chip`), стили — у каждого интерфейса свои, по общим токенам.

import { esc, humanError } from "./text";

export function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export function q<E extends Element = HTMLElement>(sel: string, root: ParentNode = document): E {
  const node = root.querySelector<E>(sel);
  if (!node) throw new Error(`нет элемента ${sel}`);
  return node;
}

/** Кнопка-иконка: svg-путь в viewBox 0 0 20 20, подпись для диктора. */
export function icon(paths: string, label: string, className = "icon-btn"): HTMLButtonElement {
  const b = el("button", className);
  b.type = "button";
  b.setAttribute("aria-label", label);
  b.title = label;
  b.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${paths}</svg>`;
  return b;
}

export interface FieldOptions {
  type?: "text" | "password" | "url" | "number";
  mono?: boolean;
  placeholder?: string;
  hint?: string;
  /** Установщик помечает настоящие контролы, чтобы четверти навигации их не перехватывали. */
  control?: boolean;
}

/** Поле ввода с подписью сверху и подсказкой снизу. Возвращает обёртку `label.field`. */
export function field(label: string, value: string, onInput: (v: string) => void, opts: FieldOptions = {}): HTMLLabelElement {
  const wrap = el("label", "field");
  wrap.append(el("span", "field-label", label));
  const input = el("input", "field-input" + (opts.mono ? " mono" : ""));
  input.type = opts.type || "text";
  input.value = value;
  input.placeholder = opts.placeholder || "";
  input.autocomplete = "off";
  input.spellcheck = false;
  if (opts.control) input.setAttribute("data-control", "");
  input.addEventListener("input", () => onInput(input.value));
  wrap.append(input);
  if (opts.hint) wrap.append(el("span", "field-hint", opts.hint));
  return wrap;
}

/** Подставить значение в поле, собранное `field()` (список моделей, пресеты). */
export function setField(wrap: HTMLElement, value: string): void {
  const input = wrap.querySelector("input");
  if (input) input.value = value;
}

/** Переключатель. HTMLButtonElement, а не HTMLElement: сценам нужно и погасить
 *  его (`disabled`), и переставить значение снаружи (`aria-checked`). */
export function toggle(label: string, value: boolean, onChange: (v: boolean) => void, control = false): HTMLButtonElement {
  const b = el("button", "switch");
  b.type = "button";
  b.setAttribute("role", "switch");
  b.setAttribute("aria-checked", String(value));
  if (control) b.setAttribute("data-control", "");
  b.append(el("span", "switch-knob"), el("span", "switch-label", label));
  b.addEventListener("click", () => {
    if (b.getAttribute("aria-disabled") === "true") return;
    const next = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(next));
    onChange(next);
  });
  return b;
}

export type ButtonKind = "primary" | "quiet" | "ghost" | "danger";

export function button(text: string, kind: ButtonKind, onClick: () => void, control = false): HTMLButtonElement {
  const b = el("button", `btn btn-${kind}`, text);
  b.type = "button";
  if (control) b.setAttribute("data-control", "");
  b.addEventListener("click", onClick);
  return b;
}

export interface ChoiceItem<V extends string> {
  value: V;
  title: string;
  text?: string;
}

export interface Choice<V extends string> {
  el: HTMLElement;
  value(): V;
  set(value: V): void;
}

/** Ряд карточек-выборов (radio без формы). `stack` — столбиком, когда описания длинные. */
export function choice<V extends string>(
  items: Array<ChoiceItem<V>>,
  value: V,
  onChange: (value: V) => void,
  opts: { stack?: boolean; control?: boolean } = {},
): Choice<V> {
  const row = el("div", "choice" + (opts.stack ? " stack" : ""));
  row.setAttribute("role", "radiogroup");
  let current = value;
  const buttons: HTMLButtonElement[] = [];
  const sync = () => {
    for (const b of buttons) b.setAttribute("aria-checked", String(b.dataset.value === current));
  };
  for (const item of items) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    if (opts.control) b.setAttribute("data-control", "");
    b.dataset.value = item.value;
    b.append(el("span", "choice-title", item.title));
    if (item.text) b.append(el("span", "choice-text", item.text));
    b.addEventListener("click", () => {
      if (b.getAttribute("aria-disabled") === "true") return;
      current = item.value;
      sync();
      onChange(item.value);
    });
    buttons.push(b);
    row.append(b);
  }
  sync();
  return {
    el: row,
    value: () => current,
    set: (v) => {
      current = v;
      sync();
    },
  };
}

/** Карточка экрана настроек: заголовок, содержимое, строки-подсказки. */
export function card(title: string, ...children: Array<HTMLElement | string>): HTMLElement {
  const c = el("section", "card");
  if (title) c.append(el("h3", "", title));
  for (const ch of children) c.append(typeof ch === "string" ? el("p", "field-hint", ch) : ch);
  return c;
}

/** Чипы выбора одного значения из списка (модели, ступени усилия). */
export function chips(box: HTMLElement, items: Array<{ value: string; label?: string }>, current: string, pick: (v: string) => void, label = ""): void {
  box.replaceChildren();
  if (!items.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  if (label) box.append(el("span", "models-label", label));
  for (const it of items) {
    const chip = el("button", "model-chip", it.label ?? it.value);
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(it.value === current));
    chip.addEventListener("click", () => {
      pick(it.value);
      for (const other of box.querySelectorAll(".model-chip")) other.setAttribute("aria-pressed", String(other === chip));
    });
    box.append(chip);
  }
}

/** Расписка под кнопкой: текст и цвет исхода одним вызовом. */
export function receipt(node: HTMLElement, text: string, kind: "" | "ok" | "err" = ""): void {
  node.className = "receipt" + (kind ? " " + kind : "");
  node.textContent = text;
}

// ------------------------------------------------------------------ тост

let toastTimer = 0;
let toastHideTimer = 0;
let toastBound = false;

/** Короткое уведомление внизу. Элемент `#toast` создаётся сам, если его нет. */
export function toast(text: string) {
  let box = document.getElementById("toast");
  if (!box) {
    box = el("div", "toast");
    box.id = "toast";
    box.hidden = true;
    document.body.append(box);
  }
  if (!toastBound) {
    toastBound = true;
    // Текст можно выделить и скопировать — значит тост ловит клики и должен
    // уметь уйти с дороги сам. Клик прячет его, но не когда что-то выделяют.
    box.addEventListener("click", () => {
      if ((window.getSelection()?.toString() || "").trim()) return;
      clearTimeout(toastTimer);
      clearTimeout(toastHideTimer);
      box!.classList.remove("show");
      toastHideTimer = window.setTimeout(() => (box!.hidden = true), 300);
    });
  }
  box.textContent = text;
  box.hidden = false;
  box.classList.add("show");
  clearTimeout(toastTimer);
  clearTimeout(toastHideTimer);
  // Время показа от длины: ~58 знаков в секунду, втрое быстрее чтения.
  const ms = Math.min(12000, Math.max(2800, Math.round(text.length * 55)));
  toastTimer = window.setTimeout(() => {
    box!.classList.remove("show");
    toastHideTimer = window.setTimeout(() => (box!.hidden = true), 300);
  }, ms);
}

// ------------------------------------------------------------------ отказ на месте

/** Экран/блок отказа: фраза, «Повторить» и складка с сырым текстом. */
export function failHTML(e: unknown, opts: { retry?: boolean } = {}): string {
  const h = humanError(e);
  const retry = opts.retry === false ? "" : '<button class="btn btn-quiet" data-fail-retry type="button">Повторить</button>';
  const detail = h.detail
    ? `<details class="fail-detail"><summary>Подробности</summary><pre class="mono">${esc(h.detail)}</pre>` +
      '<button class="btn btn-quiet" data-fail-copy type="button">Скопировать</button></details>'
    : "";
  return `<div class="fail"><b>${esc(h.text)}</b><div class="fail-actions">${retry}</div>${detail}</div>`;
}

/** Оживляет кнопки блока отказа: «Повторить» и «Скопировать». */
export function bindFail(root: ParentNode, retry?: () => void) {
  for (const b of root.querySelectorAll<HTMLButtonElement>("[data-fail-retry]")) {
    if (retry) b.addEventListener("click", retry);
    else b.hidden = true;
  }
  for (const b of root.querySelectorAll<HTMLButtonElement>("[data-fail-copy]")) {
    b.addEventListener("click", () => {
      const pre = b.parentElement?.querySelector("pre");
      const text = pre?.textContent || "";
      void copyText(text).then((ok) => toast(ok ? "Скопировано" : "Скопировать не вышло — выдели текст мышью"));
    });
  }
}

/**
 * Скопировать текст в буфер — и там, где `navigator.clipboard` нет.
 *
 * ⚠ `navigator.clipboard` живёт только в secure context. На Windows страница
 * окна и установщика открыта с `http://helene.localhost`, и WebView2 считает
 * его своим; на macOS та же страница живёт на `helene://localhost` — кастомная
 * схема Tauri, которую wry 0.55 secure не объявляет, и `navigator.clipboard`
 * там undefined. Голый вызов на Mac — TypeError по клику, молча. Поэтому
 * сначала пробуем clipboard, а без него — выделение невидимого поля и
 * `execCommand("copy")`: устаревший путь, но работает в любом контексте.
 * -> удалось ли скопировать.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // clipboard есть, но отказал (нет фокуса, нет разрешения) — пробуем запасной путь
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
    document.body.append(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  } catch {
    return false;
  }
}

/**
 * «Выстрелил и забыл» рядом с надписью «читаю…» — всегда баг: промис отвергся,
 * ловца нет, спиннер стоит вечно. Эта обёртка рисует отказ на месте спиннера,
 * и только если узел ещё на экране.
 */
export function safeRender(box: HTMLElement, fn: () => Promise<void>): void {
  void fn().catch((e) => {
    if (!box.isConnected) return;
    box.innerHTML = failHTML(e);
    bindFail(box, () => safeRender(box, fn));
  });
}

// ------------------------------------------------------------------ тема

export type Theme = "system" | "light" | "dark";

/** Тема из хранилища; «system» — ничего не ставим, решает prefers-color-scheme. */
export function readTheme(key = "frame.theme"): Theme {
  try {
    const raw = localStorage.getItem(key);
    if (raw === "system" || raw === "dark" || raw === "light") return raw;
  } catch {
    // без хранилища — как в системе
  }
  return "system";
}

export function applyTheme(mode: Theme): void {
  if (mode === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
}

export function saveTheme(mode: Theme, key = "frame.theme"): void {
  try {
    localStorage.setItem(key, mode);
  } catch {
    // без хранилища тема живёт до перезапуска
  }
  applyTheme(mode);
}
