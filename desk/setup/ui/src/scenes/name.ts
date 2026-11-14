import { t as uiText } from "../../../../lang/lang";
// Сцена «Имя»: как назвать агента и как зовут владельца.
import { FormScene } from "./base";
import { el, explain, field } from "./form";
import { setup } from "../setup";

export class NameScene extends FormScene {
  private agentInput!: HTMLInputElement;

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.name.5b7ce8ea26")));
    const grid = el("div", "form-grid two");
    // Подпись: имя агента его же почерком — так оно встанет над его комнатой в
    // окне. Живой предпросмотр, пока печатаешь: имя видно раньше, чем принято.
    const signature = el("p", "signature");
    const syncSignature = () => {
      const name = setup.agent.trim();
      signature.textContent = name || "…";
      signature.classList.toggle("empty", !name);
    };
    const agentField = field({
      label: uiText("ui.setup.ui.src.scenes.name.ba527ef131"),
      value: setup.agent,
      placeholder: uiText("ui.setup.ui.src.scenes.name.14b2af1076"),
      onInput: (v) => {
        setup.agent = v;
        syncSignature();
      },
    });
    syncSignature();
    const ownerField = field({
      label: uiText("ui.setup.ui.src.scenes.name.1a399e1b3a"),
      value: setup.owner,
      placeholder: uiText("ui.setup.ui.src.scenes.name.320ddc73a2"),
      onInput: (v) => (setup.owner = v),
    });
    grid.append(agentField, ownerField);
    this.agentInput = agentField.querySelector("input")!;
    const notes = el("div", "explain-row");
    notes.append(
      explain(
        uiText("ui.setup.ui.src.scenes.name.38125fad9c"),
        uiText("ui.setup.ui.src.scenes.name.aeefef8ac1"),
      ),
      explain(
        uiText("ui.setup.ui.src.scenes.name.78572ef3b4"),
        uiText("ui.setup.ui.src.scenes.name.061c3ecd57"),
      ),
    );
    this.mount(head, grid, signature, notes);
  }

  protected onEntered() {
    if (!setup.agent) this.agentInput.focus({ preventScroll: true });
  }

  validate(): string | null {
    if (!setup.agent.trim()) return uiText("ui.setup.ui.src.scenes.name.5413f95923");
    if (!setup.owner.trim()) return uiText("ui.setup.ui.src.scenes.name.299ac3dc78");
    return null;
  }
}
