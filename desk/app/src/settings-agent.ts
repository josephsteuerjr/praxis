// Карточки МЕСТНОГО агента: модель, Telegram, ограда рук со службой, песочница,
// монтирование, управление компьютером, папка данных.
//
// ⚠ Почему это отдельный файл и почему он у Элен. Всё здесь читает и пишет
// helene.json, лежащий РЯДОМ С ОКНОМ, и спрашивает харнесс на этой же машине.
// У Пульта агент живёт на сервере: его настройки правятся там, а этот файл
// правил бы не тот конфиг — молча и правдоподобно. До 10.09 карточки жили в
// общем экране и прятались сорока четырьмя ветками `if (remote)`; заплатка
// работала, но означала, что окно Элен возит в себе логику Пульта, а Пульт —
// карточки агента, которого рядом нет.
//
// Каркас экрана — общий (`ui-kit/window/views/settings-frame.ts`), и правило
// «блоки конфига СЛИВАЮТСЯ, а не пересобираются» держится там же: всё, что
// пишется здесь, идёт через `keepBlock`.
import QRCode from "qrcode";
import { api, shell } from "../../ui-kit/window/api";
import { ANTHROPIC_PRESETS, BILLING_LABEL, clampEffort, effortPlan } from "../../ui-kit/providers";
import { keepBlock } from "../../ui-kit/window/config";
import { el, humanError, toast } from "../../ui-kit/window/lib";
import { button, card, chips, field, setField, toggle } from "../../ui-kit/dom";
import { computerCard, storedComputer } from "./computer";
import { MODE_KEY, loadMode, type ModeState } from "../../ui-kit/window/mode";
import { modeCard } from "./modecard";
import { mountsCard, type LiveSandbox } from "./mounts";
import { voiceCard } from "./voicecard";
import { agentsCard } from "./agentscard";
import { RELAY_PORT, relayBaseUrl, relayProbeUrl, newRelayKey } from "./relay";
import type { Config, Edition, EditionContext } from "../../ui-kit/window/views/settings-frame";
import { GROUP, inGroup } from "../../ui-kit/window/views/settings-frame";

type Provider = "api" | "anthropic" | "chatgpt" | "local";


/**
 * Годный ли Telegram-id владельца. То же правило, что в визарде
 * (setup/ui/src/scenes/keys.ts): только цифры. Плюс ноль — не id, а ровно то
 * значение, которое окно молча клало вместо пустого поля: гейт харнесса при
 * нём не пускает НИКОГО, и бот молчит на всё при зелёной шапке.
 */
function ownerIdOk(v: unknown): boolean {
  const s = String(v ?? "").trim();
  return /^\d+$/.test(s) && Number(s) > 0;
}

function providerOf(c: Config): Provider {
  if (c.relay?.enabled) return "chatgpt";
  if (String(c.model?.framework || "") === "anthropic") return "anthropic";
  const url = c.model?.base_url || "";
  if (/127\.0\.0\.1|localhost/.test(url) && !c.relay?.enabled) return "local";
  return "api";
}

function renderModels(box: HTMLElement, models: string[], current: string, pick: (id: string) => void) {
  const known = models.includes(current.trim());
  const label = known || !models.length ? "Доступные модели" : `Модели «${current.trim() || "…"}» в списке нет. Доступные:`;
  chips(box, models.slice(0, 40).map((value) => ({ value })), current.trim(), pick, label);
}

