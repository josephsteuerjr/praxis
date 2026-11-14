import { t as uiText, locale } from "../../../../lang/lang";
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


/** `2026-09-27` → «27 сентября» (год — только если не нынешний). */
export function humanDay(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return "";
  const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
  const date = new Date(Date.UTC(y, mo - 1, d, 12));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return "";
  return new Intl.DateTimeFormat(locale(), {day:"numeric",month:"long",timeZone:"UTC",
    ...(y !== new Date().getFullYear() ? {year:"numeric"} : {})}).format(date);
}

/** Что это за находка — словами, а не видом записи. */
function kindWords(f: Found): string {
  const ver = f.version ? uiText("ui.setup.ui.src.scenes.found.62e3d3a93e", {p0: String(f.version)}) : "";
  switch (f.kind) {
    case "backup":
      return uiText("ui.setup.ui.src.scenes.found.e18cc57ed5", {p0: String(ver)});
    case "leftover":
      return uiText("ui.setup.ui.src.scenes.found.f6f84b3b1b", {p0: String(ver)});
    case "unconfigured":
      return uiText("ui.setup.ui.src.scenes.found.223375d98b", {p0: String(ver)});
    default:
      return uiText("ui.setup.ui.src.scenes.found.a0c6a633bf", {p0: String(ver)});
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
    this.go = button(uiText("ui.setup.ui.src.scenes.found.a42aee58d6"), "primary", () => {
      if (this.picked) this.actions?.resume(this.picked);
    });
    buttons.append(this.go, button(uiText("ui.setup.ui.src.scenes.found.9a885083e0"), "quiet", () => this.actions?.fresh()));
    const why = el("div", "explain-row");
    why.append(
      explain(
        uiText("ui.setup.ui.src.scenes.found.a42aee58d6"),
        uiText("ui.setup.ui.src.scenes.found.565670d875"),
      ),
      explain(
        uiText("ui.setup.ui.src.scenes.found.9a885083e0"),
        uiText("ui.setup.ui.src.scenes.found.99bb274b39"),
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
    this.go.textContent = resumable(machine.found).length > 1 ? uiText("ui.setup.ui.src.scenes.found.1fc37a2ba1") : uiText("ui.setup.ui.src.scenes.found.a42aee58d6");
  }

  protected beforeEnter(): void {
    const items = resumable(machine.found);
    const one = items.length === 1 ? items[0] : null;
    // Без глагола прошедшего времени: род агента по имени не угадывается.
    this.head.replaceChildren(el("span", "line", items.length > 1 ? uiText("ui.setup.ui.src.scenes.found.ebe2f460e9") : uiText("ui.setup.ui.src.scenes.found.8ce5bfeb7b")));
    this.lead.textContent = one
      ? uiText("ui.setup.ui.src.scenes.found.6d003a6d5d")
      : uiText("ui.setup.ui.src.scenes.found.2fa89b630e");
    this.list.replaceChildren();
    for (const f of items) {
      const card = el("button", "found-card");
      card.type = "button";
      card.setAttribute("role", "radio");
      card.setAttribute("data-control", "");
      card.dataset.dir = f.dir;
      const name = el("span", "found-name", f.agent || uiText("ui.setup.ui.src.scenes.found.0106f3ae25"));
      const meta: string[] = [];
      if (f.owner) meta.push(uiText("ui.setup.ui.src.scenes.found.a6997af3b7", {p0: String(f.owner)}));
      const day = humanDay(f.last);
      if (day) meta.push(uiText("ui.setup.ui.src.scenes.found.faaf39dfbc", {p0: String(day)}));
      if (f.data_mb > 0) meta.push(uiText("ui.setup.ui.src.scenes.found.11d7dabe03", {p0: new Intl.NumberFormat(locale(), {maximumFractionDigits:1}).format(f.data_mb)}));
      if (f.agents.length) meta.push(uiText("ui.setup.ui.src.scenes.found.309fa8bfda", {p0: String(f.agents.join(", "))}));
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
