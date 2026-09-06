// Настройки экраном, а не файлом: имена, модель с живой проверкой, подписка
// ChatGPT с состоянием входа, Telegram, ограда рук со службой, монтирование,
// автозапуск, тема, папка данных. Пишет helene.json через оболочку; применяется
// перезапуском.
//
// ⚠⚠ ПРАВИЛО ЭТОГО ЭКРАНА: БЛОКИ КОНФИГА СЛИВАЮТСЯ, А НЕ ПЕРЕСОБИРАЮТСЯ.
// Владелец правит helene.json ещё и руками, а харнесс кладёт туда своё — всё,
// чего этот экран не знает, обязано пережить «Сохранить». Дважды пересборка
// уже стоила живых данных: сперва `relay.instructions` (23 КБ чужого промпта
// возвращались агенту), потом `sandbox.mounts` — вся работа по монтированию
// обнулялась одним кликом. Поэтому объектные блоки пишутся только через
// `keepBlock` (см. ниже), и на этом стоит тест app/test/config-blocks.test.mjs.
import { api, cfg, inTauri, post, shell } from "../api";
import { ANTHROPIC_PRESETS, BILLING_LABEL, clampEffort, effortPlan } from "../../../ui-kit/providers";
import { keepBlock } from "../config";
import QRCode from "qrcode";
import { bindFail, el, esc, failHTML, humanError, q, toast } from "../lib";
import { button, card, chips, field, saveTheme, setField, toggle, type Theme } from "../../../ui-kit/dom";
import { computerCard, storedComputer } from "../computer";
import { MODE_KEY, loadMode, modeCard, type ModeState } from "../mode";
import { mountsCard, type LiveSandbox } from "../mounts";
import { RELAY_PORT, relayBaseUrl, relayProbeUrl, newRelayKey } from "../relay";
import { S } from "../state";

interface Config {
  agent?: { name?: string };
  phone?: { enabled?: boolean };
  update?: { url?: string };
  owner?: { name?: string; room?: string };
  model?: { framework?: string; base_url?: string; model?: string; key?: string; keys?: Record<string, string>; max_tokens?: number; reasoning_effort?: string };
  // `instructions` экран не показывает, но обязан сохранить: этой ручкой
  // оболочка гасит 23 КБ чужого системного промпта Codex CLI перед конституцией
  // (shell/src/main.rs, RELAY_INSTRUCTIONS). Раньше блок relay пересобирался
  // заново, и ручка исчезала при первом же «Сохранить».
  relay?: { enabled?: boolean; port?: number; instructions?: string; [k: string]: unknown };
  telegram?: { bot_token?: string; owner_id?: number | string; mode?: string; api_id?: string | number; api_hash?: string; phone?: string };
  // ⚠ `mounts` и `mounts_denied` этот экран ТОЖЕ пишет (карточка «Монтирование»),
  // а `[k: string]` держит и то, чего он не знает: блок обязан СЛИВАТЬСЯ при
  // сохранении, иначе список смонтированных папок исчезает при первом же клике.
  sandbox?: { enabled?: boolean; network?: boolean; mounts?: unknown; mounts_denied?: unknown; [k: string]: unknown };
  // Режим агента — ОГРАДА РУК и только она: sandbox | interactive. Значение
  // "service" здесь больше не пишется никем: служба — не режим, а опция поверх
  // любой ограды (см. ../mode). Ключ именно `agent_mode` — `mode` в этом файле
  // занят под местожительство харнесса (local | remote), и режим, записанный
  // туда, выключает и окно, и службу.
  // Форму `{name: …}` конфиг тоже допускает; окно пишет строкой.
  agent_mode?: string | { name?: string; [k: string]: unknown };
  // Блок службы. `session0` — доступ агента к правам системы (умолчание false),
  // `firewall` — ставит ли служба правило брандмауэра для телефона (умолчание
  // true). Это два РАЗНЫХ вопроса: на одном ключе кнопка «Телефон» под службой
  // была мертва, пока владелец не отдаст агенту права системы. Оба лежат ровно
  // там, где их читает служба (svc/src/main.rs::load_plan).
  service?: { session0?: boolean; firewall?: boolean; [k: string]: unknown };
  // Управление компьютером: `enabled` (перезапуском) и четыре права `scopes`
  // (харнесс перечитывает на ходу). `port` и всё прочее — не наше, блок
  // СЛИВАЕТСЯ через keepBlock, как и остальные.
  computer?: { enabled?: boolean; scopes?: unknown; port?: number; [k: string]: unknown };
  installed?: { service?: boolean; [k: string]: unknown };
  // Местожительство харнесса: `local` — дети окна; `remote` — окно ходит в
  // трубу на сервере по `base` и `key` (server/README-СЕРВЕР.md). Это НЕ
  // режим агента — тот в `agent_mode`.
  mode?: string;
  base?: string;
  key?: string;
  [k: string]: unknown;
}

