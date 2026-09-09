// Сцена «Конституция»: канонический текст с именами, правится и принимается.
import { FormScene } from "./base";
import { el, explain, toggle } from "./form";
import { constitutionFor, setup } from "../setup";

export class ConstitutionScene extends FormScene {
  private head: HTMLElement;
  private editor: HTMLTextAreaElement;
  private touched = false;
  private accept: HTMLElement;

  constructor(root: HTMLElement) {
    super(root);
    this.head = el("h2", "form-head");
    const row = el("div", "const-row");
    const aside = el("div", "const-aside");
    aside.append(
      explain(
        "Это корень агента",
        "Кто он, зачем, как устроен, чего не делает и кому верен. Всё остальное в нём может меняться само; этот текст — только вместе с тобой.",
      ),
      explain(
        "Прочитай и поправь",
        "Текст канонический, но твой. Если что-то не про твоего агента, перепиши прямо здесь: он примет это как своё с первого запуска.",
      ),
    );
    this.editor = el("textarea", "const-editor");
    this.editor.setAttribute("data-control", "");
    this.editor.spellcheck = false;
    this.editor.addEventListener("input", () => {
      this.touched = true;
      setup.constitution = this.editor.value;
    });
    this.accept = toggle({
      label: "Принимаю эту конституцию",
      value: setup.accepted,
      onChange: (v) => (setup.accepted = v),
    });
    aside.append(this.accept);
    row.append(aside, this.editor);
    this.mount(this.head, row);
  }

  protected beforeEnter() {
    // Имя не склоняем кодом: заголовок без него, имя живёт в самом тексте.
    this.head.replaceChildren(el("span", "line", "Конституция"));
    if (!this.touched) {
      setup.constitution = constitutionFor(setup.agent, setup.owner);
      this.editor.value = setup.constitution;
    }
  }

  validate(): string | null {
    if (!setup.constitution.trim()) return "Конституция не может быть пустой";
    if (!setup.accepted) return "Сначала прими конституцию: переключатель слева";
    return null;
  }
}