/** Карточки местного агента и его часть записи в конфиг. */
export async function agentEdition({ draft, loaded }: EditionContext): Promise<Edition> {
  // Режим спрашиваем у трубы, а не разбираем конфиг сами: правила режима живут
  // в одном месте (localharness/modes.py), и второй разбор здесь разошёлся бы с
  // первым. Отказ трубы — не повод не открыть настройки: карточка скажет о нём
  // словами, остальные настройки работают.
  let modeLive: ModeState | null = null;
  let modeFail: unknown = null;
  try {
    modeLive = await loadMode();
  } catch (e) {
    modeFail = e;
  }
  // Снимок устройства нужен карточке монтирования: просьбы агента лежат в его
  // дереве (`memory/.state/mounts.json`), а приговор харнесса по каждой папке —
  // только у него. Отказ снимка настройки не закрывает: карточка скажет, чего
  // не знает. ⚠ Снимок пишется на СТАРТЕ харнесса (runner._write_anatomy), то
  // есть он не свежее последнего запуска — обещать по нему «сейчас» нельзя.
  let liveSandbox: LiveSandbox | null = null;
  let anatomyFail: unknown = null;
  try {
    const snap = await api<{ sandbox?: LiveSandbox }>("/api/anatomy");
    liveSandbox = snap && typeof snap.sandbox === "object" ? snap.sandbox || null : null;
  } catch (e) {
    anatomyFail = e;
  }
  // Просьбы агента — живьём из `/api/mode` (КОНТРАКТ A→B §2), когда канал их
  // отдаёт: снимок анатомии пишется один раз на старте и устаревает.
  if (modeLive?.mounts_live && Array.isArray(modeLive.mounts_live.requests)) {
    liveSandbox = { ...(liveSandbox || {}), mount_requests: modeLive.mounts_live.requests };
    anatomyFail = null;
  }
  let provider = providerOf(draft);
  const savedProvider = provider;
  // Ключи провайдеров храним раздельно. Раньше в конфиге был один model.key, а
  // черновик неактивной вкладки заполнялся пустотой: щелчок по чужой вкладке и
  // «Сохранить» стирали боевой ключ прежнего провайдера — единственную копию на
  // диске, без предупреждения и без отмены. Блок model.keys ядру не мешает:
  // boot._brain_config читает только известные поля.
  const keyStore: Record<string, string> = { ...(draft.model.keys || {}) };
  if (draft.model.key) keyStore[savedProvider] = String(draft.model.key);
  const keyOf = (p: Provider) => String(keyStore[p] || "");
  const cards: HTMLElement[] = [];
  //  Заполняется на «Сохранить»: расписка читает его следом за collect.
  let noKeyNote = "";
  // --- модель
  const model = el("div");
  const pick = el("div", "choice");
  pick.setAttribute("role", "radiogroup");
  const panes: Record<Provider, HTMLElement> = { api: el("div"), anthropic: el("div"), chatgpt: el("div"), local: el("div") };
  const items: Array<[Provider, string, string]> = [
    ["api", "Ключ API", "OpenAI и любой совместимый адрес"],
    ["anthropic", "Anthropic API", "Anthropic, Z.ai, MiniMax, Kimi и совместимые"],
    ["chatgpt", "Подписка ChatGPT", "вход в аккаунт через встроенное реле"],
    ["local", "Локальная модель", "Ollama или LM Studio на этом ПК"],
  ];
  const syncPick = () => {
    for (const b of pick.querySelectorAll<HTMLButtonElement>(".choice-item")) b.setAttribute("aria-checked", String(b.dataset.value === provider));
    for (const [k, pane] of Object.entries(panes)) pane.hidden = k !== provider;
  };
  for (const [value, title, text] of items) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.value = value;
    b.append(el("span", "choice-title", title), el("span", "choice-text", text));
    b.addEventListener("click", () => {
      provider = value;
      syncPick();
      // Ярлык «усилия» зависит от провайдера — на подписке пустое поле значит
      // «рассуждение выключено», а не «решает модель».
      syncEffort();
    });
    pick.append(b);
  }
  const apiGrid = el("div", "form-grid three");
  apiGrid.style.marginTop = "14px";
  // gpt-5.2 реле не знает (SUPPORTED_MODELS её не содержит) и PLAN просил её
  // убрать: подставленная вслепую, она уезжала в конфиг как имя несуществующей.
  const apiDraft = { base_url: provider === "api" ? String(draft.model.base_url || "") : "https://api.openai.com/v1", model: provider === "api" ? String(draft.model.model || "") : "gpt-5.4", key: keyOf("api") };
  const apiModelField = field("Модель", apiDraft.model, (v) => (apiDraft.model = v), { mono: true });
  apiGrid.append(
    field("Адрес", apiDraft.base_url, (v) => (apiDraft.base_url = v), { mono: true }),
    apiModelField,
    field("Ключ", apiDraft.key, (v) => (apiDraft.key = v), { type: "password", mono: true }),
  );
  const apiModels = el("div", "models");
  apiModels.hidden = true;
  const probeRow = el("div", "actions");
  probeRow.style.marginTop = "12px";
  const probeOut = el("span", "receipt");
  probeRow.append(
    button("Проверить", "quiet", async () => {
      probeOut.className = "receipt";
      probeOut.textContent = "проверяю…";
      try {
        const r = await shell<{ ok: boolean; note: string; models?: string[] }>("probe_model", { baseUrl: apiDraft.base_url, key: apiDraft.key, framework: "openai" });
        probeOut.className = "receipt " + (r.ok ? "ok" : "err");
        probeOut.textContent = r.note;
        renderModels(apiModels, r.models || [], apiDraft.model, (id) => {
          apiDraft.model = id;
          setField(apiModelField, id);
        });
      } catch (e) {
        probeOut.className = "receipt err";
        probeOut.textContent = humanError(e).text;
      }
    }),
    probeOut,
  );
  panes.api.append(apiGrid, probeRow, apiModels);

  // --- Anthropic-совместимые (Anthropic, Z.ai)
  const anthDraft = { base_url: provider === "anthropic" ? String(draft.model.base_url || "") : "https://api.z.ai/api/anthropic", model: provider === "anthropic" ? String(draft.model.model || "") : "glm-5.3", key: keyOf("anthropic") };
  const anthGrid = el("div", "form-grid three");
  anthGrid.style.marginTop = "14px";
  const anthBase = field("Адрес", anthDraft.base_url, (v) => (anthDraft.base_url = v), { mono: true });
  const anthModelField = field("Модель", anthDraft.model, (v) => {
    anthDraft.model = v;
    // Шкала рассуждения зависит от модели (glm-* против остальных).
    syncEffort();
  }, { mono: true });
  anthGrid.append(anthBase, anthModelField, field("Ключ", anthDraft.key, (v) => (anthDraft.key = v), { type: "password", mono: true, placeholder: "sk-ant-…" }));
  const anthPresets = el("div", "actions");
  anthPresets.style.marginTop = "12px";
  // Кнопки провайдеров — из общей таблицы (ui-kit/providers.ts): адрес, модель
  // по умолчанию и честная строка про план и рассуждение.
  const presetNote = el("p", "field-hint", "");
  presetNote.style.marginTop = "8px";
  for (const preset of ANTHROPIC_PRESETS) {
    anthPresets.append(button(preset.label, "quiet", () => {
      anthDraft.base_url = preset.url;
      setField(anthBase, preset.url);
      if (preset.model) {
        anthDraft.model = preset.model;
        setField(anthModelField, preset.model);
      }
      presetNote.textContent = `${preset.label} — ${BILLING_LABEL[preset.billing]}. ${preset.note}`;
      syncEffort();
    }));
  }
  const anthModels = el("div", "models");
  anthModels.hidden = true;
  const anthOut = el("span", "receipt");
  anthPresets.append(
    button("Проверить", "quiet", async () => {
      anthOut.className = "receipt";
      anthOut.textContent = "проверяю…";
      try {
        const r = await shell<{ ok: boolean; note: string; models?: string[] }>("probe_model", { baseUrl: anthDraft.base_url, key: anthDraft.key, framework: "anthropic" });
        anthOut.className = "receipt " + (r.ok ? "ok" : "err");
        anthOut.textContent = r.note;
        renderModels(anthModels, r.models || [], anthDraft.model, (id) => { anthDraft.model = id; setField(anthModelField, id); syncEffort(); });
      } catch (e) {
        anthOut.className = "receipt err";
        anthOut.textContent = humanError(e).text;
      }
    }),
    anthOut,
  );
  panes.anthropic.append(anthGrid, anthPresets, presetNote, anthModels, el("p", "field-hint", "Все эти провайдеры говорят на протоколе Anthropic Messages: кнопка подставляет адрес и модель, ключ — из кабинета провайдера. Проверка спросит список моделей, если сервер его отдаёт."));

  const relayRow = el("div", "actions");
  relayRow.style.marginTop = "14px";
  const relayOut = el("span", "receipt");
  let relayAuthorized = false;
  const relayRefresh = async () => {
    try {
      const st = await shell<string>("relay_status");
      relayAuthorized = st === "authorized";
      relayOut.className = "receipt " + (relayAuthorized ? "ok" : "");
      // Реле читает учётные данные ОДИН раз, на старте: пока его не
      // перезапустят, оно на каждый вызов отдаёт 503 login_required. Раньше
      // здесь стояло зелёное «Вход выполнен», и агент всё равно молчал.
      relayOut.textContent =
        relayAuthorized
          ? "Вход выполнен — применится перезапуском"
          : st === "pending" ? "Ждём вход в браузере. Повторное нажатие отменит прежнюю попытку." : "Вход ещё не выполнен";
      loginBtn.textContent = st === "pending" ? "Начать вход заново" : "Войти в ChatGPT";
      relayRestart.hidden = !relayAuthorized;
    } catch (e) {
      relayOut.className = "receipt err";
      relayOut.textContent = humanError(e).text;
    }
  };
  const relayRestart = button("Перезапустить сейчас", "quiet", () => dispatchEvent(new Event("frame-restart")));
  relayRestart.hidden = true;
  let relayPoll = 0;
  const loginBtn = button("Войти в ChatGPT", "quiet", async () => {
    try {
      toast(await shell<string>("relay_login"));
      window.clearInterval(relayPoll);
      let tries = 0;
      relayPoll = window.setInterval(() => {
        void relayRefresh();
        if (++tries > 100) window.clearInterval(relayPoll);
      }, 3000);
    } catch (e) {
      toast("Вход не запустился: " + humanError(e).text);
    }
  });
  relayRow.append(loginBtn, relayRestart, relayOut);
  const chatgptDraft = { model: provider === "chatgpt" ? String(draft.model.model || "gpt-5.6-sol") : "gpt-5.6-sol" };
  const chatgptGrid = el("div", "form-grid two");
  chatgptGrid.style.marginTop = "14px";
  const chatgptModelField = field("Модель", chatgptDraft.model, (v) => (chatgptDraft.model = v), { mono: true });
  chatgptGrid.append(chatgptModelField);
  const chatgptModels = el("div", "models");
  chatgptModels.hidden = true;
  const chatgptProbeRow = el("div", "actions");
  const chatgptProbeOut = el("span", "receipt");
  chatgptProbeRow.append(
    button("Показать модели подписки", "quiet", async () => {
      chatgptProbeOut.className = "receipt";
      chatgptProbeOut.textContent = "спрашиваю реле…";
      try {
        // Ключ сюда не отдаём: /v1/models у реле авторизацию не проверяет вовсе,
        // а порт 5011 в этот момент может держать не наше реле — и боевой ключ
        // OpenAI уехал бы чужому процессу заголовком Authorization.
        // RELAY_PROBE_URL — единственное место, где «/v1» уместно: у реле есть
        // GET /v1/models, но НЕТ POST /v1/chat/completions. Адрес мозга берётся
        // из RELAY_BASE_URL того же модуля, чтобы эти двое не разошлись снова.
        const r = await shell<{ ok: boolean; note: string; models?: string[] }>("probe_model", { baseUrl: relayProbeUrl(Number(draft.relay?.port) || RELAY_PORT), key: "", framework: "openai" });
        // Ответ /v1/models доказывает только то, что процесс реле жив: ни вход в
        // аккаунт, ни остаток лимита он не проверяет. Зелёное «моделей 6» при
        // невыполненном входе и было механизмом «всё зелёное, агент молчит».
        await relayRefresh();
        chatgptProbeOut.className = "receipt " + (r.ok && relayAuthorized ? "ok" : r.ok ? "" : "err");
        chatgptProbeOut.textContent = r.ok
          ? relayAuthorized
            ? `Реле живо, вход в ChatGPT выполнен. ${r.note}`
            : `Реле живо, но вход в ChatGPT НЕ выполнен — агент будет молчать. Нажми «Войти в ChatGPT» выше. (${r.note})`
          : `${r.note}. Реле поднимается вместе с программой после сохранения и перезапуска.`;
        renderModels(chatgptModels, r.models || [], chatgptDraft.model, (id) => {
          chatgptDraft.model = id;
          setField(chatgptModelField, id);
        });
      } catch (e) {
        chatgptProbeOut.className = "receipt err";
        chatgptProbeOut.textContent = humanError(e).text;
      }
    }),
    chatgptProbeOut,
  );
  panes.chatgpt.append(el("p", "field-hint", "Реле поднимается вместе с программой и ходит в ChatGPT по подписке. Вход открывает браузер; после входа ключ не нужен."), relayRow, chatgptGrid, chatgptProbeRow, chatgptModels);
  void relayRefresh();

  const localGrid = el("div", "form-grid two");
  localGrid.style.marginTop = "14px";
  const localDraft = { base_url: provider === "local" ? String(draft.model.base_url || "") : "http://127.0.0.1:11434/v1", model: provider === "local" ? String(draft.model.model || "") : "" };
  const localModelField = field("Модель", localDraft.model, (v) => (localDraft.model = v), { mono: true, placeholder: "например, qwen3:14b" });
  localGrid.append(field("Адрес", localDraft.base_url, (v) => (localDraft.base_url = v), { mono: true }), localModelField);
  const localModels = el("div", "models");
  localModels.hidden = true;
  const localProbeRow = el("div", "actions");
  localProbeRow.style.marginTop = "12px";
  const localProbeOut = el("span", "receipt");
  localProbeRow.append(
    button("Показать модели", "quiet", async () => {
      localProbeOut.className = "receipt";
      localProbeOut.textContent = "спрашиваю…";
      try {
        const r = await shell<{ ok: boolean; note: string; models?: string[] }>("probe_model", { baseUrl: localDraft.base_url, key: "", framework: "openai" });
        localProbeOut.className = "receipt " + (r.ok ? "ok" : "err");
        localProbeOut.textContent = r.note;
        renderModels(localModels, r.models || [], localDraft.model, (id) => {
          localDraft.model = id;
          setField(localModelField, id);
        });
      } catch (e) {
        localProbeOut.className = "receipt err";
        localProbeOut.textContent = humanError(e).text;
      }
    }),
    localProbeOut,
  );
  panes.local.append(localGrid, localProbeRow, localModels);
  // Усилие рассуждения — шкала ЗАВИСИТ ОТ ПРОВАЙДЕРА И МОДЕЛИ (ui-kit/providers.ts):
  // окно показывает только те ступени, которые ядро действительно передаст, и
  // говорит, что значит пустое поле именно здесь (на подписке — «выключено»,
  // у Z.ai — серверный max, у остальных по протоколу Anthropic — ничего).
  let effort = String(draft.model.reasoning_effort || "");
  const effortRow = el("div", "models");
  effortRow.style.marginTop = "14px";
  const effortHint = el("p", "field-hint", "");
  effortHint.style.marginTop = "8px";
  const modelForEffort = () =>
    provider === "anthropic" ? anthDraft.model : provider === "api" ? apiDraft.model : provider === "local" ? localDraft.model : chatgptDraft.model;
  const syncEffort = () => {
    const plan = effortPlan(provider, modelForEffort());
    effort = clampEffort(provider, modelForEffort(), effort);
    effortRow.replaceChildren(el("span", "models-label", "Усилие рассуждения"));
    for (const { value, label } of plan.chips) {
      const chip = el("button", "model-chip", label);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(effort === value));
      chip.addEventListener("click", () => {
        effort = value;
        for (const other of effortRow.querySelectorAll(".model-chip")) other.setAttribute("aria-pressed", String(other === chip));
      });
      effortRow.append(chip);
    }
    if (!plan.chips.length) effortRow.append(el("span", "receipt", "ступень сюда не передаётся"));
    effortHint.textContent = plan.hint;
  };
  // Запасная и зрячая модели — ручки того же блока `model`, общие для всех провайдеров.
  // До 09.09 их можно было задать только правкой helene.json руками (boot.py их переносил,
  // окно — нет): владелец спросил «есть ли фолбэк в интерфейсе» — не было.
  let fallbackModel = String(draft.model.fallback_model || "");
  let visionModel = String(draft.model.vision_model || "");
  const spareGrid = el("div", "form-grid two");
  spareGrid.style.marginTop = "14px";
  spareGrid.append(
    field("Запасная модель", fallbackModel, (v) => (fallbackModel = v), { mono: true, placeholder: "пусто — без запасной" }),
    field("Зрячая модель", visionModel, (v) => (visionModel = v), { mono: true, placeholder: "пусто — glm-5.3-flash для GLM" }),
  );
  const spareHint = el("p", "field-hint",
    "Запасная модель того же провайдера берёт ход, когда основная упала (обрыв, 5xx, пустой ответ). " +
    "Зрячая модель получает ход, в котором есть картинка, если основная её не видит: для GLM это glm-5.3-flash " +
    "того же ключа, у зрячих моделей (GPT, Claude) поле не нужно. Переключение происходит до вызова, роль и усилие не меняются.");
  spareHint.style.marginTop = "8px";
  model.append(pick, panes.api, panes.anthropic, panes.chatgpt, panes.local, effortRow, effortHint, spareGrid, spareHint);
  syncPick();
  syncEffort();
  cards.push(inGroup(card("Модель", model), GROUP.brain));

  // --- Telegram: бот или свой аккаунт агента
  const tgBox = el("div");
  let tgMode: "bot" | "account" = draft.telegram.mode === "account" ? "account" : "bot";
  const tgPick = el("div", "choice");
  tgPick.setAttribute("role", "radiogroup");
  const tgPanes = { bot: el("div"), account: el("div") };
  const syncTg = () => {
    for (const b of tgPick.querySelectorAll<HTMLButtonElement>(".choice-item")) b.setAttribute("aria-checked", String(b.dataset.value === tgMode));
    tgPanes.bot.hidden = tgMode !== "bot";
    tgPanes.account.hidden = tgMode !== "account";
  };
  for (const [value, title, text] of [
    ["bot", "Бот", "токен от @BotFather, самый простой путь"],
    ["account", "Свой аккаунт", "отдельный номер для агента, MTProto"],
  ] as Array<["bot" | "account", string, string]>) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.value = value;
    b.append(el("span", "choice-title", title), el("span", "choice-text", text));
    b.addEventListener("click", () => {
      tgMode = value;
      draft.telegram!.mode = value;
      syncTg();
    });
    tgPick.append(b);
  }
  // Предупреждение харнесса «telegram.owner_id не задан — ход не пойдёт ни от
  // кого» (localharness/botapi.py) уходило только в helene.log, которого
  // владелец не читает. Поднимаем его сюда, на глаза, ещё до «Сохранить».
  const tgWarn = el("p", "receipt err");
  const syncTgWarn = () => {
    const hasToken = !!String(draft.telegram!.bot_token || "").trim();
    tgWarn.hidden = !hasToken || ownerIdOk(draft.telegram!.owner_id);
    tgWarn.textContent = "Токен есть, а твоего id нет: ход разрешён только владельцу, и без id бот будет молчать на всё. Узнать id: напиши @userinfobot — он ответит числом.";
  };
  const ownerField = field("Твой Telegram id", String(draft.telegram.owner_id || "") === "0" ? "" : String(draft.telegram.owner_id || ""), (v) => {
    draft.telegram!.owner_id = v;
    syncTgWarn();
  }, { mono: true, placeholder: "число, узнать у @userinfobot" });
  const tg = el("div", "form-grid two");
  tg.style.marginTop = "14px";
  tg.append(field("Токен бота", String(draft.telegram.bot_token || ""), (v) => {
    draft.telegram!.bot_token = v;
    syncTgWarn();
  }, { type: "password", mono: true, placeholder: "от @BotFather" }));
  tgPanes.bot.append(tg, tgWarn, el("p", "field-hint", "Бот — вторая дверь к агенту. Без токена агент живёт только в окне."));
  syncTgWarn();
  const acc = el("div", "form-grid three");
  acc.style.marginTop = "14px";
  acc.append(
    field("api_id", String(draft.telegram.api_id || ""), (v) => (draft.telegram!.api_id = v), { mono: true, placeholder: "с my.telegram.org" }),
    field("api_hash", String(draft.telegram.api_hash || ""), (v) => (draft.telegram!.api_hash = v), { type: "password", mono: true }),
    field("Телефон агента", String(draft.telegram.phone || ""), (v) => (draft.telegram!.phone = v), { mono: true, placeholder: "+7…" }),
  );
  const accRow = el("div", "actions");
  accRow.style.marginTop = "12px";
  const accOut = el("span", "receipt");
  const codeField = field("Код из Telegram", "", (v) => (accCode = v), { mono: true, placeholder: "12345" });
  const passField = field("Облачный пароль, если есть", "", (v) => (accPass = v), { type: "password", mono: true });
  let accCode = "";
  let accPass = "";
  const codeGrid = el("div", "form-grid two");
  codeGrid.style.marginTop = "12px";
  codeGrid.append(codeField, passField);
  codeGrid.hidden = true;
  const accCall = async (step: string) => {
    accOut.className = "receipt";
    accOut.textContent = "спрашиваю Telegram…";
    try {
      const r = await shell<{ ok: boolean; state?: string; username?: string; name?: string; error?: string }>("telegram_account", {
        step,
        apiId: String(draft.telegram?.api_id || ""),
        apiHash: String(draft.telegram?.api_hash || ""),
        phone: String(draft.telegram?.phone || ""),
        code: accCode,
        password: accPass,
      });
      if (r.state === "authorized") {
        accOut.className = "receipt ok";
        accOut.textContent = `Вошла: ${r.name || ""} ${r.username ? "@" + r.username : ""}`.trim();
        codeGrid.hidden = true;
      } else if (r.state === "code_sent" || r.state === "password_needed") {
        accOut.className = "receipt " + (r.ok ? "" : "err");
        accOut.textContent = r.error || "Код отправлен в Telegram агента — введи его ниже";
        codeGrid.hidden = false;
      } else if (r.ok) {
        accOut.textContent = "Вход ещё не выполнен";
      } else {
        accOut.className = "receipt err";
        accOut.textContent = r.error || "не вышло";
      }
    } catch (e) {
      accOut.className = "receipt err";
      accOut.textContent = humanError(e).text;
    }
  };
  accRow.append(
    button("Получить код", "quiet", () => void accCall("send")),
    button("Войти с кодом", "primary", () => void accCall("code")),
    button("Выйти", "quiet", () => void accCall("logout")),
    accOut,
  );
  tgPanes.account.append(
    acc,
    accRow,
    codeGrid,
    el("p", "field-hint", "Агент говорит из своего аккаунта Telegram, как человек: нужен отдельный номер и ключи приложения с my.telegram.org. Вход один раз; сессия лежит в data/telegram. Применяется перезапуском."),
  );
  tgBox.append(tgPick, tgPanes.bot, tgPanes.account, ownerField);
  syncTg();
  if (tgMode === "account" && draft.telegram.api_id) void accCall("status");
  cards.push(inGroup(card("Telegram", tgBox), GROUP.brain));

  // --- ограда рук (песочница | интерактивный) и ОТДЕЛЬНО от неё служба
  //
  // Карточка живёт отдельным файлом (../mode). Она же держит кнопки «Поставить
  // службу» / «Снять службу» и две галочки службы: служба — не режим, а опция
  // поверх любой ограды, и второй точки управления ею на экране нет. Ограду
  // служба не снимает: ровно эта склейка стоила владельцу молча снятой
  // песочницы.
  draft.sandbox = draft.sandbox || {};
  // Галочки службы берём ИЗ ФАЙЛА, а не из ответа трубы: труба отдаёт
  // действующие, а без установленной службы `session0` всегда false — писать по
  // ней значило бы молча стирать выбор владельца при первом же «Сохранить».
  const svcBlock = draft.service && typeof draft.service === "object" ? draft.service : {};
  const storedService = {
    session0: !!svcBlock.session0,
    // Умолчание брандмауэра — ДА: ключа нет = служба ставит правило сама
    // (modes.FIREWALL_DEFAULT). Прочитать его как false значило бы выключить
    // владельцу кнопку «Телефон», которой он не касался.
    firewall: svcBlock.firewall !== false,
  };
  const sandboxState = el("p", "field-hint");
  const mode = modeCard(modeLive, modeFail, storedService, (_name, sandbox, title) => {
    syncSandboxState(sandbox, title);
    mounts.setFence(sandbox, title);
    // Монтирование — дверь песочницы. Без ограды файловые руки и shell видят
    // всё, что доступно учётке (слово владельца 06.09), и карточке здесь нечего
    // показывать; список в конфиге живёт и оживает вместе с песочницей.
    mounts.el.hidden = !sandbox;
  });
  cards.push(inGroup(mode.el, GROUP.rights));

  // --- песочница: сеть контейнера остаётся выбором владельца, ограду ставит режим
  const sb = el("div");
  const sbNet = toggle("Сеть из shell", draft.sandbox.network !== false, (v) => (draft.sandbox!.network = v));
  /**
   * Строка состояния ограды — БЕЗ единого утверждения о том, что ограда накрывает.
   *
   * ⚠ Здесь стояла своя строка, и в ней была неправда: «Окна ограда не трогает:
   * агент их видит и водит». Ограда до руки окон действительно не достаёт, но
   * самой руки в поставке нет ни в одном режиме — это проверено по коду (задача
   * C3) и снято из `modes.TEXTS` и `fence.WINDOWS_TRUTH`. На экране владельца
   * выдумка пережила исправление ровно потому, что была копией.
   *
   * Поэтому второй копии тут нет и не будет: что именно накрывает ограда,
   * написано ОДИН раз — в карточке «Режим» выше, словами `modes.TEXTS` прямо из
   * трубы. Здесь только название выбранной ограды (тоже из трубы) и то, что
   * делает соседний тумблер.
   */
  function syncSandboxState(on: boolean, title: string) {
    const named = title ? `«${title}»` : "выбранный режим";
    sandboxState.textContent = on
      ? `Ограду ставит ${named} — что она накрывает, написано в карточке «Режим» выше. ` +
        "Тумблер ниже решает, пустят ли команды агента в интернет из контейнера."
      : `${title ? named : "Выбранный режим"} ограду не ставит: файлы и команды идут с твоими правами. ` +
        "Тумблер ниже подействует только в песочнице.";
  }
  syncSandboxState(mode.name() ? mode.sandbox() : draft.sandbox.enabled !== false, mode.title());
  sb.append(sandboxState, sbNet);
  cards.push(inGroup(card("Песочница", sb, "Что вышло на самом деле — видно на экране «Система». Применяется перезапуском."), GROUP.rights));

  // --- монтирование: папки владельца, открытые агенту сверх его дома
  //
  // Списка в окне не было вовсе: владелец правил `sandbox.mounts` руками, а
  // просьбы агента копились в `data/memory/.state/mounts.json` и были видны
  // только в анатомии. Карточка правит ЧЕРНОВИК; в файл всё уезжает общей
  // кнопкой «Сохранить», как и остальные настройки.
  const mounts = mountsCard(
    draft.sandbox,
    liveSandbox,
    anatomyFail,
    mode.name() ? mode.sandbox() : draft.sandbox.enabled !== false,
    mode.title() || modeLive?.title || "",
  );
  mounts.el.hidden = !(mode.name() ? mode.sandbox() : draft.sandbox.enabled !== false);
  cards.push(inGroup(mounts.el, GROUP.rights));

  // --- управление компьютером: опция ПОВЕРХ любого режима (06.09)
  //
  // Тело руки `computer` живёт снаружи ограды, поэтому карточка не прячется
  // ни в одном режиме. Тексты и четыре права — из трубы (`computer_option`),
  // живое состояние тела — из снимка харнесса (`computer_live`); окно
  // пишет ровно два ключа блока и сливает остальное.
  const computer = computerCard(modeLive, storedComputer(draft.computer));
  cards.push(inGroup(computer.el, GROUP.rights));

  // --- голос: единственная часть продукта, которой не хватает гигабайта
  //
  // Библиотека едет в рантайме, модель — нет (480 МБ у маленькой, 1,6 ГБ у
  // рабочей). Поэтому карточка не просто ставит галочку, а показывает, чего не
  // хватает, и качает это; состояние она спрашивает у канала (`/api/voice`),
  // тем же модулем, которым голос поднимает раннер.
  const voice = voiceCard(draft);
  cards.push(inGroup(voice.el, GROUP.brain));

  // --- агенты этой установки (11.09)
  //
  // Карточка стоит перед «Данными агента» намеренно: сразу за ней идёт папка
  // ЭТОГО агента, и владелец видит, чей дом ему показывают.
  cards.push(inGroup(agentsCard().el, GROUP.agent));

  // --- данные
  const data = el("div", "actions");
  data.append(
    el("span", "mono", loaded.tree),
    button("Открыть папку", "quiet", () => void shell("open_path", { path: loaded.tree }).catch((e) => toast(humanError(e).text))),
  );
  cards.push(inGroup(card("Данные агента", data, "Память, дневник, конституция и настройки лежат здесь. Перенос агента на другую машину — перенос этой папки вместе с программой."), GROUP.agent));

  return {
    cards,
    // Четыре карточки, которые вместе решают, что агенту разрешено на этом
    // компьютере, впервые оказываются рядом: режим ограды, песочница,
    // смонтированные папки и управление компьютером. До этого они лежали в
    // общем списке из четырнадцати, вперемешку с моделью и телефоном.
    groups: [
      { id: GROUP.agent, label: "Агент" },
      { id: GROUP.brain, label: "Мозг и связь" },
      { id: GROUP.rights, label: "Права на этом ПК" },
      { id: GROUP.app, label: "Программа" },
    ],
    // Подпись у изданий разная: здесь имя агента правит того, кто живёт рядом.
    namesHint:
      "Имя агента войдёт в подписи и в снимок состояния; имя владельца нужно агенту, чтобы знать, чьё слово решает. " +
      "Конституция агента этим не меняется: имена в ней он написал при рождении, и правит их только он сам или ты руками — " +
      "раздел «Файлы», soul/SOUL.md.",
    // Телефон подключается к ЭТОЙ машине: харнесс здесь.
    phoneBase: "",
    qrSvg: (text: string) =>
      QRCode.toString(text, { type: "svg", margin: 1, width: 240,
                              color: { dark: "#262320", light: "#ffffff" } }),
    // Хвост расписки: что осталось сделать по режиму (поставить или снять
    // службу) и не забыт ли ключ модели. Пустой ключ — самая частая причина
    // «всё зелёное, а агент молчит»: в снимок состояния он не попадает, и
    // фраза состояния сказать про него не может.
    note: () => noKeyNote + mode.note(),
    collect(out: Config): string {
        out.model = keepBlock(out.model, { framework: "openai" });
        // Блок relay СЛИВАЕТСЯ, а не пересобирается: кроме enabled/port в нём
        // живут ручки, которых этот экран не знает (relay.instructions), и
        // `delete out.relay` стирал их при первом же «Сохранить».
        const relayBlock = { ...(draft.relay || {}) };
        const keys = { ...keyStore };
        if (provider === "api") {
          out.model.base_url = apiDraft.base_url.trim();
          out.model.model = apiDraft.model.trim();
          out.model.key = apiDraft.key.trim();
          keys.api = out.model.key;
        } else if (provider === "local") {
          out.model.base_url = localDraft.base_url.trim();
          out.model.model = localDraft.model.trim();
          // Заглушка, а не пустая строка: ядро без ключа не создаёт клиента
          // вообще (live/llm.py: `if not key: return None`) и агент молчит на
          // каждый ход. Ollama и LM Studio Authorization игнорируют. Ровно это
          // кладёт установщик (setup/src/install.rs, ветка "local"); пустая
          // строка здесь была третьим расхождением двух писателей одного файла.
          out.model.key = "local";
        } else if (provider === "anthropic") {
          out.model.framework = "anthropic";
          out.model.base_url = anthDraft.base_url.trim();
          out.model.model = anthDraft.model.trim();
          out.model.key = anthDraft.key.trim();
          keys.anthropic = out.model.key;
        } else {
          // БЕЗ «/v1»: клиент ядра приклеивает к base_url «/chat/completions», а
          // у реле есть именно этот маршрут — /v1/chat/completions у него нет
          // вовсе. Установщик пишет ровно этот адрес (setup/src/install.rs, тест
          // relay_base_url_has_no_v1); окно возвращало «/v1» обратно, и первое же
          // «Сохранить» уводило каждый ход в 404 при зелёной кнопке «Проверить».
          // Порт — ИЗ КОНФИГА, не из константы: установщик мог выбрать не 5011
          // (см. relayBaseUrl в ../relay). Константа здесь возвращала 5011 и
          // уводила мозг в чужое реле при первом же «Сохранить».
          const relayPort = Number(relayBlock.port) || RELAY_PORT;
          out.model.base_url = relayBaseUrl(relayPort);
          out.model.model = chatgptDraft.model.trim() || "gpt-5.6-sol";
          // Ключ петли к реле держим постоянным: при уходе с подписки и возврате
          // здесь генерировался НОВЫЙ sk-frame-…, и живое реле, читавшее прежний
          // при старте, начинало отвечать 401.
          const relayKey = String(out.model.key || "").startsWith("sk-frame-")
            ? String(out.model.key)
            : keys.chatgpt && keys.chatgpt.startsWith("sk-frame-")
              ? keys.chatgpt
              : newRelayKey();
          out.model.key = relayKey;
          keys.chatgpt = relayKey;
          out.relay = { ...relayBlock, enabled: true, port: relayPort };
        }
        // Не подписка: реле не поднимаем, но блок не сносим — иначе вместе с ним
        // исчезнут ручки, которых этот экран не знает.
        if (provider !== "chatgpt") {
          if (Object.keys(relayBlock).length) out.relay = { ...relayBlock, enabled: false };
          else delete out.relay;
        }
        // Ключи неактивных провайдеров переживают смену вкладки.
        out.model.keys = keys;
        // Чужая ступень не уезжает: у провайдера без селектора поле снимается.
        const effortOut = clampEffort(provider, modelForEffort(), effort);
        if (effortOut) out.model.reasoning_effort = effortOut;
        else delete out.model.reasoning_effort;
        // Запасная и зрячая модели: пустое поле — снять ручку, а не записать "".
        if (fallbackModel.trim()) out.model.fallback_model = fallbackModel.trim();
        else delete out.model.fallback_model;
        if (visionModel.trim()) out.model.vision_model = visionModel.trim();
        else delete out.model.vision_model;
        // Telegram: токен без числового id — молчащий бот, а не «почти готово».
        // Гейт харнесса пускает ход только от владельца (telegram.allow_from),
        // и при owner_id = 0 не проходит НИКТО: бот в окне числится включённым,
        // шапка говорит «На связи», а на любое сообщение он молчит. Визард сюда
        // не пускает (setup/ui/src/scenes/keys.ts) — окно молча клало ноль.
        const ownerIdRaw = String(out.telegram?.owner_id ?? "").trim();
        const botToken = String(out.telegram?.bot_token || "").trim();
        if (botToken && !ownerIdOk(ownerIdRaw)) {
          return "Для Telegram нужен и твой id: число. Без него бот включится и будет молчать на всё — "
                 + "ход разрешён только владельцу. Узнать id: напиши @userinfobot в Telegram, он ответит числом.";
        }
        out.telegram = keepBlock(out.telegram, { owner_id: ownerIdRaw ? Number(ownerIdRaw) || 0 : 0, mode: tgMode });
        // Ограда — в `agent_mode`, галочки службы — в `service`. Ключ `mode`
        // (местожительство харнесса: local | remote) не трогаем ни при каких
        // обстоятельствах: режим, записанный туда, оставляет окно без харнесса, а
        // службу — без старта.
        //
        // ⚠ `mode.name()` отдаёт ТОЛЬКО ограду: "sandbox" или "interactive".
        // Слово "service" в этот ключ не пишется больше никогда — служба не режим,
        // и запись её сюда как раз и снимала ограду молча.
        const picked = mode.name();
        if (picked) {
          out[MODE_KEY] = picked;
          // Две РАЗНЫЕ галочки, а не одна: `session0` — доступ агента к правам
          // системы (умолчание нет), `firewall` — ставит ли служба правило для
          // кнопки «Телефон» (умолчание да). На общем ключе кнопка «Телефон» под
          // службой была мертва, пока владелец не отдаст агенту права системы.
          out.service = keepBlock(out.service, { session0: mode.session0(), firewall: mode.firewall() });
        }
        // Ограду выставляет режим — той же раскладкой, что `modes.apply` делает
        // перед `fence.install`. Пишем её здесь, а не оставляем руннеру, чтобы
        // файл не противоречил сам себе между «Сохранить» и следующим стартом:
        // владелец открывает helene.json и видит выбранную ограду и согласную с
        // ней раскладку, а не расхождение, о котором харнесс потом напишет в
        // журнал.
        //
        // ⚠⚠ БЛОК СЛИВАЕТСЯ. Здесь стояла пересборка `{ enabled, network }`, и она
        // стирала `sandbox.mounts` и `sandbox.mounts_denied`: вся работа по
        // монтированию — и список папок, и записанные отказы владельца — умирала
        // при первом же «Сохранить», молча. Тот же класс, что был с `relay`.
        out.sandbox = keepBlock(out.sandbox, {
          enabled: picked ? mode.sandbox() : draft.sandbox?.enabled !== false,
          network: draft.sandbox?.network !== false,
          // Списки пишет карточка монтирования — она их и читала из этого же
          // блока, так что здесь они не появляются из ниоткуда, а возвращаются.
          mounts: mounts.mounts(),
          mounts_denied: mounts.denied(),
        });
        // Управление компьютером: два ключа от карточки, `port` и прочее — как
        // лежали. Права харнесс перечитывает на ходу, включение — перезапуском.
        out.computer = keepBlock(out.computer, { enabled: computer.enabled(), scopes: computer.scopes() });
        // Голос: три ручки владельца, остальное (язык, потоки, тип счёта) — как
        // лежало. Модель качается отдельно и в конфиг не пишется: в нём стоит
        // ВЫБОР, а что скачано — знает дерево.
        out.voice = keepBlock(out.voice, {
          enabled: voice.enabled(),
          model: voice.model(),
          keep_loaded: voice.keepLoaded(),
          // Речь — свои две ручки в том же блоке: слух и голос настраиваются
          // одной карточкой, но включаются порознь.
          speak: voice.speak(),
          voice: voice.speakVoice(),
        });
      const needKey = provider === "api" || provider === "anthropic";
      noKeyNote = needKey && !String(out.model?.key || "").trim()
        ? " Ключ модели не задан — агент будет молчать."
        : "";
      return "";
    },
  };
}
