"use strict";
const $ = (id) => document.getElementById(id);
const escape = (s) =>
  String(s ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const papers = {
  cream: ["Сливочная", "#f8eee0"],
  white: ["Белая", "#fbf7f0"],
  kraft: ["Крафт", "#ecd4b4"],
  sage: ["Шалфей", "#e7ebdf"],
  sky: ["Небо", "#e6ecef"],
  coal: ["Уголь", "#1d1914"],
  cocoa: ["Какао", "#251d18"],
  graphite: ["Графит", "#1b1c1e"],
  navy: ["Ночь", "#151a22"],
  pine: ["Хвоя", "#161c18"],
};
const accents = {
  terra: ["Терракота", "#e0915a", "#9a4a1e", "#eda676"],
  ochre: ["Охра", "#d9a441", "#7a5610", "#e8c170"],
  sage: ["Шалфей", "#8fae8b", "#3f6a3b", "#a9c9a4"],
  plum: ["Слива", "#b07a9e", "#7a3f68", "#d3a3c4"],
  sea: ["Море", "#6f9fb5", "#2d5d73", "#9cc3d6"],
  cinnabar: ["Киноварь", "#d9674e", "#9a3420", "#ee9a86"],
};
const titles = {
  overview: ["Главная"],
  accounts: ["Подписки"],
  activity: ["События"],
  settings: ["Настройки"],
};
let state = null,
  busy = false,
  settingsPending = 0,
  revealed = false,
  lastAccounts = "",
  lastEvents = "",
  limits = {},
  identities = {},
  currentPage = "overview",
  lastQuotaRefresh = 0,
  toastTimer,
  saveQueue = Promise.resolve();
const systemTheme = matchMedia("(prefers-color-scheme: dark)");
const drafts = new Map(),
  inputTimers = new Map();
function problem(error) {
  const raw = String(error);
  let message =
    "Не удалось выполнить действие. Причина указана в подробностях.";
  if (/^[А-ЯЁ]/.test(raw)) message = raw;
  else if (/OpenAI usage endpoint returned (401|403)/.test(raw))
    message = "Сессия недействительна. Войди в эту подписку заново.";
  else if (/OpenAI usage request failed/.test(raw))
    message =
      "Не удалось получить квоты. Проверь соединение и попробуй ещё раз.";
  else if (/Python 3\.10\+|Windows Store aliases/.test(raw))
    message =
      "Для входа нужен рабочий Python 3.10 или новее. Установи Python или укажи RELAY_PYTHON.";
  else if (/Port \d+ is already in use/.test(raw))
    message = "Порт занят. Выбери другой в настройках подключения.";
  else if (
    /WinError 10048|Address already in use|address already in use/.test(raw)
  )
    message = "Порт входа занят. Заверши вход в другой программе и повтори.";
  else if (
    /not writable|Permission denied|Access is denied|Отказано в доступе|PermissionError/.test(
      raw,
    )
  )
    message =
      "Нет доступа к папке данных реле. Перенеси программу в доступную для записи папку.";
  else if (/Login timed out/.test(raw))
    message = "Время ожидания входа истекло. Начни заново.";
  else if (/login is already in progress/i.test(raw))
    message = "Заверши текущий вход в браузере или нажми «Отменить».";
  return `<div>${escape(message)}</div>${message === raw ? "" : `<details class="error-details"><summary>Технические подробности</summary><pre>${escape(raw)}</pre></details>`}`;
}
function notice(message) {
  $("notice").innerHTML = message ? problem(message) : "";
  $("notice").hidden = !message;
}
function toast(message) {
  clearTimeout(toastTimer);
  $("toast").textContent = message;
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 2200);
}
async function invoke(op, slot = null, port = null, settings = null) {
  return window.__TAURI__.core.invoke("relay_command", {
    op,
    slot,
    port,
    settings,
  });
}
async function windowAction(action) {
  try {
    if (action === "quit") await flushSettings();
    await window.__TAURI__.core.invoke("window_command", { action });
  } catch (error) {
    notice(String(error));
  }
}
function page(id) {
  if (!titles[id]) return;
  currentPage = id;
  for (const node of document.querySelectorAll(".page"))
    node.hidden = node.id !== id;
  for (const node of document.querySelectorAll("nav button")) {
    node.classList.toggle("selected", node.dataset.page === id);
    node.setAttribute(
      "aria-current",
      node.dataset.page === id ? "page" : "false",
    );
  }
  $("page-title").textContent = titles[id][0];
  document.querySelector("main").scrollTop = 0;
  try {
    localStorage.setItem("relay.page", id);
  } catch {}
}
function settingsTab(id) {
  for (const tab of document.querySelectorAll("[data-settings-tab]"))
    tab.setAttribute("aria-selected", String(tab.dataset.settingsTab === id));
  for (const panel of document.querySelectorAll(".settings-panel"))
    panel.hidden = panel.id !== "settings-" + id;
}
function appearance(a) {
  if (!a) return;
  const root = document.documentElement,
    dark = a.theme === "dark" || (a.theme === "system" && systemTheme.matches),
    accent = accents[a.accent] || accents.terra;
  root.dataset.dark = String(dark);
  root.style.setProperty(
    "--paper",
    papers[dark ? a.paper_dark : a.paper_light]?.[1] ||
      papers[dark ? "coal" : "cream"][1],
  );
  root.style.setProperty("--terra", accent[1]);
  root.style.setProperty("--accent", accent[dark ? 3 : 2]);
  root.style.setProperty("--font-size", (14 * a.scale) / 100 + "px");
  root.style.setProperty(
    "--hand",
    a.handwritten
      ? "Zen, 'Segoe Print', cursive"
      : "Golos, 'Segoe UI', sans-serif",
  );
  document.body.dataset.grain = String(a.grain);
  document.body.dataset.motion = String(!a.reduced_motion);
  document.body.dataset.density = a.density;
  for (const b of document.querySelectorAll("[data-swatch]"))
    b.setAttribute(
      "aria-pressed",
      String(a[b.dataset.setting.split(".")[1]] === b.dataset.swatch),
    );
}
function addSwatches(id, keys, path, palette, dark = false) {
  $(id).innerHTML = keys
    .map(
      (key) =>
        `<button class="swatch" data-setting="${path}" data-swatch="${key}" style="--swatch:${palette[key][1]};--swatch-ink:${dark ? "#f1e4d0" : "#1d1a17"}" aria-label="${palette[key][0]}" title="${palette[key][0]}" aria-pressed="false"></button>`,
    )
    .join("");
}
addSwatches(
  "paper-light",
  ["cream", "white", "kraft", "sage", "sky"],
  "appearance.paper_light",
  papers,
);
addSwatches(
  "paper-dark",
  ["coal", "cocoa", "graphite", "navy", "pine"],
  "appearance.paper_dark",
  papers,
  true,
);
addSwatches("accents", Object.keys(accents), "appearance.accent", accents);
systemTheme.addEventListener("change", () =>
  appearance(state?.settings?.appearance),
);
function quotaWindow(window, fallback) {
  if (!window || window.remaining_percent == null) return "";
  const seconds = window.limit_window_seconds ?? window.window_seconds;
  let label = fallback;
  if (seconds === 604800) label = "За неделю";
  else if (seconds === 86400) label = "За сутки";
  else if (seconds && seconds % 3600 === 0) label = `За ${seconds / 3600} ч`;
  const remaining = Math.max(
    0,
    Math.min(100, Number(window.remaining_percent)),
  );
  const reset = window.reset_at
    ? new Date(window.reset_at * 1000).toLocaleString("ru-RU", {
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  return `<div class="quota"><div class="quota-line"><span>${escape(label)}</span><strong>${Math.round(remaining)}% осталось</strong></div><progress aria-label="${escape(label)}: осталось ${Math.round(remaining)} процентов" max="100" value="${remaining}"></progress>${reset ? `<div class="quota-reset">Обновится ${escape(reset)}</div>` : ""}</div>`;
}
function quotaPanel(slot) {
  const result = limits[slot];
  if (!result)
    return '<p class="quota-note">Квоты появятся после проверки.</p>';
  if (result.loading) return '<p class="quota-note">Проверяем квоты…</p>';
  if (result.error)
    return `<div class="quota-error">${problem(result.error)}</div>`;
  let html =
    quotaWindow(result.rate_limit?.primary_window, "Основное окно") +
    quotaWindow(result.rate_limit?.secondary_window, "Дополнительное окно");
  for (const extra of result.additional_rate_limits || [])
    html +=
      `<div class="quota-note">${escape(extra.limit_name || extra.name || "Дополнительная квота")}</div>` +
      quotaWindow(extra.rate_limit?.primary_window, "Основное окно") +
      quotaWindow(extra.rate_limit?.secondary_window, "Дополнительное окно");
  if (!html)
    html =
      '<p class="quota-note">Провайдер не передал данные об окнах квот.</p>';
  return (
    html +
    `<div class="quota-note">Проверено ${escape(new Date(result._checked).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" }))}</div>`
  );
}
function accountCard(a, detailed) {
  const slot = a.slot,
    name = state.settings.labels[slot],
    button = (op, title, cls = "") =>
      `<button class="${cls}" data-op="${op}" data-slot="${slot}">${title}</button>`;
  return `<article class="account card ${a.active ? "active" : ""}"><div class="account-top"><span class="avatar">${slot === "primary" ? "I" : "II"}</span><span class="account-name">${escape(name)}</span>${a.active ? '<span class="active-label">● Активна</span>' : ""}</div>${a.configured ? `<p class="email">${escape(a.email || "Аккаунт подключён")}</p><p class="account-caption">${escape(a.error || a.plan || "Подписка ChatGPT")}</p>` : '<p class="empty-account">Здесь может быть ещё один вход в ChatGPT.</p>'}<div class="account-actions">${!a.configured ? button("login", "Войти в ChatGPT", "primary") : a.active ? "" : button("switch", "Использовать", "primary")}${a.configured && detailed ? button("limits", "Проверить квоты") + button("login", "Сменить вход") + button("logout", "Выйти", "danger") : ""}</div>${detailed && a.configured ? `<div class="quota-container">${quotaPanel(slot)}</div>` : ""}</article>`;
}
function renderAccounts() {
  if (!state) return;
  const sig = JSON.stringify([state.accounts, state.settings.labels, limits]);
  if (sig === lastAccounts) return;
  lastAccounts = sig;
  $("overview-accounts").innerHTML = state.accounts
    .map((a) => accountCard(a, false))
    .join("");
  $("account-cards").innerHTML = state.accounts
    .map((a) => accountCard(a, true))
    .join("");
}
function syncSettings() {
  const prefs = state.settings;
  appearance(prefs.appearance);
  const overrides = state.environment_overrides || [];
  const environment = {
    port: "RELAY_PORT",
    "defaults.model": "RELAY_DEFAULT_MODEL",
    "defaults.reasoning": "RELAY_REASONING_EFFORT",
    "defaults.instructions_mode": "RELAY_INSTRUCTIONS",
    "defaults.parallel_tools": "RELAY_PARALLEL_TOOL_CALLS",
  };
  for (const input of document.querySelectorAll(
    "input[data-setting],select[data-setting],textarea[data-setting]",
  )) {
    const path = input.dataset.setting,
      value = path.split(".").reduce((o, k) => o?.[k], prefs);
    if (document.activeElement !== input && !drafts.has(path)) {
      if (input.type === "checkbox") input.checked = !!value;
      else input.value = value ?? "";
    }
    input.disabled =
      overrides.includes(environment[path]) ||
      !!(
        state.instructions_file &&
        ["defaults.instructions_mode", "defaults.instructions"].includes(path)
      );
  }
  $("environment-note").hidden = !overrides.length;
  $("environment-note").textContent =
    "Переменные запуска имеют приоритет: " +
    overrides.join(", ") +
    ". Соответствующие поля недоступны для изменения.";
  $("instructions-note").hidden = !state.instructions_file;
  $("instructions-note").textContent =
    "Инструкции задаёт файл: " +
    state.instructions_file +
    ". Пока он не пуст, он заменяет базовый контекст и дополнения. Изменения файла действуют на следующий запрос.";
}
function render(next) {
  const initial = !state;
  const previousPending = state?.login?.pending;
  state = next;
  if (initial) {
    const startupError = state.events.find((e) =>
      e.message.startsWith("API startup failed: "),
    );
    if (startupError)
      notice(startupError.message.slice("API startup failed: ".length));
  }
  for (const a of state.accounts) {
    const identity = JSON.stringify([a.email, a.plan, a.configured]);
    if (identities[a.slot] !== identity) {
      delete limits[a.slot];
      identities[a.slot] = identity;
    }
  }
  if (
    previousPending &&
    !state.login.pending &&
    !state.login.error &&
    state.login.slot
  )
    delete limits[state.login.slot];
  $("sidebar-version").textContent = `v.${state.version}`;
  $("sidebar-status").textContent = state.running
    ? "API работает"
    : "API остановлен";
  $("sidebar-dot").classList.toggle("online", state.running);
  $("server-badge").textContent = state.running ? "На связи" : "API остановлен";
  $("server-badge").classList.toggle("online", state.running);
  $("server-toggle").textContent = state.running
    ? "Остановить API"
    : "Запустить API";
  $("base-url").textContent = state.base_url;
  $("api-key").textContent = revealed
    ? state.api_key || "—"
    : "••••••••••••••••";
  $("reveal-key").textContent = revealed ? "Скрыть" : "Показать";
  $("reveal-key").setAttribute(
    "aria-label",
    revealed ? "Скрыть ключ" : "Показать ключ",
  );
  $("connection-state").textContent = state.running
    ? state.accounts.some((a) => a.configured)
      ? "Готов принимать запросы"
      : "Подключи подписку"
    : "Запусти API для подключения";
  $("auth-dir").textContent = state.auth_directory;
  $("app-version").textContent = state.version;
  $("protocol-version").textContent = state.protocol_version;
  $("restart-banner").hidden = !state.restart_required;
  $("login-banner").hidden = !state.login.pending;
  $("login-link").hidden = !state.login_url;
  if (previousPending && !state.login.pending && state.login.error)
    notice(state.login.error);
  syncSettings();
  renderAccounts();
  const events = JSON.stringify(state.events);
  if (events !== lastEvents) {
    lastEvents = events;
    $("events").innerHTML = state.events.length
      ? state.events
          .map(
            (e) =>
              `<div class="event"><time>${escape(e.time)}</time><span>${escape(e.message)}</span></div>`,
          )
          .join("")
      : '<p class="empty-events">Пока тихо. Здесь появятся запуск API, переключения и входы.</p>';
  }
}
async function action(op, slot = null) {
  if (busy) return;
  await flushSettings();
  busy = true;
  notice("");
  try {
    if (
      op === "logout" &&
      !window.confirm(`Выйти из «${state.settings.labels[slot]}» в этом реле?`)
    )
      return;
    if (op === "limits") {
      limits[slot] = { loading: true };
      renderAccounts();
      try {
        limits[slot] = { ...(await invoke(op, slot)), _checked: Date.now() };
      } catch (error) {
        limits[slot] = { error: String(error) };
      }
      renderAccounts();
    } else if (op === "restart") {
      await invoke("stop");
      render(await invoke("start"));
    } else if (op === "models") {
      const data = await invoke("models");
      $("model-list").innerHTML = data.models
        .map((model) => `<option value="${escape(model)}"></option>`)
        .join("");
      $("model-source").textContent =
        `${data.models.length} моделей · ${data.source === "live" ? "актуальный каталог" : data.source === "stale" ? "сохранённый каталог" : "резервный каталог"}`;
    } else render(await invoke(op, slot));
  } catch (error) {
    notice(String(error));
  } finally {
    busy = false;
  }
}
async function refreshQuotas() {
  if (!state || busy || settingsPending) return;
  lastQuotaRefresh = Date.now();
  for (const account of state.accounts.filter((a) => a.configured))
    await action("limits", account.slot);
}
function saveSetting(path, value) {
  settingsPending++;
  $("save-state").textContent = "Сохраняем…";
  const keys = path.split("."),
    patch = {};
  let cursor = patch;
  keys.forEach((key, i) => {
    cursor[key] = i === keys.length - 1 ? value : {};
    cursor = cursor[key];
  });
  saveQueue = saveQueue.then(async () => {
    try {
      notice("");
      const next = await invoke("configure", null, null, patch);
      if (drafts.get(path) === value) drafts.delete(path);
      render(next);
      $("save-state").textContent = "Сохранено";
    } catch (error) {
      notice(String(error));
      if (state) syncSettings();
      $("save-state").textContent = "Не сохранено";
    } finally {
      settingsPending--;
    }
  });
}
function commitInput(input) {
  if (!input.dataset.setting || !state) return;
  clearTimeout(inputTimers.get(input));
  if (input.checkValidity && !input.checkValidity()) {
    input.reportValidity();
    return;
  }
  const value =
    input.type === "checkbox"
      ? input.checked
      : input.hasAttribute("data-number")
        ? Number(input.value)
        : input.value;
  const saved = input.dataset.setting
    .split(".")
    .reduce((o, k) => o?.[k], state.settings);
  if (saved === value && !settingsPending) {
    drafts.delete(input.dataset.setting);
    return;
  }
  drafts.set(input.dataset.setting, value);
  saveSetting(input.dataset.setting, value);
}
async function flushSettings() {
  for (const input of document.querySelectorAll(
    "input[data-setting],textarea[data-setting]",
  )) {
    if (drafts.has(input.dataset.setting)) commitInput(input);
  }
  await saveQueue;
}
document.addEventListener("change", (event) => commitInput(event.target));
document.addEventListener("input", (event) => {
  const input = event.target;
  if (
    !input.dataset.setting ||
    input.type === "checkbox" ||
    input.tagName === "SELECT"
  )
    return;
  drafts.set(input.dataset.setting, input.value);
  $("save-state").textContent = "Сохраняем…";
  clearTimeout(inputTimers.get(input));
  inputTimers.set(
    input,
    setTimeout(() => commitInput(input), 500),
  );
});
document.addEventListener("click", async (event) => {
  const b = event.target.closest("button");
  if (!b) return;
  if (b.dataset.page) page(b.dataset.page);
  if (b.dataset.go) page(b.dataset.go);
  if (b.dataset.settingsTab) settingsTab(b.dataset.settingsTab);
  if (b.dataset.window) await windowAction(b.dataset.window);
  if (b.dataset.op) await action(b.dataset.op, b.dataset.slot);
  if (b.dataset.swatch && state)
    saveSetting(b.dataset.setting, b.dataset.swatch);
  if (b.dataset.copy && state) {
    try {
      await navigator.clipboard.writeText(
        b.dataset.copy === "key" ? state.api_key : state.base_url,
      );
      toast("Скопировано");
    } catch {
      notice("Не удалось скопировать. Выдели значение и нажми Ctrl+C.");
    }
  }
});
$("titlebar").addEventListener("mousedown", (event) => {
  if (
    event.button === 0 &&
    !event.target.closest("button") &&
    event.detail === 1
  )
    windowAction("drag");
});
$("titlebar").addEventListener("dblclick", (event) => {
  if (!event.target.closest("button")) windowAction("maximize");
});
$("server-toggle").addEventListener("click", () =>
  action(state?.running ? "stop" : "start"),
);
$("restart-api").addEventListener("click", () => action("restart"));
$("cancel-login").addEventListener("click", () => action("cancel_login"));
$("login-link").addEventListener("click", () => action("open_login"));
$("refresh-quotas").addEventListener("click", refreshQuotas);
$("load-models").addEventListener("click", () => action("models"));
$("reveal-key").addEventListener("click", () => {
  revealed = !revealed;
  if (state) render(state);
});
document.addEventListener("keydown", (event) => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  const dest = { 1: "overview", 2: "accounts", 3: "activity", ",": "settings" }[
    event.key
  ];
  if (dest) {
    event.preventDefault();
    page(dest);
  }
  if (event.key.toLowerCase() === "q") {
    event.preventDefault();
    windowAction("quit");
  }
});
async function refresh() {
  try {
    if (!busy && !settingsPending) render(await invoke("status"));
    const interval = state?.settings.quota_refresh_seconds;
    if (
      interval &&
      state.window_visible !== false &&
      !document.hidden &&
      Date.now() - lastQuotaRefresh >= interval * 1000
    )
      await refreshQuotas();
  } catch (error) {
    notice(String(error));
  }
}
try {
  page(localStorage.getItem("relay.page") || "overview");
} catch {}
refresh();
setInterval(refresh, 1500);
