// «Система»: всё, чем владелец управляет с её телефона, и само окно.
// Управление Пракс, служба XTTS и её голос — её же блоки (те, что стоят на телефоне);
// контейнеры, мозг, изображения, телефонные пары, вид и настройки окна — здесь.
import { mountPraxisControl } from "../../../ui-kit/praxis-control";
import { mountXTTSService } from "../../../ui-kit/praxis-service";
import { mountVoicePanel } from "../../../ui-kit/praxis-voice";
import { lookCard } from "../../../ui-kit/window/look";
import { esc, fmtDay, fmtTime } from "../../../ui-kit/text";
import { receipt, toast } from "../../../ui-kit/dom";
import { el, gate, shell, why, type ProxyNetwork, type ProxyState, type ShellSettings } from "../core";

interface Brain {
  ok?: boolean; note?: string;
  roles?: Record<string, Record<string, string>>;
  frameworks?: Record<string, unknown>;
  images?: { enabled?: boolean; model?: string; quality?: string; size?: string; background?: string };
  image_models?: string[];
}
interface Models { by_framework?: Record<string, { ok?: boolean; models?: string[] }> }
interface Container { name: string; up: boolean; status: string; image: string }
interface Device { id: string; name: string; created?: string; last_seen?: string }
interface OwnerTelegram { available: boolean; enabled?: boolean; state?: string }
interface Box {
  ok?: boolean; note?: string; available?: boolean; why?: string;
  containers?: Container[]; lines?: string[]; text?: string;
}

const EFFORTS = ["", "low", "medium", "high", "xhigh"];
const TELEGRAM: Record<string, string> = { ready: "Работает", starting: "Запускается", off: "Выключен", error: "Ошибка сервиса",
  login_required: "Требуется вход", account_mismatch: "Другой аккаунт", offline: "Связь потеряна", unavailable: "Состояние недоступно" };
