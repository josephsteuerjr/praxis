// Сцена «Ключи»: откуда приходит модель и нужен ли Telegram. Рядом с каждым
// полем — зачем оно, человеческими словами.
import { FormScene } from "./base";
import { button, choice, el, explain, field } from "./form";
import { probeModel, relayLogin, relayModels, relayStatus, setup, type Effort, type Provider } from "../setup";
import { ANTHROPIC_PRESETS, BILLING_LABEL, clampEffort, effortPlan } from "../../../../ui-kit/providers";

export class KeysScene extends FormScene {
  private panes: Record<Provider, HTMLElement>;
  /** Последнее известное состояние входа в ChatGPT — его спрашивает validate(). */
  private relay: "authorized" | "pending" | "no-auth" = "no-auth";
  private relayWarned = false;
  /** Перестроить шкалу рассуждения под провайдера и модель; задаётся в constructor. */
  private syncEffort: () => void = () => {};

  constructor(root: HTMLElement) {
    super(root);
    const head = el("h2", "form-head");
    head.append(el("span", "line", "Откуда приходит модель"));
    const lead = el("p", "form-lead",
      "Модель — единственное, что агент берёт извне. Всё остальное остаётся на этом компьютере.");
    // Про деньги установщик не говорил ни слова, а первая вкладка — платная по счётчику.
    const money = el("p", "form-lead muted",
      "О деньгах прямо: по ключу API платишь за каждый ответ по счётчику, потолка нет. " +
      "По подписке ChatGPT — фиксированная плата, но есть недельное окно запросов. " +
      "Локальная модель бесплатна, но считает на твоём железе.");

    const picker = choice<Provider>({
      value: setup.provider,
      items: [
        { value: "api", title: "Ключ API", text: "OpenAI и любой совместимый адрес" },
        { value: "anthropic", title: "Anthropic API", text: "Anthropic, Z.ai, MiniMax, Kimi и совместимые" },
        { value: "chatgpt", title: "Подписка ChatGPT", text: "вход в аккаунт, без ключа" },
        { value: "local", title: "Локальная модель", text: "Ollama или LM Studio на этом ПК" },
      ],
      onChange: (v) => {
        setup.provider = v;
        this.showPane(v);
        this.syncEffort();
      },
    });

    const api = el("div", "pane");
    const apiGrid = el("div", "form-grid three");
    const apiModelField = field({ label: "Модель", value: setup.api.model, mono: true, onInput: (v) => (setup.api.model = v) });
    apiGrid.append(
      field({ label: "Адрес", value: setup.api.base_url, mono: true, onInput: (v) => (setup.api.base_url = v) }),
      apiModelField,
      field({ label: "Ключ", value: setup.api.key, type: "password", mono: true, placeholder: "sk-…", onInput: (v) => (setup.api.key = v) }),
    );
    const apiModels = el("div", "models");
    apiModels.hidden = true;
    const probeRow = el("div", "actions");
    const probeOut = el("span", "receipt");
    probeRow.append(
      button("Проверить ключ", "quiet", async () => {
        probeOut.className = "receipt";
        probeOut.textContent = "проверяю…";
        const r = await probeModel(setup.api.base_url, setup.api.key).catch((e) => ({ ok: false, note: String(e), models: [] as string[] }));
        probeOut.className = "receipt " + (r.ok ? "ok" : "err");
        probeOut.textContent = r.note;
        renderModels(apiModels, r.models || [], setup.api.model, (id) => {
          setup.api.model = id;
          setInput(apiModelField, id);
        });
      }),
      probeOut,
    );
    api.append(apiGrid, probeRow, apiModels, explain("Зачем ключ",
      "Ключ даёт агенту доступ к модели напрямую. Он лежит только в файле настроек на этом диске и никуда больше не уходит. Адрес и модель — любые OpenAI-совместимые."));

    // Anthropic-совместимые: Anthropic и Z.ai (тот же протокол, другой адрес).
    const anthropic = el("div", "pane");
    const anthGrid = el("div", "form-grid three");
    const anthBase = field({ label: "Адрес", value: setup.anthropic.base_url, mono: true, onInput: (v) => (setup.anthropic.base_url = v) });
    const anthModel = field({ label: "Модель", value: setup.anthropic.model, mono: true, onInput: (v) => { setup.anthropic.model = v; this.syncEffort(); } });
    anthGrid.append(
      anthBase,
      anthModel,
      field({ label: "Ключ", value: setup.anthropic.key, type: "password", mono: true, placeholder: "sk-ant-…", onInput: (v) => (setup.anthropic.key = v) }),
    );
    // Кнопки провайдеров — из общей с окном таблицы (ui-kit/providers.ts):
    // адрес, модель по умолчанию и честная строка про план и рассуждение.
    const anthPresets = el("div", "actions");
    const presetNote = el("p", "form-lead muted");
    presetNote.hidden = true;
    for (const preset of ANTHROPIC_PRESETS) {
      anthPresets.append(button(preset.label, "quiet", () => {
        setup.anthropic.base_url = preset.url;
        setInput(anthBase, preset.url);
        if (preset.model) {
          setup.anthropic.model = preset.model;
          setInput(anthModel, preset.model);
        }
        presetNote.textContent = `${preset.label} — ${BILLING_LABEL[preset.billing]}. ${preset.note}`;
        presetNote.hidden = false;
        this.syncEffort();
      }));
    }
    const anthModels = el("div", "models");
    anthModels.hidden = true;
    const anthProbeRow = el("div", "actions");
    const anthOut = el("span", "receipt");
    anthProbeRow.append(
      button("Проверить ключ", "quiet", async () => {
        anthOut.className = "receipt";
        anthOut.textContent = "проверяю…";
        const r = await probeModel(setup.anthropic.base_url, setup.anthropic.key, "anthropic").catch((e) => ({ ok: false, note: String(e), models: [] as string[] }));
        anthOut.className = "receipt " + (r.ok ? "ok" : "err");
        anthOut.textContent = r.note;
        renderModels(anthModels, r.models || [], setup.anthropic.model, (id) => {
          setup.anthropic.model = id;
          setInput(anthModel, id);
          this.syncEffort();
        });
      }),
      anthOut,
    );
    anthropic.append(anthGrid, anthPresets, presetNote, anthProbeRow, anthModels, explain("Один протокол, шесть дверей",
      "Anthropic, Z.ai, MiniMax, Kimi, DeepSeek и Qwen говорят на протоколе Anthropic Messages: кнопка подставляет адрес и модель, ключ — из кабинета провайдера. Подписки (coding plan) — у Z.ai, MiniMax, Kimi и Qwen; DeepSeek и Anthropic — только по счётчику. Проверка спросит список моделей, если сервер его отдаёт."));

    const chatgpt = el("div", "pane");
    const relayRow = el("div", "actions");
    const relayOut = el("span", "receipt");
    const relayRefresh = async () => {
      const st = await relayStatus().catch(() => "no-auth" as const);
      this.relay = st;
      relayOut.className = "receipt " + (st === "authorized" ? "ok" : "");
      relayOut.textContent =
        st === "authorized"
          ? "Вход выполнен, он переедет вместе с установкой"
          : st === "pending"
            ? "Ждём вход в браузере. Если вкладка закрылась, нажми ещё раз: прежняя попытка отменится."
            : "Вход ещё не выполнен";
      loginBtn.textContent = st === "pending" ? "Начать вход заново" : "Войти в ChatGPT";
    };
    let poll = 0;
    const loginBtn = button("Войти в ChatGPT", "quiet", async () => {
      relayOut.className = "receipt";
      relayOut.textContent = await relayLogin().catch((e) => String(e));
      window.clearInterval(poll);
      let tries = 0;
      poll = window.setInterval(() => {
        void relayRefresh();
        if (++tries > 100) window.clearInterval(poll);
      }, 3000);
    });
    relayRow.append(loginBtn, relayOut);
    void relayRefresh();
    const chatgptGrid = el("div", "form-grid two");
    const chatgptModel = field({ label: "Модель", value: setup.chatgpt_model, mono: true, onInput: (v) => (setup.chatgpt_model = v) });
    chatgptGrid.append(chatgptModel);
    const relayModelsBox = el("div", "models");
    relayModelsBox.hidden = true;
    const relayModelsRow = el("div", "actions");
    const relayModelsOut = el("span", "receipt");
    relayModelsRow.append(
      button("Показать модели реле", "quiet", async () => {
        relayModelsOut.className = "receipt";
        relayModelsOut.textContent = "спрашиваю реле…";
        try {
          const ids = await relayModels();
          relayModelsOut.className = "receipt ok";
          relayModelsOut.textContent = `Реле знает моделей: ${ids.length}`;
          renderModels(relayModelsBox, ids, setup.chatgpt_model, (id) => {
            setup.chatgpt_model = id;
            setInput(chatgptModel, id);
          });
        } catch (e) {
          relayModelsOut.className = "receipt err";
          relayModelsOut.textContent = String(e);
        }
      }),
      relayModelsOut,
    );
    chatgpt.append(chatgptGrid, relayModelsRow, relayModelsBox, relayRow, explain("Как это работает",
      "Встроенное реле ходит в ChatGPT по твоей подписке. Вход откроет браузер; учётные данные переедут в установленную программу. Ключ не нужен. Войти можно и позже, в настройках; там же после входа виден список моделей подписки, и модель можно сменить."));

    const local = el("div", "pane");
    const localGrid = el("div", "form-grid two");
    const localModelField = field({ label: "Модель", value: setup.local.model, mono: true, placeholder: "например, qwen3:14b", onInput: (v) => (setup.local.model = v) });
    localGrid.append(
      field({ label: "Адрес", value: setup.local.base_url, mono: true, onInput: (v) => (setup.local.base_url = v) }),
      localModelField,
    );
    const localModels = el("div", "models");
    localModels.hidden = true;
    const localProbeRow = el("div", "actions");
    const localProbeOut = el("span", "receipt");
    localProbeRow.append(
      button("Показать модели", "quiet", async () => {
        localProbeOut.className = "receipt";
        localProbeOut.textContent = "спрашиваю…";
        const r = await probeModel(setup.local.base_url, "").catch((e) => ({ ok: false, note: String(e), models: [] as string[] }));
        localProbeOut.className = "receipt " + (r.ok ? "ok" : "err");
        localProbeOut.textContent = r.note;
        renderModels(localModels, r.models || [], setup.local.model, (id) => {
          setup.local.model = id;
          setInput(localModelField, id);
        });
      }),
      localProbeOut,
    );
    local.append(localGrid, localProbeRow, localModels, explain("Всё остаётся здесь",
      "Ollama или LM Studio на этом компьютере. Ключа нет, в сеть ничего не уходит. Модель должна быть уже скачана."));

    this.panes = { api, anthropic, chatgpt, local };
    const paneBox = el("div", "panes");
    paneBox.append(api, anthropic, chatgpt, local);

    // Усилие рассуждения — начальное значение (слово владельца). Шкала ЗАВИСИТ ОТ
    // ПРОВАЙДЕРА И МОДЕЛИ (ui-kit/providers.ts): показываем только ступени,
    // которые ядро действительно передаст, и говорим, что значит пустое поле
    // именно здесь. Прежний единый ряд low…xhigh обещал Anthropic-совместимым
    // серверам то, что до них не доезжало.
    const effortBox = el("div", "effort");
    effortBox.append(el("h3", "form-sub", "Усилие рассуждения"));
    const effortRow = el("div", "models");
    const effortHint = el("p", "form-lead muted");
    const modelForEffort = () =>
      setup.provider === "anthropic" ? setup.anthropic.model
        : setup.provider === "api" ? setup.api.model
          : setup.provider === "local" ? setup.local.model
            : setup.chatgpt_model;
    this.syncEffort = () => {
      const plan = effortPlan(setup.provider, modelForEffort());
      setup.reasoning_effort = clampEffort(setup.provider, modelForEffort(), setup.reasoning_effort) as Effort;
      effortRow.replaceChildren();
      for (const { value, label } of plan.chips) {
        const chip = el("button", "model-chip", label) as HTMLButtonElement;
        chip.type = "button";
        chip.setAttribute("aria-pressed", String(setup.reasoning_effort === value));
        chip.addEventListener("click", () => {
          setup.reasoning_effort = value as Effort;
          for (const other of effortRow.querySelectorAll(".model-chip")) other.setAttribute("aria-pressed", String(other === chip));
        });
        effortRow.append(chip);
      }
      if (!plan.chips.length) effortRow.append(el("span", "receipt", "ступень сюда не передаётся"));
      effortHint.textContent = plan.hint;
    };
    this.syncEffort();
    effortBox.append(effortRow, effortHint, explain("",
      "Сколько модель думает перед ответом: выше усилие — дольше и дороже ответ (рассуждение оплачивается как выходные токены). Менять можно в настройках."));

    const tg = el("div", "tg");
    tg.append(el("h3", "form-sub", "Telegram, если нужен"));
    const tgGrid = el("div", "form-grid two");
    tgGrid.append(
      field({ label: "Токен бота", value: setup.telegram.bot_token, type: "password", mono: true, placeholder: "от @BotFather", onInput: (v) => (setup.telegram.bot_token = v) }),
      field({ label: "Твой Telegram id", value: setup.telegram.owner_id, mono: true, placeholder: "число, узнать у @userinfobot", onInput: (v) => (setup.telegram.owner_id = v) }),
    );
    tg.append(tgGrid, explain("",
      "Бот — вторая дверь к агенту, кроме окна. Токен выдаёт @BotFather, свой id подскажет @userinfobot: без него агент не поймёт, кто из пишущих его владелец. Можно оставить пустым. Свой аккаунт Telegram для агента (не бот) подключается после установки, в настройках."));

    this.mount(head, lead, money, picker, paneBox, effortBox, tg);
    this.showPane(setup.provider);
  }