interface Loaded {
  config: Config;
  path: string;
  tree: string;
  exe_dir: string;
  /** Отпечаток файла на момент чтения (КОНТРАКТ-B→A §2); старая оболочка его не шлёт. */
  mtime_ns?: string | number;
}

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

export async function render(container: HTMLElement): Promise<void> {
  if (!inTauri) {
    const center = el("div", "center");
    // Тумблер телефона в вебе был пустышкой: черновик выбрасывался в мусор,
    // кнопки «Сохранить» в этой ветке нет вовсе — человек щёлкал, и ничего не
    // происходило, и никто не говорил, что не происходит.
    center.append(themeCard(), el("div", "card muted", "Остальные настройки доступны в приложении Hélène на том компьютере, где живёт агент: здесь окно смотрит на удалённый харнесс."));
    mountSettings(container, center);
    bindTheme(container);
    return;
  }
  let loaded: Loaded;
  try {
    loaded = await shell<Loaded>("config_load");
  } catch (e) {
    container.innerHTML = failHTML(e);
    bindFail(container, () => void render(container));
    return;
  }
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
  const c = loaded.config;
  const draft: Config = JSON.parse(JSON.stringify(c));
  draft.agent = draft.agent || {};
  draft.owner = draft.owner || {};
  draft.model = draft.model || {};
  draft.telegram = draft.telegram || {};
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
  const center = el("div", "center");

  // --- имена
  const names = el("div", "form-grid two");
  names.append(
    field("Имя агента", String(draft.agent.name || ""), (v) => (draft.agent!.name = v)),
    field("Твоё имя", String(draft.owner.name || ""), (v) => (draft.owner!.name = v)),
  );
  // Настройки пишут только helene.json. Конституцию (data/soul/SOUL.md) не
  // переписывает никто, кроме установщика, — а в ней старые имена остаются
  // навсегда, и агент в своём K-слое читает именно их. Обещать обратное нельзя.
  center.append(card("Имена", names,
    "Имя агента войдёт в подписи и в снимок состояния; имя владельца нужно агенту, чтобы знать, чьё слово решает. " +
    "Конституция агента этим не меняется: имена в ней он написал при рождении, и правит их только он сам или ты руками — " +
    "раздел «Файлы», soul/SOUL.md."));

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
  model.append(pick, panes.api, panes.anthropic, panes.chatgpt, panes.local, effortRow, effortHint);
  syncPick();
  syncEffort();
  center.append(card("Модель", model));

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
  center.append(card("Telegram", tgBox));

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
  center.append(mode.el);

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
  center.append(card("Песочница", sb, "Что вышло на самом деле — видно на экране «Система». Применяется перезапуском."));

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
  center.append(mounts.el);

  // --- управление компьютером: опция ПОВЕРХ любого режима (06.09)
  //
  // Тело руки `computer` живёт снаружи ограды, поэтому карточка не прячется
  // ни в одном режиме. Тексты и четыре права — из трубы (`computer_option`),
  // живое состояние тела — из снимка харнесса (`computer_live`); окно
  // пишет ровно два ключа блока и сливает остальное.
  const computer = computerCard(modeLive, storedComputer(draft.computer));
  center.append(computer.el);

  center.append(phoneCard(draft, !!c.phone?.enabled));

  // --- перенос: экспорт агента одним архивом и окно к харнессу на сервере
  center.append(transferCard(draft));

  // --- автозапуск
  const auto = el("div");
  const autoToggle = toggle("Запускать при входе в Windows", false, async (v) => {
    try {
      await shell("autostart_set", { on: v });
      toast(v ? "Автозапуск включён" : "Автозапуск выключен");
    } catch (e) {
      toast(humanError(e).text);
    }
  });
  shell<boolean>("autostart_get").then((v) => autoToggle.setAttribute("aria-checked", String(v))).catch(() => {});
  auto.append(autoToggle);
  center.append(card("Автозапуск", auto));

  // --- тема
  center.append(themeCard());

  // --- данные
  const data = el("div", "actions");
  data.append(
    el("span", "mono", loaded.tree),
    button("Открыть папку", "quiet", () => void shell("open_path", { path: loaded.tree }).catch((e) => toast(humanError(e).text))),
  );
  center.append(card("Данные агента", data, "Память, дневник, конституция и настройки лежат здесь. Перенос агента на другую машину — перенос этой папки вместе с программой."));

  // --- о программе
  draft.update = draft.update || {};
  const about = el("div");
  const aboutRow = el("div", "actions");
  const ver = el("span", "mono", "версия …");
  let aboutInfo: { version: string; exe_dir: string; log: string } | null = null;
  shell<{ version: string; exe_dir: string; log: string }>("app_info")
    .then((i) => {
      aboutInfo = i;
      ver.textContent = `версия ${i.version}`;
    })
    .catch(() => (ver.textContent = "версия видна в окне программы"));
  const updOut = el("span", "receipt");
  // Кнопка создаётся один раз и переключается. Раньше её добавляли внутрь
  // обработчика: три нажатия «Проверить обновления» — три кнопки «Скачать» в
  // ряд, и она оставалась висеть даже рядом с «Это последняя версия».
  let updUrl = "";
  // Скачать и поставить — оболочка (КОНТРАКТ-B→A §3: update_download →
  // update_install). Пока этих команд у оболочки нет, кнопка честно открывает
  // ссылку на выпуск, как раньше, и говорит об этом.
  const dlBtn = button("Скачать и установить", "primary", async () => {
    if (!updUrl) return;
    dlBtn.disabled = true;
    updOut.className = "receipt";
    updOut.textContent = "Скачиваю…";
    try {
      const got = await shell<{ path: string; sha_ok: boolean; sha_expected?: string; sha_actual?: string }>("update_download", { url: updUrl });
      if (!got.sha_ok) {
        updOut.className = "receipt err";
        updOut.textContent = `Архив скачан, но его отпечаток не сошёлся с заметками выпуска — ставить не буду. Файл: ${got.path}`;
        return;
      }
      updOut.textContent = "Скачано, отпечаток сошёлся. Запускаю установщик — программа закроется сама.";
      await shell("update_install", { path: got.path });
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (/not found|неизвестн|unknown|command/i.test(text)) {
        updOut.textContent = "Эта версия оболочки ещё не умеет ставить обновление сама — открыл страницу выпуска, скачай и запусти helene-setup.exe.";
        void shell("open_path", { path: updUrl }).catch((e2) => toast(humanError(e2).text));
      } else {
        updOut.className = "receipt err";
        updOut.textContent = humanError(e).text;
      }
    } finally {
      dlBtn.disabled = false;
    }
  });
  dlBtn.hidden = true;
  aboutRow.append(
    ver,
    button("Проверить обновления", "quiet", async () => {
      updOut.className = "receipt";
      updOut.textContent = "спрашиваю…";
      try {
        const r = await shell<{ current: string; latest: string; newer: boolean; url: string; notes: string }>("update_check", {
          url: String(draft.update?.url || ""),
        });
        if (r.newer) {
          updOut.className = "receipt ok";
          updOut.textContent = `Есть версия ${r.latest}. ${r.notes || ""}`.trim();
          updUrl = r.url || "";
          dlBtn.hidden = !updUrl;
        } else {
          updOut.textContent = `Это последняя версия (${r.current}).`;
          updUrl = "";
          dlBtn.hidden = true;
        }
      } catch (e) {
        updOut.className = "receipt err";
        updOut.textContent = humanError(e).text;
        updUrl = "";
        dlBtn.hidden = true;
      }
    }),
    dlBtn,
    updOut,
  );
  const logsRow = el("div", "actions");
  logsRow.append(
    button("Собрать логи для поддержки", "quiet", async () => {
      try {
        const p = await shell<string>("logs_bundle");
        toast("Логи собраны: " + p);
        await shell("reveal_path", { path: p });
      } catch (e) {
        toast(humanError(e).text);
      }
    }),
    button("Открыть helene.log", "quiet", () => {
      if (aboutInfo) void shell("open_path", { path: aboutInfo.log }).catch((e) => toast(humanError(e).text));
    }),
  );
  about.append(
    aboutRow,
    field("Адрес обновлений", String(draft.update?.url || ""), (v) => (draft.update!.url = v), {
      mono: true,
      placeholder: "https://api.github.com/repos/<владелец>/helene/releases/latest",
      hint: "Адрес выпусков на GitHub (…/releases/latest) или свой JSON с полями version, url и notes. Программа только сообщает о новой версии и даёт ссылку, сама ничего не подменяет.",
    }),
    logsRow,
  );
  center.append(card("О программе", about));

  // --- сохранить
  const save = el("div", "actions");
  const saveOut = el("span", "receipt");
  save.append(
    button("Сохранить", "primary", async () => {
      const out: Config = JSON.parse(JSON.stringify(draft));
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
      // Telegram: токен без числового id — молчащий бот, а не «почти готово».
      // Гейт харнесса пускает ход только от владельца (telegram.allow_from),
      // и при owner_id = 0 не проходит НИКТО: бот в окне числится включённым,
      // шапка говорит «На связи», а на любое сообщение он молчит. Визард сюда
      // не пускает (setup/ui/src/scenes/keys.ts) — окно молча клало ноль.
      const ownerIdRaw = String(out.telegram?.owner_id ?? "").trim();
      const botToken = String(out.telegram?.bot_token || "").trim();
      if (botToken && !ownerIdOk(ownerIdRaw)) {
        saveOut.className = "receipt err";
        saveOut.textContent =
          "Для Telegram нужен и твой id: число. Без него бот включится и будет молчать на всё — ход разрешён только владельцу. Узнать id: напиши @userinfobot в Telegram, он ответит числом.";
        return;
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
      out.phone = keepBlock(out.phone, { enabled: !!draft.phone?.enabled });
      // Управление компьютером: два ключа от карточки, `port` и прочее — как
      // лежали. Права харнесс перечитывает на ходу, включение — перезапуском.
      out.computer = keepBlock(out.computer, { enabled: computer.enabled(), scopes: computer.scopes() });
      // Местожительство харнесса (карточка «Перенос»): три скаляра. `remote`
      // без адреса — это окно без харнесса, поэтому пустой адрес = `local`.
      const remoteBase = String(draft.base || "").trim();
      const remoteOn = draft.mode === "remote" && !!remoteBase;
      out.mode = remoteOn ? "remote" : "local";
      if (remoteBase) out.base = remoteBase;
      else delete out.base;
      const remoteKey = String(draft.key || "").trim();
      if (remoteKey) out.key = remoteKey;
      else delete out.key;
      out.setup_complete = true;
      try {
        await writeConfig(out);
        saveOut.className = "receipt ok";
        // Под службой перезапуск ОКНА настройки не применит: службу конфиг
        // читает один раз при своём старте. Раньше расписка обещала обратное.
        let svc = "";
        try {
          svc = await shell<string>("service_state");
        } catch {
          // не смогли спросить — говорим общее
        }
        // Пустой ключ — самая частая причина «всё зелёное, агент молчит»:
        // фраза состояния про него сказать не может, ключ в снимок не попадает.
        const needKey = provider === "api" || provider === "anthropic";
        const noKey = needKey && !String(out.model.key || "").trim()
          ? " Ключ модели не задан — агент будет молчать."
          : "";
        // Хвост про режим — от карточки: она знает, что осталось сделать
        // (поставить или снять службу), а расписка не имеет права молчать об этом.
        const modeNote = mode.note();
        if (svc === "running") {
          saveOut.textContent = "Сохранено. Агента держит служба Windows: чтобы настройки применились, сними и поставь её заново (карточка «Режим» выше)." + noKey + modeNote;
          restartBtn.hidden = true;
        } else {
          saveOut.textContent = "Сохранено. Чтобы применить, перезапусти программу." + noKey + modeNote;
          restartBtn.hidden = false;
        }
        S.agent = String(out.agent?.name || S.agent);
      } catch (e) {
        if (e instanceof StaleConfig) {
          // Файл менял кто-то ещё (установщик, харнесс, Блокнот). Развилка
          // вместо молчаливой перезаписи — как у /api/md.
          saveOut.className = "receipt err";
          saveOut.textContent = "Файл настроек изменился, пока экран был открыт.";
          conflictBox.hidden = false;
          return;
        }
        saveOut.className = "receipt err";
        saveOut.textContent = humanError(e).text;
      }
    }),
    saveOut,
  );
  // Свежесть: отпечаток файла из config_load едет обратно в config_save; при
  // расхождении оболочка отвечает `stale:<mtime>` (КОНТРАКТ-B→A §2). Старая
  // оболочка отпечатка не шлёт — тогда пишем как раньше.
  let seenMtime: string | undefined = loaded.mtime_ns != null ? String(loaded.mtime_ns) : undefined;
  class StaleConfig extends Error {}
  const writeConfig = async (out: Config, force = false) => {
    const args: Record<string, unknown> = { config: JSON.stringify(out) };
    if (seenMtime && !force) args.mtimeNs = seenMtime;
    try {
      const r = await shell<{ ok?: boolean; mtime_ns?: string | number } | null>("config_save", args);
      if (r && typeof r === "object" && r.mtime_ns != null) seenMtime = String(r.mtime_ns);
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (/^stale:/.test(text)) throw new StaleConfig(text);
      throw e;
    }
  };
  const conflictBox = el("div");
  conflictBox.hidden = true;
  conflictBox.innerHTML = `<div class="notice err" style="margin-top:12px"><span class="dot failed"></span>
    <span>Пока настройки были открыты, helene.json изменил кто-то ещё — установщик, харнесс или ты в Блокноте. Если сохранить как есть, его правка пропадёт.</span></div>`;
  const conflictRow = el("div", "actions");
  conflictRow.style.marginTop = "10px";
  conflictRow.append(
    button("Перечитать (мои правки потеряются)", "primary", () => void render(container)),
    button("Перезаписать своим", "quiet", async () => {
      conflictBox.hidden = true;
      try {
        await writeConfig(JSON.parse(JSON.stringify(draft)), true);
        saveOut.className = "receipt ok";
        saveOut.textContent = "Перезаписано. Чтобы применить, перезапусти программу.";
        restartBtn.hidden = false;
      } catch (e) {
        saveOut.className = "receipt err";
        saveOut.textContent = humanError(e).text;
      }
    }),
  );
  conflictBox.append(conflictRow);
  const restartBtn = button("Перезапустить сейчас", "quiet", () => dispatchEvent(new Event("frame-restart")));
  restartBtn.hidden = true;
  save.append(restartBtn);
  const saveCard = el("section", "card save-bar");
  saveCard.append(save, conflictBox, el("p", "field-hint", `Файл настроек: ${loaded.path}`));
  center.append(saveCard);

  mountSettings(container, center);
  bindTheme(container);
}

/**
 * Экран настроек — карточки и оглавление слева. Оглавление собирается из
 * заголовков карточек: ни одной второй копии списка.
 */
function mountSettings(container: HTMLElement, center: HTMLElement) {
  const wrap = el("div", "settings");
  const nav = el("nav", "settings-nav");
  nav.setAttribute("aria-label", "Разделы настроек");
  const body = el("div", "settings-body");
  center.className = "";
  body.append(center);
  const cards = [...center.querySelectorAll<HTMLElement>("section.card")].filter((c) => c.querySelector(":scope > h3"));
  const links: HTMLAnchorElement[] = [];
  cards.forEach((c, i) => {
    const title = c.querySelector(":scope > h3")!.textContent || "";
    c.id = "s-" + i;
    const a = el("a", "", title);
    a.href = "#" + c.id;
    a.addEventListener("click", (e) => {
      e.preventDefault();
      c.scrollIntoView({ block: "start", behavior: "smooth" });
      for (const l of links) l.setAttribute("aria-current", String(l === a));
    });
    links.push(a);
    nav.append(a);
  });
  wrap.append(nav, body);
  container.replaceChildren(wrap);
  if ("IntersectionObserver" in window && cards.length) {
    const seen = new Map<Element, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target, e.isIntersecting);
        const first = cards.find((c) => seen.get(c));
        if (!first) return;
        for (const l of links) l.setAttribute("aria-current", String(l.getAttribute("href") === "#" + first.id));
      },
      { root: container, rootMargin: "-10% 0px -70% 0px" },
    );
    for (const c of cards) io.observe(c);
  }
}

