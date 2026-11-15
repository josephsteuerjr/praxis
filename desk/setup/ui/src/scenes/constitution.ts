import { t as uiText } from "../../../../lang/lang";
// Сцена «Конституция»: канонический текст с именами, правится и принимается.
import { FormScene } from "./base";
import { el, explain, toggle } from "./form";
import { constitutionFor, setup } from "../setup";

export class ConstitutionScene extends FormScene {
  private head: HTMLElement;
  private editor: HTMLTextAreaElement;
  private touched = false;
  private generated: string | null = null;
  private accept: HTMLElement;

  constructor(root: HTMLElement) {
    super(root);
    this.head = el("h2", "form-head");
    const row = el("div", "const-row");
    const aside = el("div", "const-aside");
    aside.append(
      explain(
        uiText("ui.setup.ui.src.scenes.constitution.1b250082f1"),
        uiText("ui.setup.ui.src.scenes.constitution.103b78f8b6"),
      ),
      explain(
        uiText("ui.setup.ui.src.scenes.constitution.1f28e45f6c"),
        uiText("ui.setup.ui.src.scenes.constitution.e397d7097d"),
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
      label: uiText("ui.setup.ui.src.scenes.constitution.527e5509ad"),
      value: setup.accepted,
      onChange: (v) => (setup.accepted = v),
    });
    aside.append(this.accept);
    row.append(aside, this.editor);
    this.mount(this.head, row);
  }

  protected beforeEnter() {
    // Имя не склоняем кодом: заголовок без него, имя живёт в самом тексте.
    this.head.replaceChildren(el("span", "line", uiText("ui.setup.ui.src.scenes.constitution.8a9346b6c0")));
    if (!this.touched && (this.generated === null ? !setup.constitution.trim() : setup.constitution === this.generated)) {
      setup.constitution = constitutionFor(setup.agent, setup.owner);
      this.generated = setup.constitution;
    }
    this.editor.value = setup.constitution;
  }

  validate(): string | null {
    if (!setup.constitution.trim()) return uiText("ui.setup.ui.src.scenes.constitution.943ccbda92");
    if (!setup.accepted) return uiText("ui.setup.ui.src.scenes.constitution.060a5b161a");
    return null;
  }
}
