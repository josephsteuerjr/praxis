// Сцена «Что агенту можно?» — ДВА ВОПРОСА, а не один список из трёх.
//
// ⚠⚠ ЗДЕСЬ БЫЛ P0, и он был родом отсюда. Прежняя сцена показывала три
// карточки-режима (песочница | интерактивный | служба) одним рядом, и служба
// оказывалась третьей ОГРАДОЙ. Владелец, поставивший службу и хотевший
// песочницу, выбирал одну карточку — и в конфиг уезжало `agent_mode:
// "service"`, что означало «ограды нет». Ограда снималась молча.
//
// Правильная картина (localharness/modes.py, докстринг модуля):
//   1. ОГРАДА РУК — насколько далеко агент дотягивается. Два значения:
//      песочница или интерактивный. Прав администратора не требует ни одна.
//   2. СЛУЖБА — опция ПОВЕРХ любой ограды. Ставится один раз под
//      администратором, даёт защиту папки установки, жизнь без окна и брокера
//      прав. Ограду не снимает и не включает. Внутри опции — галочка нулевой
//      сессии, выключенная по умолчанию и с оговоркой.
//
// Тексты обоих вопросов НЕ пишутся здесь: они приезжают из
// localharness/modes.py на сборке (плагин helene-modes-from-python в
// vite.config.ts). Своей копии нет намеренно — разошлись бы описания, и
// владелец выбирал бы одно, а получал другое. Здесь дописано только то, что
// касается самой установки: нужны ли права администратора и что произойдёт
// прямо сейчас.
//
// Куда уезжает выбор: ограда — ключом `agent_mode` (НЕ `mode`: тот занят под
// местожительство харнесса, local|remote), служба — полем `service` установки,
// галочка — в `service.session0`, туда, где её читает служба.
import { MODE_CARDS, SERVICE_OPTION, SESSION0_WARNING, type ModeCard } from "virtual:helene-modes";
import { FormScene } from "./base";
import { el, explain, toggle } from "./form";
import { adminRights, setup, type AdminRights, type AgentMode } from "../setup";

/** Что произойдёт при установке с этой оградой — про сам установщик, а не про
 *  ограду: её описание приезжает из modes.py. */
const INSTALL_NOTE: Record<string, string> = {
  sandbox: "Прав администратора не нужно: программа ставится в твою папку. Папки, которые агент увидит снаружи, подключаются потом — в настройках.",
  interactive: "Прав администратора не нужно для самой ограды. Агент попросит их отдельно, окном Windows, когда они понадобятся конкретному действию.",
};

/** Что произойдёт при установке со службой. Только про установку: про саму
 *  службу уже сказано выше словами харнесса, и повторять их здесь незачем. */
const SERVICE_NOTE =
  "Windows один раз спросит права администратора: зарегистрировать службу может только он.";

/** Почему опция недоступна. Серая галочка без причины — это загадка, а не
 *  честность. */
const NO_ADMIN =
  "У этой учётной записи нет прав администратора, а зарегистрировать службу Windows может " +
  "только он. Попроси администратора запустить установщик — или ставь без службы: обе ограды " +
  "работают без неё, и службу можно добавить потом, в настройках.";

/** Оговорка к выключенной галочке нулевой сессии. Владелец должен прочитать её
 *  ДО того, как включит, а не узнать после. Включённая говорит словами самого
 *  харнесса (modes.SESSION0_WARNING) — чтобы окно потом не сказало иначе. */
const SESSION0_OFF =
  "Пока выключено: агент живёт в твоей сессии и видит рабочий стол. Если включишь — получит " +
  "права системы, но рабочего стола видеть перестанет, а слабая модель может не понять, что делает.";

/** Галочка нулевой сессии — единственная, которую спрашиваем при установке
 *  (владелец просил ровно её). Вторая галочка опции (`service.firewall`)
 *  живёт в Настройках: у неё оба положения не равноценны — выключенная только
 *  добавляет окно UAC на кнопку «Телефон», и на экране установки это шум.
 *  Ищем по ключу, а не по номеру: пропадёт она из modes.py — сборка упадёт
 *  здесь, а не покажет владельцу пустое место. */
const SESSION0 = (() => {
  const found = SERVICE_OPTION.toggles.find((t) => t.key === "service.session0");
  if (!found) throw new Error("modes.py: в опции службы нет галочки service.session0");
  return found;
})();

export class ModeScene extends FormScene {
  private cards = new Map<AgentMode, HTMLElement>();
  private serviceSwitch!: HTMLButtonElement;
  private serviceWhy!: HTMLElement;
  private extra!: HTMLElement;
  private session0Switch!: HTMLButtonElement;
  private extraText!: HTMLElement;
  private rights: AdminRights = { can: true, certain: false, elevated: false };

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", "Что агенту можно?"));
    const lead = el(
      "p",
      "form-lead",
      "Два вопроса, и они не связаны: насколько далеко агент дотягивается — и ставить ли службу Windows. " +
        "Поменять можно потом, в настройках программы.",
    );

    const row = el("div", "modes");
    row.setAttribute("role", "radiogroup");
    row.setAttribute("aria-label", "Ограда рук");
    for (const card of MODE_CARDS) row.append(this.card(card));

