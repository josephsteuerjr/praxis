import { t as uiText } from "../../../../lang/lang";
// Сцена «Для кого поставить» (1.2, 27.09): «для меня» или «для всех».
//
// До 1.2 этот вопрос задавала серая страница установщика NSIS («визард позорный» —
// слово владельца). Теперь он здесь, в лице продукта: две карточки, папка под выбранной,
// и честно — что будет с правами. «Для всех» — Program Files и один запрос прав
// администратора на всю установку (мастер перезапускает себя исполнителем с правами);
// дальше без вопросов.
//
// Обновление стоящей установки сюда не заходит: у неё режим и папка уже решены, и
// раскладку мастер сам не меняет (требование 7).
import { FormScene } from "./base";
import { el, explain } from "./form";
import { isMac, machine, setup } from "../setup";
import { PRODUCT_NAME, isPraxis } from "../config";

type Scope = "user" | "machine";

const CARDS: Record<Scope, { title: string; text: string; note: string }> = {
  user: {
    get title() { return uiText("ui.setup.ui.src.scenes.where.b0a9e1d087"); },
    get text() { return uiText("ui.setup.ui.src.scenes.where.3f62b27e44"); },
    get note() { return uiText("ui.setup.ui.src.scenes.where.28134b7aef"); },
  },
  machine: {
    get title() { return uiText("ui.setup.ui.src.scenes.where.a9079cc0bf"); },
    get text() { return uiText("ui.setup.ui.src.scenes.where.54164dd2fa"); },
    get note() { return uiText("ui.setup.ui.src.scenes.where.20089bf603"); },
  },
};

export class WhereScene extends FormScene {
  private cards = new Map<Scope, HTMLButtonElement>();
  private path: HTMLElement;
  private fixed: HTMLElement;
  private row: HTMLElement;

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.where.505fe5adc2")));
    const lead = el(
      "p",
      "form-lead",
      isPraxis()
        ? uiText("ui.setup.ui.src.scenes.where.bebfe676ba")
        : uiText("ui.setup.ui.src.scenes.where.6e2845636b"),
    );
    this.row = el("div", "modes where-cards");
    this.row.setAttribute("role", "radiogroup");
    for (const scope of ["user", "machine"] as Scope[]) {
      const c = CARDS[scope];
      const card = el("button", "mode-card");
      card.type = "button";
      card.setAttribute("role", "radio");
      card.setAttribute("data-control", "");
      const text = isPraxis()
        ? scope === "machine"
          ? uiText("ui.setup.ui.src.scenes.where.367932715c")
          : uiText("ui.setup.ui.src.scenes.where.ea58ad6835")
        : c.text;
      card.append(el("span", "mode-title", c.title), el("p", "mode-text", text), el("p", "mode-note", c.note));
      card.addEventListener("click", () => this.choose(scope));
      this.cards.set(scope, card);
      this.row.append(card);
    }
    this.path = el("p", "where-path");
    this.fixed = el("p", "form-lead where-fixed");
    this.fixed.hidden = true;
    const notes = el("div", "explain-row");
    notes.append(
      isPraxis()
        ? explain(
            uiText("ui.setup.ui.src.scenes.where.43a068e5e1"),
            uiText("ui.setup.ui.src.scenes.where.b91328759b"),
          )
        : explain(
            uiText("ui.setup.ui.src.scenes.where.10a3388094"),
            uiText("ui.setup.ui.src.scenes.where.1339dd7f0c"),
          ),
      explain(
        uiText("ui.setup.ui.src.scenes.where.1de06a2c83"),
        uiText("ui.setup.ui.src.scenes.where.87a2d5707b"),
      ),
    );
    this.mount(head, lead, this.row, this.path, this.fixed, notes);
  }

  private dirFor(scope: Scope): string {
    return scope === "machine" ? machine.machineDir : machine.userDir;
  }

  private choose(scope: Scope) {
    setup.scope = scope;
    setup.dir = this.dirFor(scope) || setup.dir;
    for (const [s, card] of this.cards) card.setAttribute("aria-checked", String(s === scope));
    this.path.textContent = setup.dir ? uiText("ui.setup.ui.src.scenes.where.5e16148938", {p0: String(setup.dir)}) : "";
  }

  protected beforeEnter(): void {
    // Мастер «на месте» или поверх стоящей установки: папка и режим уже решены.
    const inst = machine.installedFound;
    const fixedDir = machine.inPlace ? setup.dir : inst?.dir ?? "";
    if (fixedDir) {
      this.row.hidden = true;
      this.path.hidden = true;
      this.fixed.hidden = false;
      const mode = (inst?.scope || setup.scope) === "machine" ? uiText("ui.setup.ui.src.scenes.where.07b10af87d") : uiText("ui.setup.ui.src.scenes.where.27719febc2");
      this.fixed.textContent = uiText("ui.setup.ui.src.scenes.where.38f40b70f6", {p0: String(PRODUCT_NAME), p1: String(mode), p2: String(fixedDir), p3: String(isPraxis() ? uiText("ui.setup.ui.src.scenes.where.88dc3b07b0") : uiText("ui.setup.ui.src.scenes.where.348c99417c"))});
      setup.dir = fixedDir;
      return;
    }
    this.row.hidden = false;
    this.path.hidden = false;
    this.fixed.hidden = true;
    // На macOS «для всех» нет: программа живёт в ~/Applications владельца.
    this.cards.get("machine")!.hidden = isMac();
    const scope: Scope = setup.scope === "machine" && !isMac() ? "machine" : "user";
    this.choose(scope);
  }
}
