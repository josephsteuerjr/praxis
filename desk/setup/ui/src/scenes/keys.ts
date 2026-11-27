import { t as uiText, locale } from "../../../../lang/lang";
// Сцена «Ключи»: откуда приходит модель и нужен ли Telegram. Рядом с каждым
// полем — зачем оно, человеческими словами.
import { FormScene } from "./base";
import { button, choice, el, explain, field } from "./form";
import { openLoginPage, probeModel, relayLogin, relayLoginUrl, relayModels, relayStatus, setup, type Effort, type Provider } from "../setup";
import { ANTHROPIC_PRESETS, BILLING_LABEL, clampEffort, effortPlan } from "../../../../ui-kit/providers";
import { copyText } from "../../../../ui-kit/dom";

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
    head.append(el("span", "line", uiText("ui.setup.ui.src.scenes.keys.dcab2054e6")));
    const lead = el("p", "form-lead",
      uiText("ui.setup.ui.src.scenes.keys.10668e9ff1"));
    // Про деньги установщик не говорил ни слова, а первая вкладка — платная по счётчику.
    const money = el("p", "form-lead muted",
      uiText("ui.setup.ui.src.scenes.keys.2f6fcb7e68"));

    const picker = choice<Provider>({
      value: setup.provider,
      items: [
        { value: "api", title: uiText("ui.setup.ui.src.scenes.keys.7a63ad80c1"), text: uiText("ui.setup.ui.src.scenes.keys.56f4cbc5fd") },
        { value: "anthropic", title: "Anthropic API", text: uiText("ui.setup.ui.src.scenes.keys.5c06578d8c") },
        { value: "chatgpt", title: uiText("ui.setup.ui.src.scenes.keys.f4a43f895c"), text: uiText("ui.setup.ui.src.scenes.keys.1b16e85be8") },
        { value: "local", title: uiText("ui.setup.ui.src.scenes.keys.1ad4d6964d"), text: uiText("ui.setup.ui.src.scenes.keys.bc2889d0ea") },
      ],
      onChange: (v) => {
        setup.provider = v;
        this.showPane(v);
        this.syncEffort();
      },
    });

    const api = el("div", "pane");
    const apiGrid = el("div", "form-grid three");
    const apiModelField = field({ label: uiText("ui.setup.ui.src.scenes.keys.f3bdf7cce3"), value: setup.api.model, mono: true, onInput: (v) => (setup.api.model = v) });
    apiGrid.append(
      field({ label: uiText("ui.setup.ui.src.scenes.keys.da82e80563"), value: setup.api.base_url, mono: true, onInput: (v) => (setup.api.base_url = v) }),
      apiModelField,
      field({ label: uiText("ui.setup.ui.src.scenes.keys.248f99a1d2"), value: setup.api.key, type: "password", mono: true, placeholder: "sk-…", onInput: (v) => (setup.api.key = v) }),
    );
    const apiModels = el("div", "models");
    apiModels.hidden = true;
    const probeRow = el("div", "actions");
    const probeOut = el("span", "receipt");
    probeRow.append(
      button(uiText("ui.setup.ui.src.scenes.keys.2babf9e4d1"), "quiet", async () => {
        probeOut.className = "receipt";
        probeOut.textContent = uiText("ui.setup.ui.src.scenes.keys.1bdd492f1c");
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
    api.append(apiGrid, probeRow, apiModels, explain(uiText("ui.setup.ui.src.scenes.keys.77e5917e28"),
      uiText("ui.setup.ui.src.scenes.keys.9d778cd59e")));

    // Anthropic-совместимые: Anthropic и Z.ai (тот же протокол, другой адрес).
    const anthropic = el("div", "pane");
    const anthGrid = el("div", "form-grid three");
    const anthBase = field({ label: uiText("ui.setup.ui.src.scenes.keys.da82e80563"), value: setup.anthropic.base_url, mono: true, onInput: (v) => (setup.anthropic.base_url = v) });
    const anthModel = field({ label: uiText("ui.setup.ui.src.scenes.keys.f3bdf7cce3"), value: setup.anthropic.model, mono: true, onInput: (v) => { setup.anthropic.model = v; this.syncEffort(); } });
    anthGrid.append(
      anthBase,
      anthModel,
      field({ label: uiText("ui.setup.ui.src.scenes.keys.248f99a1d2"), value: setup.anthropic.key, type: "password", mono: true, placeholder: "sk-ant-…", onInput: (v) => (setup.anthropic.key = v) }),
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
      button(uiText("ui.setup.ui.src.scenes.keys.2babf9e4d1"), "quiet", async () => {
        anthOut.className = "receipt";
        anthOut.textContent = uiText("ui.setup.ui.src.scenes.keys.1bdd492f1c");
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
    anthropic.append(anthGrid, anthPresets, presetNote, anthProbeRow, anthModels, explain(uiText("ui.setup.ui.src.scenes.keys.04ce954c0d"),
      uiText("ui.setup.ui.src.scenes.keys.2c97eecea1")));

    const chatgpt = el("div", "pane");
    const relayRow = el("div", "actions");
    const relayOut = el("span", "receipt");
    // Ссылка входа (1.2.3): помощник реле может не открыть браузер (27.09 у владельца так и
    // было) — тогда страницу открывает сам мастер или человек вставляет ссылку сам.
    let linkUrl = "";
    const linkRow = el("div", "actions");
    linkRow.hidden = true;
    const linkOut = el("span", "receipt");
    linkRow.append(
      button(uiText("ui.setup.ui.src.scenes.keys.23b5fabf7b"), "quiet", async () => {
        linkOut.textContent = "";
        await openLoginPage().catch((e) => (linkOut.textContent = String(e)));
      }),
      button(uiText("ui.setup.ui.src.scenes.keys.8d81013698"), "quiet", async () => {
        linkOut.textContent = (await copyText(linkUrl))
          ? uiText("ui.setup.ui.src.scenes.keys.20cee8c2e0")
          : uiText("ui.setup.ui.src.scenes.keys.5377f3ed70");
      }),
      linkOut,
    );
    const relayRefresh = async () => {
      const st = await relayStatus().catch(() => "no-auth" as const);
      this.relay = st;
      relayOut.className = "receipt " + (st === "authorized" ? "ok" : "");
      relayOut.textContent =
        st === "authorized"
          ? uiText("ui.setup.ui.src.scenes.keys.891a59bfe4")
          : st === "pending"
            ? uiText("ui.setup.ui.src.scenes.keys.c34576dccd")
            : uiText("ui.setup.ui.src.scenes.keys.5382269d78");
      loginBtn.textContent = st === "pending" ? uiText("ui.setup.ui.src.scenes.keys.e3539b105f") : uiText("ui.setup.ui.src.scenes.keys.59624437c6");
      linkUrl = st === "pending" ? (await relayLoginUrl().catch(() => null)) || "" : "";
      linkRow.hidden = !linkUrl;
    };
    let poll = 0;
    const loginBtn = button(uiText("ui.setup.ui.src.scenes.keys.59624437c6"), "quiet", async () => {
      relayOut.className = "receipt";
      relayOut.textContent = await relayLogin().catch((e) => String(e));
      window.clearInterval(poll);
      window.setTimeout(() => void relayRefresh(), 1200);
      let tries = 0;
      poll = window.setInterval(() => {
        void relayRefresh();
        if (++tries > 100) window.clearInterval(poll);
      }, 3000);
    });
    relayRow.append(loginBtn, relayOut);
    void relayRefresh();
    const chatgptGrid = el("div", "form-grid two");
    const chatgptModel = field({ label: uiText("ui.setup.ui.src.scenes.keys.f3bdf7cce3"), value: setup.chatgpt_model, mono: true, onInput: (v) => (setup.chatgpt_model = v) });
    chatgptGrid.append(chatgptModel);
    const relayModelsBox = el("div", "models");
    relayModelsBox.hidden = true;
    const relayModelsRow = el("div", "actions");
    const relayModelsOut = el("span", "receipt");
    relayModelsRow.append(
      button(uiText("ui.setup.ui.src.scenes.keys.80b838e2ff"), "quiet", async () => {
        relayModelsOut.className = "receipt";
        relayModelsOut.textContent = uiText("ui.setup.ui.src.scenes.keys.85fd6f5cbe");
        try {
          const ids = await relayModels();
          relayModelsOut.className = "receipt ok";
          relayModelsOut.textContent = uiText("ui.setup.ui.src.scenes.keys.d2bc633950", {p0: new Intl.NumberFormat(locale()).format(ids.length)});
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
    chatgpt.append(chatgptGrid, relayModelsRow, relayModelsBox, relayRow, linkRow, explain(uiText("ui.setup.ui.src.scenes.keys.b9f1fffef4"),
      uiText("ui.setup.ui.src.scenes.keys.d71f679a03")));

    const local = el("div", "pane");
    const localGrid = el("div", "form-grid two");
    const localModelField = field({ label: uiText("ui.setup.ui.src.scenes.keys.f3bdf7cce3"), value: setup.local.model, mono: true, placeholder: uiText("ui.setup.ui.src.scenes.keys.ae5036bd22"), onInput: (v) => (setup.local.model = v) });
    localGrid.append(
      field({ label: uiText("ui.setup.ui.src.scenes.keys.da82e80563"), value: setup.local.base_url, mono: true, onInput: (v) => (setup.local.base_url = v) }),
      localModelField,
    );
    const localModels = el("div", "models");
    localModels.hidden = true;
    const localProbeRow = el("div", "actions");
    const localProbeOut = el("span", "receipt");
    localProbeRow.append(
      button(uiText("ui.setup.ui.src.scenes.keys.b46e85648e"), "quiet", async () => {
        localProbeOut.className = "receipt";
        localProbeOut.textContent = uiText("ui.setup.ui.src.scenes.keys.25cd3cf450");
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
    local.append(localGrid, localProbeRow, localModels, explain(uiText("ui.setup.ui.src.scenes.keys.97c8d82d1b"),
      uiText("ui.setup.ui.src.scenes.keys.c8726872b3")));

    this.panes = { api, anthropic, chatgpt, local };
    const paneBox = el("div", "panes");
    paneBox.append(api, anthropic, chatgpt, local);

    // Усилие рассуждения — начальное значение (слово владельца). Шкала ЗАВИСИТ ОТ
    // ПРОВАЙДЕРА И МОДЕЛИ (ui-kit/providers.ts): показываем только ступени,
    // которые ядро действительно передаст, и говорим, что значит пустое поле
    // именно здесь. Прежний единый ряд low…xhigh обещал Anthropic-совместимым
    // серверам то, что до них не доезжало.
    const effortBox = el("div", "effort");
    effortBox.append(el("h3", "form-sub", uiText("ui.setup.ui.src.scenes.keys.0cd8556749")));
    const effortRow = el("div", "models");
    const effortHint = el("p", "effort-hint");
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
      if (!plan.chips.length) effortRow.append(el("span", "receipt", uiText("ui.setup.ui.src.scenes.keys.f0c92f3ecb")));
      effortHint.textContent = plan.hint;
    };
    this.syncEffort();
    effortBox.append(effortRow, effortHint, explain("",
      uiText("ui.setup.ui.src.scenes.keys.f538bb04c3")));

    const tg = el("div", "tg");
    tg.append(el("h3", "form-sub", uiText("ui.setup.ui.src.scenes.keys.445d928971")));
    const tgGrid = el("div", "form-grid two");
    tgGrid.append(
      field({ label: uiText("ui.setup.ui.src.scenes.keys.81ff0c1357"), value: setup.telegram.bot_token, type: "password", mono: true, placeholder: uiText("ui.setup.ui.src.scenes.keys.978a4520dd"), onInput: (v) => (setup.telegram.bot_token = v) }),
      field({ label: uiText("ui.setup.ui.src.scenes.keys.add56e0ee1"), value: setup.telegram.owner_id, mono: true, placeholder: uiText("ui.setup.ui.src.scenes.keys.cc5a073f3a"), onInput: (v) => (setup.telegram.owner_id = v) }),
    );
    tg.append(tgGrid, explain("",
      uiText("ui.setup.ui.src.scenes.keys.b5bdd20766")));

    this.mount(head, lead, money, picker, paneBox, effortBox, tg);
    this.showPane(setup.provider);
  }

  private showPane(p: Provider) {
    for (const [k, pane] of Object.entries(this.panes)) pane.hidden = k !== p;
  }

  validate(): string | null {
    if (setup.provider === "api") {
      if (!setup.api.base_url.trim()) return uiText("ui.setup.ui.src.scenes.keys.39d77b0e97");
      if (!setup.api.model.trim()) return uiText("ui.setup.ui.src.scenes.keys.8947f32411");
      if (!setup.api.key.trim()) return uiText("ui.setup.ui.src.scenes.keys.7a36ac251c");
    }
    // Ветки anthropic не было вовсе: можно было пройти дальше с пустым адресом,
    // моделью и ключом — установка проходила, а агент молчал навсегда.
    if (setup.provider === "anthropic") {
      if (!setup.anthropic.base_url.trim()) return uiText("ui.setup.ui.src.scenes.keys.39d77b0e97");
      if (!setup.anthropic.model.trim()) return uiText("ui.setup.ui.src.scenes.keys.8947f32411");
      if (!setup.anthropic.key.trim()) return uiText("ui.setup.ui.src.scenes.keys.7a36ac251c");
    }
    if (setup.provider === "local") {
      if (!setup.local.base_url.trim()) return uiText("ui.setup.ui.src.scenes.keys.b091448e67");
      if (!setup.local.model.trim()) return uiText("ui.setup.ui.src.scenes.keys.9537a18c89");
    }
    // Для chatgpt раньше не проверялось ничего: можно было поставить без входа,
    // и в расписке про подписку не появлялось ни строки — молчание вместо
    // предупреждения. Спрашиваем один раз и пускаем: войти можно и позже.
    if (setup.provider === "chatgpt") {
      if (!setup.chatgpt_model.trim()) return uiText("ui.setup.ui.src.scenes.keys.12b9ad00f3");
      if (this.relay !== "authorized" && !this.relayWarned) {
        this.relayWarned = true;
        return uiText("ui.setup.ui.src.scenes.keys.dfd38810dd");
      }
    }
    const tg = setup.telegram;
    if (tg.bot_token.trim() && !/^\d+$/.test(tg.owner_id.trim())) {
      return uiText("ui.setup.ui.src.scenes.keys.f49c820746");
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
  const label = el("span", "models-label", known ? uiText("ui.setup.ui.src.scenes.keys.6b5b2dcdd5") : uiText("ui.setup.ui.src.scenes.keys.297baea812", {p0: String(current.trim() || "…")}));
  box.append(label);
  for (const id of shown) {
    const chip = el("button", "model-chip", id) as HTMLButtonElement;
    chip.type = "button";
    chip.setAttribute("aria-pressed", String(id === current.trim()));
    chip.addEventListener("click", () => {
      pick(id);
      for (const other of box.querySelectorAll(".model-chip")) other.setAttribute("aria-pressed", String(other === chip));
      label.textContent = uiText("ui.setup.ui.src.scenes.keys.6b5b2dcdd5");
    });
    box.append(chip);
  }
  if (models.length > shown.length) {
    box.append(el("span", "models-label", uiText("ui.setup.ui.src.scenes.keys.3247b49a56", {p0: new Intl.NumberFormat(locale()).format(models.length - shown.length)})));
  }
}
