// Сцена «Установить»: сводка решений, кнопка, ход установки с расписками и
// кнопка «Открыть Frame». Установка начинается только нажатием кнопки.
import { MODE_CARDS } from "virtual:helene-modes";
import { FormScene } from "./base";
import { button, el } from "./form";
import { machine, openFrame, runInstall, setup, type Receipt } from "../setup";

export class InstallScene extends FormScene {
  private summary: HTMLElement;
  private actions: HTMLElement;
  private progress: HTMLElement;
  private result: HTMLElement;
  private started = false;
  private receipt: Receipt | null = null;

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", "Готово к установке"));
    this.summary = el("dl", "summary");
    this.actions = el("div", "install-actions");
    this.actions.append(button("Установить", "primary", () => void this.run()));
    this.progress = el("ol", "progress");
    this.progress.hidden = true;
    this.result = el("div", "install-result");
    this.result.hidden = true;
    this.mount(head, this.summary, this.actions, this.progress, this.result);
  }

  get locked(): boolean {
    return this.started;
  }

  private row(term: string, value: string) {
    const dt = el("dt", "", term);
    const dd = el("dd", "", value);
    this.summary.append(dt, dd);
  }

  protected beforeEnter() {
    if (this.started) return;
    this.summary.replaceChildren();
    // Явный switch, а не цепочка тернарников: провайдер anthropic проваливался
    // в ветку local, и перед самой кнопкой «Установить» владельцу показывали
    // чужой адрес (Ollama) и пустое имя модели.
    let model: string;
    switch (setup.provider) {
      case "api":
        model = `${setup.api.model} · ${setup.api.base_url}`;
        break;
      case "anthropic":
        model = `${setup.anthropic.model} · ${setup.anthropic.base_url}`;
        break;
      case "chatgpt":
        model = `подписка ChatGPT через встроенное реле · ${setup.chatgpt_model}`;
        break;
      case "local":
        model = `${setup.local.model} · ${setup.local.base_url}`;
        break;
    }
    const inst = machine.installed;
    if (inst) {
      const ver = inst.version ? `версия ${inst.version}` : "прежняя установка";
      this.row(
        "Уже установлено",
        `${ver}${inst.agent ? `, агент ${inst.agent}` : ""} — это обновление: память, конституция и настройки останутся`,
      );
    }
    this.row("Агент", setup.agent.trim());
    this.row("Владелец", setup.owner.trim());
    this.row("Модель", model);
    this.row("Telegram", setup.telegram.bot_token.trim() ? "бот подключён" : "только окно");
    // Два вопроса — две строки сводки, ровно как на экране выбора. Одной
    // строкой их писать нельзя: из склейки ограды со службой и вырос P0
    // (см. шапку scenes/mode.ts).
    const picked = MODE_CARDS.find((m) => m.name === setup.agent_mode);
    this.row("Ограда", picked?.title ?? setup.agent_mode);
    this.row(
      "Служба Windows",
      setup.service
        ? "поставить — Windows спросит права администратора" +
          (setup.session0 ? "; нулевая сессия РАЗРЕШЕНА" : "")
        : "не ставить — программа живёт из окна",
    );
    // Третий ответ — тоже своей строкой: опция поверх режима, не режим.
    this.row(
      "Управление компьютером",
      setup.computer
        ? "включить — агент получит окна, экран, клавиатуру и мышь; сузить права можно в настройках"
        : "не включать — включается потом, в настройках",
    );
    this.row("Папка", setup.dir);
  }

  private async run() {
    if (this.started) return;
    this.started = true;
    this.actions.hidden = true;
    this.progress.hidden = false;
    this.progress.replaceChildren();
    let current: HTMLElement | null = null;
    try {
      this.receipt = await runInstall((p) => {
        if (current) current.classList.add("done");
        current = el("li", "step", p.label);
        this.progress.append(current);
      });
      if (current) (current as HTMLElement).classList.add("done");
      this.showResult(this.receipt);
    } catch (err) {
      if (current) (current as HTMLElement).classList.add("failed");
      const text = String(err);
      const fail = el("li", "step failed", text);
      this.progress.append(fail);
      this.started = false;
      this.actions.hidden = false;
      // Отчёт живёт не только на экране: окно закроют — текст исчезнет навсегда.
      // Тот же текст пишется в install.log рядом с установщиком.
      const copy = button("Скопировать отчёт", "quiet", () => {
        void navigator.clipboard.writeText(text).catch(() => {});
      });
      this.actions.replaceChildren(button("Повторить", "primary", () => void this.run()), copy);
      const where = el("p", "muted", "Отчёт также записан в install.log рядом с установщиком.");
      this.progress.append(where);
    }
  }

  private showResult(r: Receipt) {
    this.result.hidden = false;
    this.result.replaceChildren();
    const title = el("h3", "", "Установлено");
    const lines = el("p", "");
    // «absent» приходил и когда службы нет в поставке, и когда владелец отказал
    // в правах: людям говорили про UAC, которого они не видели.
    const service =
      r.service === "running"
        ? "Служба работает."
        : r.service === "stopped"
          ? "Служба поставлена, но не запустилась: подробности в журнале службы."
          : r.service === "absent"
            ? "Служба не поставилась: права администратора не были даны."
            : r.service === "missing"
              ? "Служба не поставлена: в этой поставке нет helene-svc.exe."
              : r.service.startsWith("failed: ")
                ? `Служба не поставилась: ${r.service.slice(8)}`
                : "";
    lines.textContent = `Агент живёт в ${r.dir}. ${service}`.trim();
    // Промис нельзя терять: если helene.exe не стартует, кнопка «Открыть»
    // молча не делала ничего — ни окна, ни сообщения.
    const open = button("Открыть", "primary", () => {
      openFrame(r.exe).catch((err) => {
        this.result.append(
          el("p", "err", `Не удалось открыть ${"Hélène"}: ${err}. Она установлена в ${r.dir} — открой ярлык на рабочем столе.`),
        );
      });
    });
    this.result.append(title, lines);
    // Предупреждение службы печатаем ТЕКСТОМ здесь: сама служба сказать этого
    // не может — её ставят скрытым поднятым процессом, и её вывод не видит
    // никто, кроме service.log, который владелец не откроет.
    if (r.warning) this.result.append(el("p", "err", r.warning));
    this.result.append(open);
  }
}
