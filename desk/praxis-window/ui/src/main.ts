// Окно Пракс: каркас. Полка разделов слева, раздел в центре, текущий ход справа.
// Каждый раздел — модуль в views/ и умеет одно: нарисовать себя в свой узел и
// вернуть то, что надо снять в конце. Разделы живут в памяти всё время: с полки
// открывается уже готовый, а опрашивает её сервер только тот, что на экране.
// Состояние Праксис читает и раздаёт core.
import "../../ui-kit/phone.css";
import "./shell.css";
import "./views.css";
import { applyTheme, readTheme, saveTheme, type Theme } from "../../ui-kit/dom";
import * as look from "../../ui-kit/window/look";
import { phoneActions } from "../../ui-kit/phone-actions";
import { agentState, gate, lull, onState, setNavigator, shell, startStateLoop, statePhrase, wake, whenStateReady, type View } from "./core";
import * as now from "./views/now";
import * as chats from "./views/chats";
import * as tasks from "./views/tasks";
import * as wakes from "./views/wakes";
import * as instruments from "./views/instruments";
import * as files from "./views/files";
import * as system from "./views/system";

interface Section { id: View; label: string; kicker: string; icon: string; side: boolean }

const SECTIONS: Section[] = [
  { id: "now", label: "Сейчас", kicker: "Что она делает", icon: '<path d="M3 10h3l2-5 3 10 2-5h4"/>', side: false },
  { id: "chats", label: "Чаты", kicker: "Её комнаты и темы", icon: '<path d="M4 5.5h12v8H8l-4 3z"/>', side: true },
  { id: "tasks", label: "Задачи", kicker: "Намеченное, доска, субагенты", icon: '<path d="M7 3.5h6a1 1 0 0 1 1 1v12l-4-2.2-4 2.2v-12a1 1 0 0 1 1-1Z"/>', side: true },
  { id: "wakes", label: "Пробуждения", kicker: "Когда она проснётся и что делала", icon: '<circle cx="10" cy="11" r="6.2"/><path d="M10 7.6V11l2.2 1.6M4.4 3.6 2.8 5.2M15.6 3.6l1.6 1.6"/>', side: true },
  { id: "instruments", label: "Приборы", kicker: "Что читает модель и что записал харнесс", icon: '<path d="M3.5 14.5a6.5 6.5 0 1 1 13 0"/><path d="M10 14.5 12.8 9M3 14.5h14"/>', side: false },
  { id: "files", label: "Файлы", kicker: "Её память и навыки", icon: '<path d="M5 3.5h7l3 3V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M11.8 3.8v3h3M6.7 10h6.6M6.7 13h4.5"/>', side: true },
  { id: "system", label: "Система", kicker: "Управление, службы, это окно", icon: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2M10 15.2v2M2.8 10h2M15.2 10h2M4.9 4.9l1.4 1.4M13.7 13.7l1.4 1.4M4.9 15.1l1.4-1.4M13.7 6.3l-1.4-1.4"/>', side: true },
];

interface Screen {
  mount(root: HTMLElement, tools: HTMLElement): void;
  focus?(intent: unknown): void;
  /** С раздела ушли: снять то, что опрашивает её сервер само, минуя ворота раздела. */
  sleep?(): void;
  /** Раздел показан: поставить это обратно и прочитать заново. */
  wake?(): void;
}
const SCREENS: Record<View, Screen> = { now, chats, tasks, wakes, instruments, files, system };

const app = document.getElementById("app") as HTMLElement;
const rail = document.getElementById("rail") as HTMLElement;
const nav = document.getElementById("rail-nav") as HTMLElement;
const kicker = document.getElementById("top-kicker") as HTMLElement;
const title = document.getElementById("top-title") as HTMLElement;
const tools = document.getElementById("top-tools") as HTMLElement;
const view = document.getElementById("view") as HTMLElement;
const side = document.getElementById("side") as HTMLElement;
const sideInner = document.getElementById("side-inner") as HTMLElement;
const railPhrase = document.getElementById("rail-phrase") as HTMLElement;
const railState = document.getElementById("rail-state") as HTMLElement;
const build = document.getElementById("rail-build") as HTMLElement;
const railButton = document.getElementById("toggle-rail") as HTMLButtonElement;
const panelButton = document.getElementById("toggle-panel") as HTMLButtonElement;

let current: View = "now";
let sideActions: ReturnType<typeof phoneActions> | undefined;
let intent: unknown = undefined;
const scope = () => location.origin + "\n" + (agentState()?.agent ?? "");

/** У каждого раздела свой дом. Раздел живёт в нём, пока окно открыто: скрытый дом ждёт
 *  за экраном со всем содержимым, позиции прокрутки и открытыми строками — оттого щелчок
 *  по полке ничего не подгружает. Опросы скрытого раздела спят (core: gate). */
interface Home { host: HTMLElement; tools: HTMLElement }
const homes = new Map<View, Home>();
const mounted = new Set<View>();

function home(name: View): Home {
  let mine = homes.get(name);
  if (!mine) {
    mine = { host: document.createElement("section"), tools: document.createElement("div") };
    mine.host.className = "view-host";
    mine.host.dataset.view = name;
    mine.host.hidden = true;
    mine.host.setAttribute("aria-label", SECTIONS.find((item) => item.id === name)?.label ?? name);
    view.append(mine.host);
    homes.set(name, mine);
  }
  return mine;
}

function ensure(name: View): Home {
  const mine = home(name);
  if (!mounted.has(name)) {
    mounted.add(name);
    SCREENS[name].mount(mine.host, mine.tools);
  }
  return mine;
}

/** Раздел готовится за экраном: читает свои данные и несколько секунд остаётся готовым.
 *  Щёлкнули — открывается уже с содержимым. Не щёлкнули — засыпает, как будто с него ушли. */
function warm(name: View): void {
  if (name === current || mounted.has(name)) return;
  ensure(name);
  window.setTimeout(() => {
    if (name === current) return;
    lull(home(name).host);
    SCREENS[name].sleep?.();
  }, WARM_FOR);
}

/** После запуска все разделы готовятся за экраном по очереди: «Файлы» — одним из первых,
 *  потому что читает всё её дерево дольше всех; к щелчку каждый уже готов. */
function warmAll(): void {
  WARM_ORDER.forEach((name, index) => window.setTimeout(() => warm(name), WARM_START + index * WARM_STEP));
}
const WARM_ORDER: View[] = ["chats", "files", "wakes", "tasks", "instruments", "system"];
const WARM_START = 4000;                  // сначала пусть откроется «Сейчас»
const WARM_STEP = 2500;                   // разделы готовятся по одному, не разом
const WARM_FOR = 5000;                    // столько готовый раздел ждёт щелчка, потом засыпает (мс)

// ------------------------------------------------------------------ память окна

function readFlag(key: string): boolean {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}
function writeFlag(key: string, on: boolean): void {
  try { localStorage.setItem(key, on ? "1" : "0"); } catch { /* без хранилища окно просто не запомнит */ }
}

// Панели сворачиваются, как в Элен, и помнят состояние. Движение — ТОЛЬКО когда
// рычаг дёрнул человек: восстановление из памяти при запуске и смена раздела
// обязаны быть мгновенными.
function setCollapsed(which: "rail" | "panel", on: boolean, animate = false): void {
  app.classList.toggle("no-anim", !animate);
  if (animate) {
    const done = () => app.classList.add("no-anim");
    app.addEventListener("transitionend", function once(event: TransitionEvent) {
      if (event.propertyName !== "--rail-track" && event.propertyName !== "--panel-track") return;
      app.removeEventListener("transitionend", once);
      done();
    });
    window.setTimeout(done, 700);              // страховка: панель уже была в этом положении
  }
  app.classList.toggle(which + "-collapsed", on);
  writeFlag("praxis.window." + which, on);
  (which === "rail" ? railButton : panelButton).setAttribute("aria-pressed", String(!on));
}
setCollapsed("rail", readFlag("praxis.window.rail"));
setCollapsed("panel", readFlag("praxis.window.panel"));

const labelRail = () => {
  const on = app.classList.contains("rail-collapsed");
  railButton.title = (on ? "Показать полку" : "Свернуть полку") + (shell() ? " (Ctrl+B)" : "");
  railButton.setAttribute("aria-label", on ? "Показать полку разделов" : "Свернуть полку разделов");
  rail.inert = on;                         // свёрнутая полка за краем окна: табом в неё не зайти
};
railButton.addEventListener("click", () => { setCollapsed("rail", !app.classList.contains("rail-collapsed"), true); labelRail(); });
panelButton.addEventListener("click", () => { setCollapsed("panel", !app.classList.contains("panel-collapsed"), true); syncSide(); });

// Узкое окно (полка + раздел + панель хода не помещаются): панель хода не показывается,
// сколько её ни включай, — кнопка честно говорит об этом.
const narrow = matchMedia("(max-width: 1179px)");

/** Панель хода — по разделу, ширине окна и рычагу владельца: кнопка, доступность табом и сама панель. */
function syncSide(): void {
  const wanted = SECTIONS.find((item) => item.id === current)?.side ?? false;
  const fits = !narrow.matches;
  panelButton.hidden = !wanted;
  panelButton.disabled = wanted && !fits;
  const on = wanted && fits && !app.classList.contains("panel-collapsed");
  panelButton.title = !wanted ? "" : !fits ? "Панель хода не помещается: разверните окно или сверните полку"
    : (on ? "Свернуть панель хода" : "Показать панель хода") + (shell() ? " (Ctrl+J)" : "");
  panelButton.setAttribute("aria-label", on ? "Свернуть панель текущего хода" : "Показать панель текущего хода");
  side.inert = !on;
  app.classList.toggle("side-shown", on);
  // Панель хода одна на всё окно: скрытая — спит (её блок нельзя останавливать среди опроса),
  // показанная — просыпается и сразу перечитывает ход.
  if (on) mountSide();
  if (on) wake(sideInner); else lull(sideInner);
}
narrow.addEventListener("change", syncSide);

// ------------------------------------------------------------------ разделы

function show(name: View): void {
  current = name;
  try { sessionStorage.setItem("praxis.window.view", name); } catch { /* раздел просто не вспомнится */ }
  const section = SECTIONS.find((item) => item.id === name) ?? SECTIONS[0];
  app.dataset.view = name;
  app.classList.toggle("with-side", section.side);
  kicker.textContent = section.kicker;
  title.textContent = section.label;
  for (const item of nav.querySelectorAll<HTMLElement>(".rail-item[data-view]"))
    item.setAttribute("aria-current", item.dataset.view === name ? "page" : "false");
  const wanted = intent;
  intent = undefined;
  const mine = ensure(name);
  for (const [other, place] of homes) {
    place.host.hidden = other !== name;
    if (other === name) continue;
    lull(place.host);
    SCREENS[other].sleep?.();
  }
  SCREENS[name].wake?.();
  wake(mine.host);                       // спавшие опросы срабатывают сразу: данные свежие с первого взгляда
  tools.replaceChildren(mine.tools);
  mine.host.classList.remove("enter");
  void mine.host.offsetWidth;            // раздел входит заново, а не продолжает прежнюю анимацию
  mine.host.classList.add("enter");
  if (wanted !== undefined) SCREENS[name].focus?.(wanted);
  syncSide();
}

setNavigator((name, withIntent) => {
  if (name === current) { if (withIntent !== undefined) SCREENS[name].focus?.(withIntent); return; }
  intent = withIntent;
  show(name);
});

/** Панель текущего хода — одна на всё окно. */
function mountSide(): void {
  if (sideActions) return;
  sideInner.replaceChildren();
  // Её блок называет свой узел своим классом — поэтому стоит во вложенной коробке.
  const box = document.createElement("div");
  sideInner.append(box);
  const panel = phoneActions(box, gate(sideInner).request, scope, undefined, { history: false });
  sideActions = panel;
  void whenStateReady().then(() => { if (sideActions === panel) panel.start(); });
}

function paintState(): void {
  const phrase = statePhrase(agentState());
  railPhrase.textContent = phrase;
  railState.dataset.level = !agentState() ? "off" : phrase.startsWith("Ведёт") ? "live" : phrase.startsWith("На связи") ? "ok" : phrase.startsWith("Подключ") ? "off" : "warn";
}

function buildNav(): void {
  SECTIONS.forEach((section, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "rail-item";
    button.dataset.view = section.id;
    button.setAttribute("aria-current", String(section.id === "now"));
    button.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${section.icon}</svg><span>${section.label}</span>${shell() ? `<kbd>Ctrl+${index + 1}</kbd>` : ""}`;
    button.addEventListener("click", () => show(section.id));
    button.addEventListener("pointerenter", () => warm(section.id));
    nav.append(button);
  });
}

/** Цвет системной рамки следует за темой окна: светлая бумага — тёмные значки, и наоборот. */
function paintChrome(): void {
  const host = shell();
  if (!host) return;
  const dark = document.documentElement.dataset.theme === "dark" || (readTheme() === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  const styles = getComputedStyle(document.documentElement);
  host.titleBar({ color: styles.getPropertyValue("--ground").trim() || (dark ? "#1a1815" : "#fdfcfa"),
    symbolColor: styles.getPropertyValue("--ink").trim() || (dark ? "#efe9df" : "#262320") });
}

const THEME: Record<Theme, string> = { system: "Как в системе", light: "Светлая", dark: "Тёмная" };
function themeCycle(): void {
  const order: Theme[] = ["system", "light", "dark"];
  const next = order[(order.indexOf(readTheme()) + 1) % order.length];
  saveTheme(next);
  applyTheme(next);
  document.getElementById("rail-theme-label")!.textContent = THEME[next];
  void paintChrome();
}

applyTheme(readTheme());
look.apply(look.load());
document.getElementById("rail-theme-label")!.textContent = THEME[readTheme()];
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => void paintChrome());
document.getElementById("rail-theme")!.addEventListener("click", themeCycle);

// Масштаб под свой экран: Ctrl + колесо мыши (и щипок на тачпаде — он приходит тем же событием).
// Сама страница при этом не прокручивается: изменение уходит оболочке, она ведёт масштабом.
document.addEventListener("wheel", (event) => {
  if (!event.ctrlKey) return;
  event.preventDefault();
  const host = shell();
  if (event.deltaY !== 0 && host?.zoom) void host.zoom({ step: event.deltaY > 0 ? -1 : 1 }).catch(() => undefined);
}, { passive: false });

document.addEventListener("keydown", (event) => {
  if (!event.ctrlKey || event.metaKey || event.altKey) return;
  const digit = /^Digit([1-7])$/.exec(event.code);
  const section = digit ? SECTIONS[Number(digit[1]) - 1] : undefined;
  if (section && !event.shiftKey) { event.preventDefault(); show(section.id); return; }
  // Ctrl+B и Ctrl+J — как в Элен: полка слева и панель хода справа. Смотрим на код клавиши:
  // он один и тот же в любой раскладке, а в её полях ввода эти сочетания ничем не заняты.
  if (event.code === "KeyB") { event.preventDefault(); railButton.click(); }
  else if (event.code === "KeyJ" && !panelButton.hidden && !panelButton.disabled) { event.preventDefault(); panelButton.click(); }
});

async function paintShell(): Promise<void> {
  const host = shell();
  if (!host) { build.textContent = "страница открыта в браузере · оболочка программы недоступна"; return; }
  document.documentElement.classList.add("native");
  host.onMaximized(() => void paintChrome());
  const settings = await host.settings();
  build.textContent = `окно программы${settings.version ? " · " + settings.version : ""}`;
  void paintChrome();
}

onState(paintState);
buildNav();
labelRail();
// После Ctrl+R/F5 раздел остаётся тем же; при запуске программы окно открывается на «Сейчас».
const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
const remembered = navigation?.type === "reload" ? sessionStorage.getItem("praxis.window.view") : null;
show(SECTIONS.some((item) => item.id === remembered) ? (remembered as View) : "now");
warmAll();
startStateLoop();
void paintShell().then(labelRail).catch(() => { build.textContent = "окно программы"; });
