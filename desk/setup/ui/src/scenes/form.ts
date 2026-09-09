// Строительные блоки сцен с вводом: поле, объяснение, карточка-выбор,
// переключатель, кнопка. Только настоящие контролы интерактивны — и только
// они помечены data-control, чтобы четверти навигации их не перехватывали.

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = "",
  text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

export interface FieldOptions {
  label: string;
  value?: string;
  placeholder?: string;
  hint?: string;
  type?: "text" | "password" | "url";
  mono?: boolean;
  onInput: (value: string) => void;
}

export function field(o: FieldOptions): HTMLElement {
  const wrap = el("label", "field");
  const cap = el("span", "field-label", o.label);
  const input = el("input", "field-input" + (o.mono ? " mono" : ""));
  input.type = o.type ?? "text";
  input.value = o.value ?? "";
  input.placeholder = o.placeholder ?? "";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.setAttribute("data-control", "");
  input.addEventListener("input", () => o.onInput(input.value));
  wrap.append(cap, input);
  if (o.hint) wrap.append(el("span", "field-hint", o.hint));
  return wrap;
}

export function explain(title: string, text: string): HTMLElement {
  const box = el("div", "explain");
  if (title) box.append(el("h3", "", title));
  box.append(el("p", "", text));
  return box;
}

export interface ChoiceOptions<V extends string> {
  items: Array<{ value: V; title: string; text: string }>;
  value: V;
  onChange: (value: V) => void;
}

/** Ряд карточек-выборов (radio без формы). */
export function choice<V extends string>(o: ChoiceOptions<V>): HTMLElement {
  const row = el("div", "choice");
  row.setAttribute("role", "radiogroup");
  const buttons: HTMLButtonElement[] = [];
  const sync = (value: V) => {
    for (const b of buttons) b.setAttribute("aria-checked", String(b.dataset.value === value));
  };
  for (const item of o.items) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.setAttribute("data-control", "");
    b.dataset.value = item.value;
    b.append(el("span", "choice-title", item.title), el("span", "choice-text", item.text));
    b.addEventListener("click", () => {
      sync(item.value);
      o.onChange(item.value);
    });
    buttons.push(b);
    row.append(b);
  }
  sync(o.value);
  return row;
}

export interface SwitchOptions {
  label: string;
  value: boolean;
  onChange: (value: boolean) => void;
}

/** Переключатель. Возвращаем именно HTMLButtonElement, а не HTMLElement:
 *  сцене режима нужно и погасить его (`disabled`, когда прав администратора
 *  нет), и переставить значение снаружи (`aria-checked`) — вслед за тем, что
 *  сцена уже поправила в состоянии установки. */
export function toggle(o: SwitchOptions): HTMLButtonElement {
  const b = el("button", "switch");
  b.type = "button";
  b.setAttribute("role", "switch");
  b.setAttribute("data-control", "");
  b.setAttribute("aria-checked", String(o.value));
  b.append(el("span", "switch-knob"), el("span", "switch-label", o.label));
  b.addEventListener("click", () => {
    const next = b.getAttribute("aria-checked") !== "true";
    b.setAttribute("aria-checked", String(next));
    o.onChange(next);
  });
  return b;
}

export function button(text: string, kind: "primary" | "quiet", onClick: () => void): HTMLButtonElement {
  const b = el("button", `btn btn-${kind}`, text);
  b.type = "button";
  b.setAttribute("data-control", "");
  b.addEventListener("click", onClick);
  return b;
}