/**
 * Карточка «Перенос»: экспорт агента одним архивом и подключение окна к
 * харнессу на сервере (server/README-СЕРВЕР.md).
 *
 * Экспорт — команда оболочки `carry_export` (app/localharness/carry.py):
 * data/ с личным git, helene.json с ключами, паспорт. Импорта из окна нет
 * намеренно: подменять data/ под живым харнессом нельзя — это делают из
 * консоли при закрытой программе, и карточка говорит как.
 *
 * Удалённый харнесс — три скаляра конфига: `mode` (local|remote), `base`,
 * `key`. Пишутся общей кнопкой «Сохранить», применяются перезапуском: с
 * `remote` оболочка своих детей не поднимает и ходит в чужую трубу.
 */
function transferCard(draft: Config): HTMLElement {
  const box = el("div");
  const exportOut = el("span", "receipt");
  const exportBtn = button("Экспорт агента", "quiet", async () => {
    exportOut.className = "receipt";
    exportOut.textContent = "Собираю архив…";
    try {
      const path = await shell<string>("carry_export");
      exportOut.className = "receipt ok";
      exportOut.textContent = `Готово: ${path}`;
      toast("Архив переноса собран");
      await shell("reveal_path", { path }).catch(() => {});
    } catch (e) {
      exportOut.className = "receipt err";
      exportOut.textContent = humanError(e).text;
    }
  });
  const exportRow = el("div", "actions");
  exportRow.append(exportBtn, exportOut);
  box.append(
    exportRow,
    el(
      "p",
      "field-hint",
      "Архив — вся папка данных агента (память, конституция, навыки, личный git, вход ChatGPT, сессия Telegram) и helene.json. " +
        "Внутри ключ модели и токены — не для пересылки посторонним; паспорт helene-carry.json в архиве перечисляет их поимённо. " +
        "Не едут: тело руки computer, журналы, ключ окна, стыки смонтированных папок. " +
        "Обратный импорт на этом ПК — из консоли при закрытой программе: runtime\\python.exe app\\localharness\\carry.py import --config helene.json --archive <архив>; прежняя data/ останется рядом как data.before-<штамп>.",
    ),
  );

  // --- окно к харнессу на сервере
  const remote = el("div");
  remote.style.marginTop = "12px";
  const remoteToggle = toggle("Окно ходит к харнессу на сервере", draft.mode === "remote", (v) => {
    draft.mode = v ? "remote" : "local";
    syncRemote();
  });
  const baseField = field("Адрес канала на сервере", String(draft.base || ""), (v) => (draft.base = v), {
    mono: true,
    placeholder: "https://helene.example.com",
    hint: "Тот адрес, по которому Caddy или Tailscale отдаёт канал контейнера (server/docker-compose.yml слушает 127.0.0.1:8094 хоста).",
  });
  const keyField = field("Ключ окна", String(draft.key || ""), (v) => (draft.key = v), {
    type: "password",
    mono: true,
    hint: "Строка ключа печатается при старте контейнера: docker logs helene | head. Тот же ключ — у телефона.",
  });
  const remoteNote = el("p", "field-hint");
  const syncRemote = () => {
    const on = draft.mode === "remote";
    baseField.hidden = !on;
    keyField.hidden = !on;
    remoteNote.textContent = on
      ? "После сохранения и перезапуска оболочка своих детей не поднимает: агент живёт на сервере, окно и телефон ходят туда. Пустой адрес — это снова local."
      : "Сейчас агент живёт на этой машине: канал и харнесс поднимает окно. Перенос на сервер — экспорт выше, затем server/README-СЕРВЕР.md в поставке.";
  };
  remote.append(remoteToggle, baseField, keyField, remoteNote);
  syncRemote();
  box.append(remote);
  return card("Перенос", box, "Применяется перезапуском.");
}

