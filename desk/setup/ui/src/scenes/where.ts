// Сцена «Для кого поставить» (1.2, 27.09): «для меня» или «для всех».
//
// До 1.2 этот вопрос задавала серая страница установщика NSIS («визард позорный» —
// слово Егора). Теперь он здесь, в лице продукта: две карточки, папка под выбранной,
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
    title: "Только для меня",
    text: "Программа ляжет в твою папку программ. Агент живёт в твоей учётной записи — с твоими файлами и правами, не больше.",
    note: "Прав администратора не нужно.",
  },
  machine: {
    title: "Для всех на этом компьютере",
    text: "Программа ляжет в Program Files, ярлыки увидят все учётные записи. Память агента одна — у компьютера. Вместе со службой агент живёт и без входа в систему.",
    note: "Windows один раз спросит права администратора — на всю установку сразу.",
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
    head.append(el("span", "line", "Для кого поставить?"));
    const lead = el(
      "p",
      "form-lead",
      isPraxis()
        ? "Окно к агенту на твоём сервере. Вопрос — кому его поставить: тебе или всем учётным записям этого компьютера."
        : "Программа одна, агент один. Вопрос — где им жить: в твоей учётной записи или у всего компьютера.",
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
          ? "Программа ляжет в Program Files, ярлыки увидят все учётные записи; настройки подключения — общие."
          : "Программа ляжет в твою папку программ; настройки подключения — только твои."
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
            "Где будут настройки",
            "Рядом с программой, в helene.json: адрес сервера и ключ канала. Их спросит само окно при первом запуске; обновление их не трогает.",
          )
        : explain(
            "Где будет память",
            "Рядом с программой, в папке data: память, дневник, конституция, ключи. Перед каждым обновлением установщик снимает с неё копию в backups.",
          ),
      explain(
        "Передумать потом",
        "Переезд между «для меня» и «для всех» — только с копией и только по твоему слову: сама установка раскладку не меняет.",
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
    this.path.textContent = setup.dir ? `Папка: ${setup.dir}` : "";
  }

  protected beforeEnter(): void {
    // Мастер «на месте» или поверх стоящей установки: папка и режим уже решены.
    const inst = machine.installedFound;
    const fixedDir = machine.inPlace ? setup.dir : inst?.dir ?? "";
    if (fixedDir) {
      this.row.hidden = true;
      this.path.hidden = true;
      this.fixed.hidden = false;
      const mode = (inst?.scope || setup.scope) === "machine" ? "для всех" : "для меня";
      this.fixed.textContent = `${PRODUCT_NAME} уже стоит здесь (${mode}): ${fixedDir}. Установка ляжет туда же — ${isPraxis() ? "настройки подключения" : "память и настройки"} останутся.`;
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