    // Врезки. Той, что обещала окна («Окна он водит в любом режиме»), здесь
    // больше нет: рука окон в поставку не едет ни в одном режиме, и modes.py
    // говорит об этом прямо в описании песочницы — карточка выше повторяет его
    // слово в слово.
    const notes = el("div", "explain-row");
    notes.append(
      explain(
        "Внутри своей папки он свободен",
        "При любой ограде агент правит собственный код и собственную память. В песочнице это " +
          "и есть весь его мир: сломать он может только себя.",
      ),
      explain(
        "Это не навсегда",
        "Ограда переключается после установки, на экране настроек. Службу там же можно поставить " +
          "или снять — Windows опять спросит права.",
      ),
    );

    this.mount(head, lead, row, this.serviceBox(), notes);
    // Вторая галочка опции (`service.firewall`) на экран не выведена, но её
    // умолчание берём отсюда же, а не заводим второй правдой в setup.ts.
    const firewall = SERVICE_OPTION.toggles.find((t) => t.key === "service.firewall");
    if (!firewall) throw new Error("modes.py: в опции службы нет галочки service.firewall");
    setup.firewall = firewall.default;
    this.select(setup.agent_mode);
    this.syncService();
    // Спрашиваем Windows один раз и в фоне: до ответа опция службы доступна —
    // заставой всё равно остаётся UAC при установке.
    void adminRights()
      .then((r) => {
        this.rights = r;
        this.syncAdmin();
      })
      .catch(() => {});
  }

  private card(item: ModeCard): HTMLElement {
    const name = item.name as AgentMode;
    const box = el("div", "mode-card");
    box.setAttribute("role", "radio");
    box.setAttribute("data-control", "");
    box.dataset.value = name;
    box.tabIndex = -1;
    box.append(el("span", "mode-title", item.title), el("p", "mode-text", item.text));
    const note = INSTALL_NOTE[name];
    if (note) box.append(el("p", "mode-note", note));

    box.addEventListener("click", () => this.select(name));
    box.addEventListener("keydown", (e) => {
      if (e.key === " " || e.key === "Enter") {
        e.preventDefault();
        this.select(name);
        return;
      }
      // Стрелки внутри группы переключают выбор, а не сцену: четверти экрана
      // отдают клавиши контролам, и без этого они здесь были бы мертвы.
      const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1
        : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      const order = MODE_CARDS.map((m) => m.name as AgentMode);
      const at = order.indexOf(setup.agent_mode);
      const next = order[(at + step + order.length) % order.length];
      this.select(next);
      this.cards.get(next)?.focus({ preventScroll: true });
    });
    this.cards.set(name, box);
    return box;
  }

  /** Опция службы: отдельным блоком под оградами, а не третьей карточкой в
   *  ряду. Ряд — это выбор одного из; служба выбором из ряда не является. */
  private serviceBox(): HTMLElement {
    const box = el("div", "service-card");
    const main = el("div", "service-main");
    this.serviceSwitch = toggle({
      label: SERVICE_OPTION.title,
      value: setup.service,
      onChange: (v) => {
        setup.service = v;
        this.syncService();
      },
    });
    main.append(
      this.serviceSwitch,
      el("p", "mode-text", SERVICE_OPTION.text),
      el("p", "mode-note", SERVICE_NOTE),
    );
    this.serviceWhy = el("p", "mode-why", NO_ADMIN);
    this.serviceWhy.hidden = true;
    main.append(this.serviceWhy);

    // Галочка нулевой сессии живёт ВНУТРИ опции службы: без службы её некому
    // исполнить, и показывать её отдельно значило бы обещать выбор, которого
    // нет. Появляется, только когда службу ставят.
    this.extra = el("div", "mode-extra");
    this.session0Switch = toggle({
      label: SESSION0.title,
      value: setup.session0,
      onChange: (v) => {
        setup.session0 = v;
        this.syncSession0();
      },
    });
    this.extraText = el("p", "mode-warn", SESSION0_OFF);
    this.extra.append(this.session0Switch, el("p", "mode-note", SESSION0.text), this.extraText);
    this.extra.hidden = true;

    box.append(main, this.extra);
    return box;
  }

  private select(name: AgentMode) {
    setup.agent_mode = name;
    for (const [key, box] of this.cards) {
      const on = key === name;
      box.setAttribute("aria-checked", String(on));
      box.tabIndex = on ? 0 : -1;
    }
  }

  /** Служба включена или нет: галочка нулевой сессии показывается только при
   *  включённой. Само значение `session0` при этом НЕ трогаем — владелец мог
   *  включить службу, поставить галочку, передумать про службу и вернуться;
   *  установщик всё равно запишет её только вместе со службой (install.rs). */
  private syncService() {
    this.serviceSwitch.setAttribute("aria-checked", String(setup.service));
    this.extra.hidden = !setup.service;
    this.syncSession0();
  }

  private syncSession0() {
    this.session0Switch.setAttribute("aria-checked", String(setup.session0));
    this.extraText.textContent = setup.session0 ? SESSION0_WARNING : SESSION0_OFF;
    this.extraText.classList.toggle("on", setup.session0);
  }

  /** Ответ Windows про права. Запрещаем только когда она ответила внятно:
   *  «не знаю» — не повод отнимать выбор, настоящей заставой остаётся UAC. */
  private syncAdmin() {
    const ok = this.rights.can || !this.rights.certain;
    this.serviceSwitch.disabled = !ok;
    this.serviceWhy.hidden = ok;
    if (!ok && setup.service) {
      // Не «просто серая»: причина уже напечатана выше, а сама опция
      // выключается — иначе установка упёрлась бы в UAC, который эта учётная
      // запись подтвердить не может.
      setup.service = false;
      this.syncService();
    }
  }

  protected beforeEnter() {
    this.select(setup.agent_mode);
    this.syncService();
    this.syncAdmin();
  }
}