function phoneCard(draft: Config, savedEnabled = false): HTMLElement {
  draft.phone = draft.phone || {};
  const phone = el("div");
  const phoneToggle = toggle("Разрешить подключение телефона по сети", !!draft.phone.enabled, (v) => {
    draft.phone!.enabled = v;
    syncPhone();
  });
  // Честно про шифрование: соединение идёт открытым текстом по http://, и в
  // общей Wi-Fi (кафе, отель, коворкинг) ключ устройства и вся переписка с
  // агентом видны соседям. Прежняя подсказка обещала «доступ только по ключу»
  // и про отсутствие шифрования молчала.
  const phoneHint = el("p", "field-hint", "Канал начнёт слушать сеть, а не только эту машину. Внимание: соединение НЕ шифруется (обычный http). В чужой или общей Wi-Fi — кафе, отель, коворкинг — ключ телефона и переписка с агентом идут открытым текстом, их видно соседям по сети. Дома в своей сети это приемлемо; в любой другой пользуйся Tailscale: поставь его на компьютер и телефон, войди в один аккаунт, и QR даст его адрес. Включение применяется перезапуском.");
  const qrRow = el("div", "actions");
  qrRow.style.marginTop = "12px";
  const qrWhy = el("span", "receipt");
  const qrOut = el("div", "qr-out");
  qrOut.hidden = true;
  const devicesBox = el("div", "devices");
  const drawDevices = async () => {
    let rows: Array<{ id: string; name: string; created: string }> = [];
    try {
      rows = await api("/pair/devices");
    } catch {
      rows = [];
    }
    devicesBox.replaceChildren();
    if (!rows.length) return;
    devicesBox.append(el("p", "field-label", "Подключённые устройства"));
    for (const d of rows) {
      const row = el("div", "device-row");
      row.append(el("span", "", `${d.name} · ${new Date(d.created).toLocaleDateString("ru-RU")}`));
      row.append(button("Отвязать", "quiet", async () => {
        await post("/pair/revoke", { id: d.id }).catch((e) => toast(humanError(e).text));
        void drawDevices();
      }));
      devicesBox.append(row);
    }
  };
  const qrBtn = button("Показать QR", "quiet", async () => {
    try {
      const pair = await post<{ path: string; expires_in: number; uses: number }>("/pair/new", {});
      const port = new URL(cfg.base || "http://127.0.0.1:8094").port || "8094";
      // Оба адреса, а не выбор за владельца: Tailscale мог быть запущен для
      // других дел, а телефон в тайлнет не добавлен — тогда QR со 100.x.y.z
      // ведёт туда, куда телефон не дойдёт, и локальный адрес не предлагался
      // никогда.
      const addrs: Array<{ host: string; note: string }> = [];
      if (inTauri) {
        const ts = await shell<string | null>("tailscale_ip").catch(() => null);
        const lan = await shell<string | null>("lan_ip").catch(() => null);
        if (lan) addrs.push({ host: lan + ":" + port, note: "В этой Wi-Fi: телефон должен быть в той же сети." });
        if (ts) addrs.push({ host: ts + ":" + port, note: "Через Tailscale: телефон с Tailscale в том же аккаунте достучится из любой сети." });
      }
      if (!addrs.length) addrs.push({ host: location.host, note: "" });
      const blocks: string[] = [];
      for (const a of addrs) {
        const link = "http://" + a.host + pair.path;
        // Белый фон модулей задан явно: карточка .qr и так белая, но так код
        // остаётся читаемым камерой, даже если карточку когда-нибудь затемнят.
        const svg = await QRCode.toString(link, { type: "svg", margin: 1, width: 240, color: { dark: "#262320", light: "#ffffff" } });
        blocks.push(`<div class="qr-pair"><div class="qr">${svg}</div>
          <div class="qr-text">${a.note ? `<p><b>${esc(a.note)}</b></p>` : ""}
            <p class="mono qr-url">${esc(link)}</p></div></div>`);
      }
      qrOut.hidden = false;
      qrOut.innerHTML = blocks.join("") +
        `<div class="qr-text">
          <p>Открой камеру телефона и наведи на код. Ссылка живёт десять минут и годится дважды.</p>
          <p><b>iPhone:</b> страница откроется в Safari; нажми «Поделиться» → «На экран „Домой“». Второе открытие из значка допишет ключ, поэтому код и двухразовый.</p>
        </div>`;
      if (inTauri) {
        const fw = await shell<string>("firewall_allow", { port: Number(port) }).catch((e) => humanError(e).text);
        toast(fw);
      }
    } catch (e) {
      qrOut.hidden = false;
      qrOut.innerHTML = failHTML(e, { retry: false });
      bindFail(qrOut);
    }
  });
  // Кнопка не смотрела ни на тумблер, ни на то, был ли перезапуск: труба всё
  // ещё слушала 127.0.0.1, владелец получал красивый QR на адрес, где никто не
  // отвечает, а телефон обвинял в этом Wi-Fi.
  const syncPhone = () => {
    const on = !!draft.phone?.enabled;
    qrBtn.disabled = !on || !savedEnabled;
    qrWhy.className = "receipt";
    qrWhy.textContent = !on
      ? "Включи тумблер, сохрани и перезапусти — тогда канал начнёт слушать сеть."
      : !savedEnabled
        ? "Сохрани и перезапусти программу: пока канал слушает только эту машину, и QR вёл бы туда, где никто не отвечает."
        : "";
  };
  qrRow.append(qrBtn, qrWhy);
  syncPhone();
  phone.append(phoneToggle, phoneHint, qrRow, qrOut, devicesBox);
  void drawDevices();
  return card("Телефон", phone);

}