  private showPane(p: Provider) {
    for (const [k, pane] of Object.entries(this.panes)) pane.hidden = k !== p;
  }

  validate(): string | null {
    if (setup.provider === "api") {
      if (!setup.api.base_url.trim()) return "Нужен адрес модели";
      if (!setup.api.model.trim()) return "Назови модель";
      if (!setup.api.key.trim()) return "Нужен ключ API";
    }
    // Ветки anthropic не было вовсе: можно было пройти дальше с пустым адресом,
    // моделью и ключом — установка проходила, а агент молчал навсегда.
    if (setup.provider === "anthropic") {
      if (!setup.anthropic.base_url.trim()) return "Нужен адрес модели";
      if (!setup.anthropic.model.trim()) return "Назови модель";
      if (!setup.anthropic.key.trim()) return "Нужен ключ API";
    }
    if (setup.provider === "local") {
      if (!setup.local.base_url.trim()) return "Нужен адрес локальной модели";
      if (!setup.local.model.trim()) return "Назови локальную модель";
    }
    // Для chatgpt раньше не проверялось ничего: можно было поставить без входа,
    // и в расписке про подписку не появлялось ни строки — молчание вместо
    // предупреждения. Спрашиваем один раз и пускаем: войти можно и позже.
    if (setup.provider === "chatgpt") {
      if (!setup.chatgpt_model.trim()) return "Назови модель подписки";
      if (this.relay !== "authorized" && !this.relayWarned) {
        this.relayWarned = true;
        return "Вход в ChatGPT не выполнен — без него агент не ответит. Нажми «Далее» ещё раз, если войдёшь позже в настройках.";
      }
    }
    const tg = setup.telegram;
    if (tg.bot_token.trim() && !/^\d+$/.test(tg.owner_id.trim())) {
      return "Для Telegram нужен и твой id: число";
    }
    return null;
  }
}

