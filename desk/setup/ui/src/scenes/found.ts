// Сцена «Нашлась память» (1.2, 27.09): программы на машине нет, а память агента
// осталась — после снятия с «оставить данные» или копией, сделанной владельцем.
//
// ⚠ Требование Егора к 1.2: «если при установке найдена память — мастер предлагает
// продолжить с <имя> / выбрать из найденных / начать заново — предложение выбрать
// агента существующего (или агентов, лол)». До 1.2 найденное молча ждало, пока
// человек сам догадается положить папку на место.
//
// Продолжение — КОПИЕЙ: в новую установку уходят `data/` и `helene.json` найденного,
// сама находка не трогается (требование 7: перенос данных — только с копией).
import { FormScene } from "./base";
import { button, el, explain } from "./form";
import { machine, type Found } from "../setup";

export interface FoundActions {
  /** Продолжить с этой памятью: решения из неё, дальше — «для кого» и установка. */
  resume: (f: Found) => void;
  /** Начать заново: найденное остаётся лежать, где лежит. */
  fresh: () => void;
}

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

/** `2026-09-27` → «27 сентября» (год — только если не нынешний). */
export function humanDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return "";
  const [, y, mo, d] = m;
  const month = MONTHS[Number(mo) - 1] ?? "";
  const year = Number(y) !== new Date().getFullYear() ? ` ${y}` : "";
  return `${Number(d)} ${month}${year}`;
}

/** Что это за находка — словами, а не видом записи. */
function kindWords(f: Found): string {
  const ver = f.version ? `, версия ${f.version}` : "";
  switch (f.kind) {
    case "backup":
      return `твоя копия${ver}`;
    case "leftover":
      return `осталась после удаления программы${ver}`;
    case "unconfigured":
      return `программа стоит, но не настроена${ver}`;
    default:
      return `установлена${ver}`;
  }
}

/** Находки, с которыми можно продолжить: память есть, программы нет. */
export function resumable(found: Found[]): Found[] {
  return found.filter((f) => (f.kind === "leftover" || f.kind === "backup") && (f.agent || f.data_mb > 0));
}

export class FoundScene extends FormScene {
  private head: HTMLElement;
  private lead: HTMLElement;
  private list: HTMLElement;
  private go: HTMLButtonElement;
  private picked: Found | null = null;
  private actions: FoundActions | null = null;

  constructor(root: HTMLElement) {
    super(root);
    this.head = el("h2", "form-head");
    this.lead = el("p", "form-lead");
    this.list = el("div", "found-list");
    this.list.setAttribute("role", "radiogroup");
    const buttons = el("div", "install-actions");
    this.go = button("Продолжить", "primary", () => {
      if (this.picked) this.actions?.resume(this.picked);
    });
    buttons.append(this.go, button("Начать заново", "quiet", () => this.actions?.fresh()));
    const why = el("div", "explain-row");
    why.append(
      explain(
        "Продолжить",
        "Программа встанет и получит эту память: агент вспомнит всё, что знал, — с теми же ключами, " +
          "Telegram и настройками. В установку уходит копия; сама находка остаётся нетронутой.",
      ),
      explain(
        "Начать заново",
        "Обычная первая установка: имя, конституция, модель. Найденное останется лежать, где лежит, — " +
          "к нему можно вернуться следующей установкой.",
      ),
    );
    this.mount(this.head, this.lead, this.list, buttons, why);
  }

  bind(actions: FoundActions) {
    this.actions = actions;
  }

  private pick(f: Found) {
    this.picked = f;
    for (const card of this.list.querySelectorAll<HTMLElement>(".found-card")) {
      card.setAttribute("aria-checked", String(card.dataset.dir === f.dir));
    }
    // Без склонения имени: «с Мира» режет слух, а угадывать падеж по имени нельзя.
    this.go.textContent = resumable(machine.found).length > 1 ? "Продолжить с этой памятью" : "Продолжить";
  }

  protected beforeEnter(): void {
    const items = resumable(machine.found);
    const one = items.length === 1 ? items[0] : null;
    // Без глагола прошедшего времени: род агента по имени не угадывается.
    this.head.replaceChildren(el("span", "line", items.length > 1 ? "Нашлась память агентов" : "Нашлась память агента"));
    this.lead.textContent = one
      ? "Программы на компьютере нет, а память осталась. Продолжим с ней — или начнём заново?"
      : "Программы на компьютере нет, а память — есть, и не в одном месте. Выбери, с кем продолжить, или начни заново.";
    this.list.replaceChildren();
    for (const f of items) {
      const card = el("button", "found-card");
      card.type = "button";
      card.setAttribute("role", "radio");
      card.setAttribute("data-control", "");
      card.dataset.dir = f.dir;
      const name = el("span", "found-name", f.agent || "Без имени");
      const meta: string[] = [];
      if (f.owner) meta.push(`владелец ${f.owner}`);
      const day = humanDay(f.last);
      if (day) meta.push(`последний день — ${day}`);
      if (f.data_mb > 0) meta.push(`${f.data_mb} МБ памяти`);
      if (f.agents.length) meta.push(`рядом: ${f.agents.join(", ")}`);
      card.append(
        name,
        el("span", "found-meta", meta.join(" · ")),
        el("span", "found-kind", kindWords(f)),
        el("span", "found-path", f.dir),
      );
      card.addEventListener("click", () => this.pick(f));
      this.list.append(card);
    }
    const first = this.picked && items.some((f) => f.dir === this.picked?.dir) ? this.picked : items[0];
    if (first) this.pick(first);
  }
}
