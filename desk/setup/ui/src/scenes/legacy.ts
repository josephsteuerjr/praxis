import { t as uiText } from "../../../../lang/lang";
// Сцена «Прежняя версия»: показывается ТОЛЬКО когда SCM отвечает, что на
// машине живёт служба прежнего поколения этого же продукта (Vera, Frame,
// Praxis) или своя, но из другой папки.
//
// Зачем она есть. Прежнее снятие продукта сносило файлы и НЕ снимало службу:
// на машине автора так и осталась `Vera` — Running, Auto, LocalSystem, exe в
// папке пользователя (кто может писать в эту папку, тот получает права
// системы), а её ребёнок держит порт 5011 — дефолтный порт реле, из-за чего
// мозг новой установки уходил бы в реле прошлого поколения.
//
// Правило сцены: НИЧЕГО не делаем сами. Показываем, что нашли, и снимаем
// только по нажатию кнопки — чужую (пусть и свою прежнюю) службу молча не
// трогают.
import { FormScene } from "./base";
import { button, el, explain } from "./form";
import { legacyServices, removeService, type LegacyService } from "../setup";

/** Состояние службы человеческими словами: SCM отвечает по-английски. */
function stateWord(s: string): string {
  const map: Record<string, string> = {
    Running: uiText("ui.setup.ui.src.scenes.legacy.f33145d545"),
    Stopped: uiText("ui.setup.ui.src.scenes.legacy.aa40868466"),
    Paused: uiText("ui.setup.ui.src.scenes.legacy.4a92a58648"),
    StartPending: uiText("ui.setup.ui.src.scenes.legacy.4c9118412a"),
    StopPending: uiText("ui.setup.ui.src.scenes.legacy.e3d986243c"),
  };
  return map[s] ?? s.toLowerCase();
}

function startWord(s: string): string {
  const map: Record<string, string> = {
    Auto: uiText("ui.setup.ui.src.scenes.legacy.5c8370728c"),
    Manual: uiText("ui.setup.ui.src.scenes.legacy.40d9db2711"),
    Disabled: uiText("ui.setup.ui.src.scenes.legacy.e3ad050f65"),
  };
  return map[s] ?? s.toLowerCase();
}

function accountWord(s: string): string {
  return /localsystem|^system$/i.test(s.trim())
    ? uiText("ui.setup.ui.src.scenes.legacy.09d2e388a4")
    : uiText("ui.setup.ui.src.scenes.legacy.8ce56570ea", {p0: String(s.trim() || uiText("ui.setup.ui.src.scenes.legacy.b40a5aea54"))});
}

export class LegacyScene extends FormScene {
  private found: LegacyService[] = [];
  private rows = el("div", "legacy-list");

  constructor(root: HTMLElement) {
    super(root);
    this.build();
  }

  /** Есть ли что показывать: сцену вставляют в маршрут только при true. */
  get any(): boolean {
    return this.found.length > 0;
  }

  /** Опрос SCM. `home` — папка, которую ставят или снимают: своя служба из
   *  списка выпадает, её установщик и так снимает сам. */
  async look(home: string): Promise<boolean> {
    try {
      this.found = (await legacyServices(home)).filter((s) => !s.ours);
    } catch {
      this.found = [];
    }
    this.build();
    return this.any;
  }

  private build() {
    const head = el("h2", "form-head");
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.legacy.1f25097ab6")));
    const lead = el(
      "p",
      "form-lead",
      this.found.length === 1
        ? uiText("ui.setup.ui.src.scenes.legacy.256b7d6903", {p0: String(this.found[0].name)})
        : uiText("ui.setup.ui.src.scenes.legacy.d84a35e032"),
    );
    this.rows = el("div", "legacy-list");
    // Кадр сцены не прокручивается: когда служб больше двух, путь к exe в
    // карточку не помещается — он там для узнавания, а не для дела.
    for (const s of this.found) this.rows.append(this.card(s, this.found.length <= 2));
    this.mount(
      head,
      lead,
      this.rows,
      explain(
        uiText("ui.setup.ui.src.scenes.legacy.c8e994b357"),
        uiText("ui.setup.ui.src.scenes.legacy.76750c16e3"),
      ),
      explain(
        uiText("ui.setup.ui.src.scenes.legacy.2a86ae002f"),
        uiText("ui.setup.ui.src.scenes.legacy.332077c756"),
      ),
    );
  }

  private card(s: LegacyService, withPath: boolean): HTMLElement {
    const box = el("div", "legacy-item");
    box.append(el("h3", "", uiText("ui.setup.ui.src.scenes.legacy.bee9a8c165", {p0: String(s.name)})));
    box.append(
      el("p", "", `${stateWord(s.state)}, ${startWord(s.start)}, ${accountWord(s.account)}.`),
    );
    if (s.path && withPath) box.append(el("p", "legacy-path", s.path));
    const result = el("p", "receipt");
    result.hidden = true;
    const actions = el("div", "install-actions");
    const btn = button(uiText("ui.setup.ui.src.scenes.legacy.a469baec03", {p0: String(s.name)}), "primary", () => {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = uiText("ui.setup.ui.src.scenes.legacy.a97c3726be");
      result.hidden = false;
      result.className = "receipt";
      result.textContent = uiText("ui.setup.ui.src.scenes.legacy.7b42651e17");
      void removeService(s.name)
        .then((text) => {
          result.className = "receipt ok";
          result.textContent = text;
          btn.hidden = true;
        })
        .catch((e) => {
          result.className = "receipt err";
          result.textContent =
            String(e) + uiText("ui.setup.ui.src.scenes.legacy.63c08637d6") + s.name + uiText("ui.setup.ui.src.scenes.legacy.d6ad0e127e") + s.name;
          btn.disabled = false;
          btn.textContent = uiText("ui.setup.ui.src.scenes.legacy.a469baec03", {p0: String(s.name)});
        });
    });
    actions.append(btn);
    box.append(actions, result);
    return box;
  }
}
