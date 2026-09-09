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
    Running: "работает прямо сейчас",
    Stopped: "остановлена",
    Paused: "приостановлена",
    StartPending: "запускается",
    StopPending: "останавливается",
  };
  return map[s] ?? s.toLowerCase();
}

function startWord(s: string): string {
  const map: Record<string, string> = {
    Auto: "запускается сама при каждой загрузке",
    Manual: "запускается по требованию",
    Disabled: "запуск запрещён",
  };
  return map[s] ?? s.toLowerCase();
}

function accountWord(s: string): string {
  return /localsystem|^system$/i.test(s.trim())
    ? "работает от имени СИСТЕМЫ — это самые большие права на компьютере"
    : `работает от имени ${s.trim() || "неизвестной учётной записи"}`;
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
    head.append(el("span", "line", "На этом компьютере уже живёт прежняя версия"));
    const lead = el(
      "p",
      "form-lead",
      this.found.length === 1
        ? `Windows говорит, что служба «${this.found[0].name}» зарегистрирована и никуда не делась. Это тот же продукт, только под прежним именем: снятие прошлой версии удаляло файлы, а службу оставляло.`
        : "Windows говорит, что здесь зарегистрированы службы прежних версий. Это тот же продукт под прежними именами: снятие прошлой версии удаляло файлы, а службу оставляло.",
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
        "Чем она мешает",
        "Такая служба работает от имени системы и запускает программу из папки, куда пишет обычный пользователь: всё, что может писать в ту папку от твоего имени, получит права системы при следующей загрузке. И ещё: её реле держит тот же локальный порт, на который настраивается новый агент, — тогда он пошёл бы разговаривать с прежним реле, с чужой подпиской, а ты увидел бы только «модель не ответила».",
      ),
      explain(
        "Можно и не снимать",
        "Без нажатия кнопки установщик ничего с ней не делает. Снять её можно и потом, из командной строки администратора: sc.exe stop <имя> и sc.exe delete <имя>. Файлы прежней версии лежат в %LOCALAPPDATA%\\Programs — их установщик тоже не трогает.",
      ),
    );
  }

  private card(s: LegacyService, withPath: boolean): HTMLElement {
    const box = el("div", "legacy-item");
    box.append(el("h3", "", `Служба «${s.name}»`));
    box.append(
      el("p", "", `${stateWord(s.state)}, ${startWord(s.start)}, ${accountWord(s.account)}.`),
    );
    if (s.path && withPath) box.append(el("p", "legacy-path", s.path));
    const result = el("p", "receipt");
    result.hidden = true;
    const actions = el("div", "install-actions");
    const btn = button(`Снять службу «${s.name}»`, "primary", () => {
      if (btn.disabled) return;
      btn.disabled = true;
      btn.textContent = "Снимаю…";
      result.hidden = false;
      result.className = "receipt";
      result.textContent = "Windows спросит права администратора.";
      void removeService(s.name)
        .then((text) => {
          result.className = "receipt ok";
          result.textContent = text;
          btn.hidden = true;
        })
        .catch((e) => {
          result.className = "receipt err";
          result.textContent =
            String(e) + " — сними её вручную: sc.exe stop " + s.name + " и sc.exe delete " + s.name;
          btn.disabled = false;
          btn.textContent = `Снять службу «${s.name}»`;
        });
    });
    actions.append(btn);
    box.append(actions, result);
    return box;
  }
}