function themeCard(): HTMLElement {
  const c = el("section", "card");
  c.append(el("h3", "", "Тема"));
  const row = el("div", "choice");
  row.id = "theme-choice";
  for (const [value, title, text] of [
    ["system", "Как в Windows", "днём светлая, ночью тёмная"],
    ["light", "Светлая", "всегда, самая светлая"],
    ["dark", "Тёмная", "всегда, тёплый уголь"],
  ]) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.dataset.value = value;
    b.append(el("span", "choice-title", title), el("span", "choice-text", text));
    row.append(b);
  }
  c.append(row);
  return c;
}

function bindTheme(container: HTMLElement) {
  const row = container.querySelector<HTMLElement>("#theme-choice");
  if (!row) return;
  const current = (() => {
    try {
      return localStorage.getItem("frame.theme") || "system";
    } catch {
      return "system";
    }
  })();
  const sync = (v: string) => {
    for (const b of row.querySelectorAll<HTMLButtonElement>(".choice-item")) b.setAttribute("aria-checked", String(b.dataset.value === v));
  };
  sync(current);
  for (const b of row.querySelectorAll<HTMLButtonElement>(".choice-item")) {
    b.addEventListener("click", () => {
      saveTheme(b.dataset.value as Theme);
      sync(b.dataset.value!);
      dispatchEvent(new Event("frame-theme"));
    });
  }
  q("#theme-choice", container);
}