const chip = (text: string, onClick: () => void) => {
  const node = el("button", "chip", text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
};

function cardOf(title: string, hint: string, cls = ""): HTMLElement {
  const box = el("section", "card sys-card " + cls);
  if (title) box.append(el("h3", "", title));
  if (hint) box.append(el("p", "field-hint", hint));
  return box;
}

// Её блоки XTTS и голоса опрашивают её сервер своим ходом, а не через канал раздела. Пока
// «Система» скрыта, они сняты (их последнее состояние остаётся на месте), показана — стоят снова.
let services: Array<() => void> = [];
let serviceHome: { service: HTMLElement; voice: HTMLElement } | null = null;
let refreshers: Array<() => void> = [];     // что перечитать при показе (состояние служб и устройств)

/** С «Системы» ушли: опросы служб останавливаются, карточки — остаются как были. */
export function sleep(): void {
  for (const off of services.splice(0)) off();
}

/** «Система» показана: службы встают и читаются заново, карточки состояния обновляются на месте —
 *  сразу, не дожидаясь щелчка. */
export function wake(): void {
  if (services.length || !serviceHome) return;
  services = [mountXTTSService(serviceHome.service), mountVoicePanel(serviceHome.voice)];
  for (const refresh of refreshers) refresh();
}

export function mount(root: HTMLElement): void {
  // Её канал через ворота раздела: скрытый раздел не опрашивает сервер, показанный — перечитывает сразу.
  const g = gate(root);
  const { api, post } = g;
  root.classList.add("system");
  const alive = true;                      // раздел живёт, пока открыто окно
  const later = (fn: () => void, ms: number) => g.later(fn, ms);

  const control = cardOf("", "", "plain");
  mountPraxisControl(control, g.request);

  // ---- Telegram владельца: включён ли её личный бот
  const telegram = cardOf("Telegram Егора", "Её личный бот, через который вы говорите с ней в Telegram.");
  const telegramState = el("p", "muted", "Проверяю…");
  telegramState.setAttribute("role", "status");
  const telegramButton = chip("", () => undefined);
  const paintTelegram = (value: OwnerTelegram) => {
    telegramState.textContent = value.available ? (TELEGRAM[value.state || ""] || "Состояние недоступно") : "Сервис недоступен в этом канале.";
    telegramButton.textContent = value.enabled ? "Выключить" : "Включить";
    telegramButton.setAttribute("aria-pressed", String(!!value.enabled));
    telegramButton.disabled = !(value.available && value.enabled !== undefined);
  };
  telegramButton.addEventListener("click", async () => {
    telegramButton.disabled = true;
    try { paintTelegram(await post<OwnerTelegram>("/api/services/owner-telegram", { enabled: telegramButton.getAttribute("aria-pressed") !== "true" })); }
    catch (error) { toast(why(error)); }
    telegramButton.disabled = false;
  });
  telegram.append(telegramState, telegramButton);
  let telegramBusy = false;
  const telegramTick = async () => {
    if (!alive) return;
    if (!telegramBusy && !document.hidden) {
      telegramBusy = true;
      try { paintTelegram(await api<OwnerTelegram>("/api/services/owner-telegram")); }
      catch { telegramState.textContent = "Не удалось проверить сервис. Повторю сам."; }
      telegramBusy = false;
    }
    later(() => void telegramTick(), 5000);
  };
  void telegramTick();

  // ---- контейнеры: состояние, перезапуск, журнал и только ошибки
  const containers = cardOf("Контейнеры", "Перезапуск нужен тогда, когда она не отвечает. Что позволено — решает служба рядом с каналом.", "wide");
  const containerBody = el("div");
  containers.append(containerBody);
  const paintContainers = (value: Box) => {
    containerBody.replaceChildren();
    if (!value.available || !value.containers?.length) { containerBody.append(el("p", "muted", value.why || "Управление контейнерами в этом канале не объявлено.")); return; }
    const table = el("table", "table containers");
    const head = el("tr");
    for (const name of ["Контейнер", "Состояние", "Образ", "Действия"]) head.append(el("th", "", name));
    table.append(head);
    const shown = { node: null as HTMLElement | null };
    for (const box of value.containers) {
      const row = el("tr");
      row.append(el("td", "mono", box.name), el("td", box.up ? "" : "err", box.status), el("td", "muted", box.image));
      const actions = el("td", "table-actions");
      const output = el("tr", "container-log");
      const cell = el("td");
      cell.colSpan = 4;
      output.append(cell);
      output.hidden = true;
      table.append(row, output);
      const show = async (errors: boolean) => {
        const again = shown.node === output && cell.dataset.mode === String(errors);
        if (shown.node) shown.node.hidden = true;
        shown.node = null;
        if (again) return;
        shown.node = output;
        output.hidden = false;
        cell.dataset.mode = String(errors);
        cell.textContent = "Читаю журнал…";
        try {
          const answer = await api<Box>(`/api/container-log/${encodeURIComponent(box.name)}?lines=300${errors ? "&errors=1" : ""}`);
          const lines = answer.lines ?? (answer.text || "").split("\n");
          cell.replaceChildren();
          cell.append(el("pre", "code", lines.join("\n") || (errors ? "Ошибок в журнале нет." : "Журнал пуст.")));
        } catch (error) { cell.textContent = why(error); }
      };
      actions.append(
        chip("Перезапустить", async () => {
          if (!window.confirm(`Перезапустить контейнер «${box.name}»?`)) return;
          try {
            const answer = await post<Box>("/api/containers/restart", { name: box.name });
            toast(answer.note || "Перезапущено.");
            paintContainers(await api<Box>("/api/containers"));
          } catch (error) { toast(why(error)); }
        }),
        chip("Журнал", () => void show(false)),
        chip("Ошибки", () => void show(true)));
      row.append(actions);
    }
    const wrap = el("div", "table-wrap");
    wrap.append(table);
    containerBody.append(wrap, el("p", "field-hint", "Журнал — последние 300 строк; «Ошибки» — только строки с ошибками."));
  };
  void api<Box>("/api/containers").then((value) => { if (alive) paintContainers(value); })
    .catch((error) => { if (alive) containerBody.append(el("p", "err", why(error))); });
  refreshers.push(() => void api<Box>("/api/containers").then(paintContainers).catch(() => undefined));

  // ---- мозг: модель, фреймворк, запасная и усилие — по роли; изображения
  const brainBox = cardOf("Мозг", "Модели её ролей. Ключи провайдеров сюда не попадают — только модели, фреймворки и усилие. Уходит лишь то, что изменили.", "wide");
  const loadBrain = async () => {
    const body = el("div", "sys-body");
    brainBox.querySelector(".sys-body")?.replaceWith(body);
    let brain: Brain;
    let models: Models;
    try {
      [brain, models] = await Promise.all([api<Brain>("/api/brain"), api<Models>("/api/brain-models")]);
    } catch (error) { body.append(el("p", "err", why(error))); return; }
    if (!alive) return;
    if (!brain.ok) { body.append(el("p", "muted", brain.note || "Мозг отсюда не читается.")); return; }
    const offered: string[] = [];
    for (const spec of Object.values(models.by_framework || {})) if (spec.ok && spec.models) offered.push(...spec.models);
    const frameworks = Object.keys(brain.frameworks || {});
    const options = (values: string[], current: string) => [...new Set([current, ...values])]
      .map((item) => `<option value="${esc(item)}"${item === current ? " selected" : ""}>${esc(item || "—")}</option>`).join("");
    const field = (label: string, control: string) => `<label class="sys-field"><span class="muted">${label}</span>${control}</label>`;
    const input = (key: string, value: string, placeholder = "") => `<input list="brain-models" data-f="${key}" data-was="${esc(value)}" value="${esc(value)}" placeholder="${esc(placeholder)}" autocapitalize="off" autocorrect="off" spellcheck="false">`;
    const select = (key: string, values: string[], value: string) => `<select data-f="${key}" data-was="${esc(value)}">${options(values, value)}</select>`;
    const value = (spec: Record<string, string>, key: string) => String(spec[key] ?? "");
    const form = (role: string, title: string, inside: string) => `<form class="task-row sys-brain" data-brain-form="${esc(role)}">
      <div class="task-head"><b>${title}</b></div>${inside}
      <div class="actions"><button type="submit" class="chip">Применить</button><span class="receipt" role="status"></span></div></form>`;
    const images = brain.images || {};
    const image = images as Record<string, unknown>;
    const choice = (key: string, pairs: Array<[string, string]>) => `<select data-f="${key}" data-was="${esc(String(image[key] ?? ""))}">${pairs.map(([item, title]) => `<option value="${item}"${String(image[key] ?? "") === item ? " selected" : ""}>${title}</option>`).join("")}</select>`;
    body.innerHTML = `<datalist id="brain-models">${[...new Set([...(brain.image_models || []), ...offered])].map((item) => `<option value="${esc(item)}"></option>`).join("")}</datalist>`
      + `<div class="grid2">${Object.entries(brain.roles || {}).map(([role, spec]) => form(role, `${esc(role)} <span class="muted">${esc(value(spec, "framework"))} · ${esc(value(spec, "model"))}</span>`,
        field("Модель", input("model", value(spec, "model")))
        + field("Фреймворк", select("framework", frameworks, value(spec, "framework")))
        + field("Запасная модель", input("fallback_model", value(spec, "fallback_model"), "пусто — без запасной"))
        + field("Фреймворк запасной", select("fallback_framework", ["", ...frameworks], value(spec, "fallback_framework"))))
        + field("Усилие", select("reasoning_effort", EFFORTS, value(spec, "reasoning_effort")))).join("")}
        ${form("images", "Изображения <span class=\"muted\">генерация и правка через реле</span>",
          field("Генерация", choice("enabled", [["true", "Включена"], ["false", "Выключена"]]))
          + field("Модель", input("model", String(images.model ?? "")))
          + field("Качество", choice("quality", [["auto", "Автоматически"], ["low", "Черновик"], ["medium", "Среднее"], ["high", "Высокое"]]))
          + field("Размер", input("size", String(images.size ?? ""), "auto или 1024x1024"))
          + field("Фон", choice("background", [["opaque", "Обычный"], ["transparent", "Прозрачный"], ["auto", "Автоматически"]])))}</div>`;
    for (const item of body.querySelectorAll<HTMLFormElement>("[data-brain-form]")) {
      item.addEventListener("submit", async (event: SubmitEvent) => {
        event.preventDefault();
        const fields: Record<string, string> = {};
        for (const control of item.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-f]"))
          if (control.value !== control.dataset.was) fields[control.dataset.f!] = control.value;
        const note = item.querySelector<HTMLElement>(".receipt")!;
        if (!Object.keys(fields).length) { receipt(note, "Нечего применять: ничего не изменилось."); return; }
        const button = item.querySelector<HTMLButtonElement>("button[type=submit]")!;
        button.disabled = true;
        try {
          const answer = await post<Box>("/api/brain", { role: item.dataset.brainForm, fields });
          receipt(note, answer.note || "Применено.", answer.ok === false ? "err" : "ok");
          if (answer.ok !== false) await loadBrain();
        } catch (error) { receipt(note, why(error), "err"); }
        button.disabled = false;
      });
    }
  };
  void loadBrain();

  // ---- телефоны: кто привязан, отвязать, выдать код новому
  const phones = cardOf("Телефоны", "Праксис пускает только привязанные устройства. Код одноразовый и живёт недолго.", "wide");
  const phoneBody = el("div");
  const issued = el("input", "phone-code mono");
  issued.readOnly = true;
  issued.hidden = true;
  issued.setAttribute("aria-label", "Код подключения для телефона");
  issued.addEventListener("focus", () => issued.select());
  const paintDevices = async () => {
    const devices = await api<Device[]>("/pair/devices");
    if (!alive) return;
    const table = el("table", "table");
    const head = el("tr");
    for (const name of ["Устройство", "Привязано", "Виделось в последний раз", ""]) head.append(el("th", "", name));
    table.append(head);
    const when = (iso?: string) => (iso ? `${fmtDay(iso)}, ${fmtTime(iso)}` : "—");
    for (const device of devices) {
      const row = el("tr");
      const actions = el("td", "table-actions");
      actions.append(chip("Отвязать", async () => {
        if (!window.confirm(`Отвязать «${device.name}»? Ему придётся подключаться заново.`)) return;
        try { await post("/pair/revoke", { id: device.id }); toast("Отвязано."); await paintDevices(); }
        catch (error) { toast(why(error)); }
      }));
      row.append(el("td", "", device.name), el("td", "", when(device.created)), el("td", "muted", when(device.last_seen)), actions);
      table.append(row);
    }
    const actions = el("div", "actions");
    actions.append(chip("Выдать код для нового телефона", async () => {
      try {
        issued.value = (await post<{ token: string }>("/pair/new", {})).token;
        issued.hidden = false;
        issued.focus();
      } catch (error) { toast(why(error)); }
    }), issued);
    const wrap = el("div", "table-wrap");
    wrap.append(table);
    phoneBody.replaceChildren(wrap, actions);
  };
  void paintDevices().catch((error) => phoneBody.append(el("p", "err", why(error))));
  refreshers.push(() => void paintDevices().catch(() => undefined));
  phones.append(phoneBody);

  // ---- её большие блоки: служба XTTS, голос, вид окна
  const service = cardOf("", "", "plain");
  const voice = cardOf("", "", "plain wide");
  serviceHome = { service, voice };
  services = [mountXTTSService(service), mountVoicePanel(voice)];
  const look = cardOf("Вид", "Бумага, акцент, фактура, крупность текста и физика прокрутки — на все экраны. Применяется сразу.", "wide look");
  look.append(lookCard());

  // ---- это окно: автозапуск и стартовый размер
  const own = cardOf("Это окно", "", "own");
  const host = shell();
  if (!host) own.append(el("p", "muted", "Страница открыта не в программе: автозапуск и стартовый размер настраиваются в окне."));
  else {
    const autostart = chip("", () => undefined);
    const maximized = chip("", () => undefined);
    let settings: ShellSettings = { autostart: false, startMaximized: true, version: "" };
    const paint = () => {
      autostart.textContent = settings.autostart ? "Запускать вместе с Windows: да" : "Запускать вместе с Windows: нет";
      maximized.textContent = settings.startMaximized ? "Открывать развёрнутым на весь экран: да" : "Открывать развёрнутым на весь экран: нет";
      autostart.setAttribute("aria-pressed", String(settings.autostart));
      maximized.setAttribute("aria-pressed", String(settings.startMaximized));
    };
    autostart.addEventListener("click", async () => {
      try { settings = await host.setSettings({ autostart: !settings.autostart }); paint(); }
      catch (error) { toast(why(error)); }
    });
    maximized.addEventListener("click", async () => {
      try { settings = await host.setSettings({ startMaximized: !settings.startMaximized }); paint(); }
      catch (error) { toast(why(error)); }
    });
    // масштаб страницы — под свой экран; колёсиком с Ctrl и клавишами ведёт оболочка
    const smaller = chip("Мельше", () => undefined);
    const bigger = chip("Крупнее", () => undefined);
    const reset = chip("Сбросить", () => undefined);
    const size = el("span", "muted sys-zoom-value");
    const paintZoom = (value: number) => { size.textContent = `${Math.round(value * 100)} %`; reset.hidden = Math.abs(value - 1) < 0.001; };
    const resize = (step: number | "reset") => host.zoom
      ? host.zoom(step === "reset" ? { reset: true } : { step }).then(paintZoom).catch((error) => toast(why(error)))
      : Promise.resolve();
    smaller.addEventListener("click", () => void resize(-1));
    bigger.addEventListener("click", () => void resize(1));
    reset.addEventListener("click", () => void resize("reset"));
    const zoomRow = el("div", "sys-zoom actions");
    zoomRow.append(smaller, size, bigger, reset);
    // дорога до сервера: напрямую или через прокси — туннель без VPN, как у Джарвиса в Hélène
    const road = chip("Дорога до сервера", () => undefined);
    const task = chip("", () => undefined);
    const roadNote = el("p", "muted");
    const NETWORK: Record<ProxyNetwork, string> = { auto: "авто", direct: "напрямую", proxy: "через прокси" };
    const ORDER: ProxyNetwork[] = ["auto", "direct", "proxy"];
    const KEEPER: Record<string, string> = {
      connecting: "подключается", tunnel: "туннель поднимается", ok: "работает", stopped: "остановлен", unconfigured: "без настроек",
    };
    const WHY: Record<string, string> = {
      hostkey: "ключ машины не совпал — не вхожу", auth: "пароль не подошёл", forward: "перенаправление запрещено",
      network: "нет сети", plink: "нет plink.exe", error: "ошибка", exit: "туннель оборвался", start: "не запустился",
    };
    let proxy: ProxyState | null = null;
    const paintProxy = (value: ProxyState) => {
      proxy = value;
      road.hidden = task.hidden = !value.configured;
      if (!value.configured) {
        roadNote.textContent = "Без VPN окно до сервера не дозвонится: прокси не настроен (proxy/install-proxy.ps1 в папке программы).";
        return;
      }
      road.textContent = `Дорога до сервера: ${NETWORK[value.network]}`;
      road.setAttribute("aria-pressed", String(value.network !== "direct"));
      task.textContent = value.proxyTask ? "Прокси без VPN: включён" : "Прокси без VPN: выключен";
      task.setAttribute("aria-pressed", String(value.proxyTask));
      const keeper = value.keeper;
      const how = !keeper ? "хранитель не запускался" : keeper.state === "retrying" || keeper.state === "failed"
        ? `${KEEPER[keeper.state] ?? keeper.state}: ${WHY[keeper.why] ?? keeper.why}` : (KEEPER[keeper.state] ?? keeper.state);
      roadNote.textContent = value.alive
        ? `Прокси работает (${value.proxy_url})${value.route === "proxy" ? " — окно ходит через него." : ""}`
        : `Прокси не запущен (${value.proxy_url}); ${how}.`;
    };
    road.addEventListener("click", () => void (host.setProxy?.({ network: ORDER[(ORDER.indexOf(proxy?.network ?? "auto") + 1) % 3] }) ?? Promise.resolve(null))
      .then((value) => { if (value) paintProxy(value); }).catch((error) => toast(why(error))));
    task.addEventListener("click", () => void (host.setProxy?.({ proxyTask: !proxy?.proxyTask }) ?? Promise.resolve(null))
      .then((value) => { if (value) paintProxy(value); }).catch((error) => toast(why(error))));
    const networkRow = el("div", "sys-zoom actions");
    networkRow.append(road, task);
    const pollProxy = () => {
      if (!alive || !host.proxyState) return;
      void host.proxyState().then(paintProxy).catch(() => undefined).finally(() => later(pollProxy, 5000));
    };
    pollProxy();
    own.append(el("p", "field-hint", "При запуске вместе с Windows окно открывается свёрнутым и не мешает. Размер и положение обычного окна запоминаются сами."),
      autostart, maximized,
      el("p", "field-hint", "Масштаб страницы — под свой экран: Ctrl + колесо мыши, Ctrl + «+» и Ctrl + «−», Ctrl + «0» — вернуть."), zoomRow,
      el("p", "field-hint", "Сервер напрямую из России недоступен. «Авто» начинает с дороги, что сработала в прошлый раз; прокси — туннель до машины с tinyproxy, его держит задача «Praxis Proxy» при входе в Windows (она же нужна телу Праксис)."),
      networkRow, roadNote,
      chip("Обновить окно", () => void host.reload().catch((error) => toast(why(error)))),
      el("p", "muted", "Интерфейс приезжает с её сервера: «Обновить окно» приносит свежую сборку без переустановки."));
    void host.settings().then((value) => { settings = value; paint(); paintZoom(value.zoom ?? 1); }).catch((error) => own.append(el("p", "err", why(error))));
  }

  const grid = el("div", "cards sys-grid");
  grid.append(control, telegram, containers, service, brainBox, phones, voice, look, own);
  root.append(grid);
}