// Список моделей после проверки адреса: выбор одним нажатием вместо имени вслепую.
function setInput(wrap: HTMLElement, value: string) {
  const input = wrap.querySelector("input");
  if (input) input.value = value;
}

function renderModels(box: HTMLElement, models: string[], current: string, pick: (id: string) => void) {
  box.replaceChildren();
  if (!models.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  // Известность считаем по ПОЛНОМУ списку, а показываем часть: раньше список
  // резался по алфавиту, и продукт заявлял «модели нет», хотя её просто обрезали.
  const known = models.includes(current.trim());
  const shown = models.filter((id) => id === current.trim()).concat(models.filter((id) => id !== current.trim())).slice(0, 40);
  const label = el("span", "models-label", known ? "Доступные модели" : `Модели «${current.trim() || "…"}» в списке нет. Доступные:`);
  box.append(label);
  for (const id of shown) {
    const chip = el("button", "model-chip", id) as HTMLButtonElement;
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(id === current.trim()));
    chip.addEventListener("click", () => {
      pick(id);
      for (const other of box.querySelectorAll(".model-chip")) other.setAttribute("aria-pressed", String(other === chip));
      label.textContent = "Доступные модели";
    });
    box.append(chip);
  }
  if (models.length > shown.length) {
    box.append(el("span", "models-label", `и ещё ${models.length - shown.length} — впиши имя руками`));
  }
}
