// Установщик Frame — сцена первого запуска.
//
// Кадр 1920×1080 масштабируется пропорционально до порога, ниже — обрезается.
// Навигация — невидимые четверти экрана слева и справа; явные контролы
// (кнопки окна, тема, поля ввода) четвертям не отдаются. Тема следует системе,
// ручное переопределение — кнопкой в верхней полосе.
import "./styles.css";
import { animate } from "motion";
import { COPY, MIN_SCALE, PRODUCT_NAME, STAGE } from "./config";
import { AboutScene } from "./scenes/about";
import { ConstitutionScene } from "./scenes/constitution";
import { InstallScene } from "./scenes/install";
import { InstalledScene } from "./scenes/installed";
import { UninstallScene } from "./scenes/uninstall";
import { KeysScene } from "./scenes/keys";
import { LegacyScene } from "./scenes/legacy";
import { NameScene } from "./scenes/name";
import { ModeScene } from "./scenes/mode";
import { TypewriterScene } from "./scenes/typewriter";
import { WordmarkScene } from "./scenes/wordmark";
import { installedSetup, isMac, loadDefaults, machine, setup, uninstallLaunch, type Setup } from "./setup";
import { T, sleep, type Dir } from "./wind";

// Сорвался модуль — окно не должно остаться пустым: оно рождается невидимым и
// показывается отсюда, поэтому исключение до show() давало живой процесс вообще
// без окна. В Rust есть вторая страховка (показ через 4 с), здесь — текст причины.
// Как зовётся установщик, тут узнаём у браузера (`navigator.platform`): ответ
// `defaults` до сорвавшегося модуля мог и не доехать.
function crashed(what: unknown) {
  const box = document.createElement("pre");
  box.style.cssText = "position:fixed;inset:0;z-index:9999;margin:0;padding:32px;white-space:pre-wrap;font:14px/1.5 monospace;background:#faf8f5;color:#2a2622;overflow:auto";
  const setupName = /Mac/.test(navigator.platform || "") ? "Helene Setup" : "helene-setup.exe";
  box.textContent =
    "Установщик не смог показать сцену.\n\n" +
    String(what) +
    `\n\nЗакрой окно и запусти ${setupName} ещё раз. Если повторяется — покажи этот текст автору.`;
  document.body.append(box);
  void import("@tauri-apps/api/window")
    .then((m) => m.getCurrentWindow().show())
    .catch(() => {});
}
addEventListener("error", (e) => crashed(e.error ?? e.message));
addEventListener("unhandledrejection", (e) => crashed(e.reason));

function q<E extends Element>(sel: string): E {
  const el = document.querySelector<E>(sel);
  if (!el) throw new Error(`нет элемента ${sel}`);
  return el;
}

const viewport = q<HTMLElement>("#viewport");
const stage = q<HTMLElement>("#stage");
const gust = q<HTMLElement>(".gust");
const hint = q<HTMLElement>("#hint");
const params = new URLSearchParams(location.search);
const inTauri = "__TAURI_INTERNALS__" in window;

// ---------------------------------------------------------------- кадр

function fit() {
  const s = Math.max(Math.min(innerWidth / STAGE.w, innerHeight / STAGE.h), MIN_SCALE);
  stage.style.setProperty("--scale", s.toFixed(4));
}
addEventListener("resize", fit);
fit();

// ---------------------------------------------------------------- тема

type Mode = keyof typeof COPY.theme;
const MODES: Mode[] = ["system", "light", "dark"];
const ICONS: Record<Mode, string> = {
  system: '<circle cx="8" cy="8" r="6"/><path class="fill" d="M8 2a6 6 0 0 1 0 12z"/>',
  light:
    '<circle cx="8" cy="8" r="3.1"/><path d="M8 1.6v1.9M8 12.5v1.9M1.6 8h1.9M12.5 8h1.9M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3M3.5 12.5l1.3-1.3M11.2 4.8l1.3-1.3"/>',
  dark: '<path d="M13.6 9.6A6 6 0 0 1 6.4 2.4a6 6 0 1 0 7.2 7.2z"/>',
};
const themeBtn = q<HTMLButtonElement>(".theme");
const themeIco = q<SVGElement>(".theme-ico");
const themeLabel = q<HTMLElement>(".theme-label");

function storage(get: string): string | null;
function storage(get: string, set: string): void;
function storage(key: string, value?: string): string | null | void {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch {
    return null;
  }
}

let mode: Mode = (() => {
  const raw = params.get("theme") ?? storage("frame.setup.theme");
  return MODES.includes(raw as Mode) ? (raw as Mode) : "system";
})();

function applyTheme() {
  if (mode === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = mode;
  themeIco.innerHTML = ICONS[mode];
  themeLabel.textContent = COPY.theme[mode];
}
themeBtn.addEventListener("click", () => {
  mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
  storage("frame.setup.theme", mode);
  applyTheme();
});
applyTheme();

// ---------------------------------------------------------------- окно

const win = inTauri ? (await import("@tauri-apps/api/window")).getCurrentWindow() : null;
for (const btn of document.querySelectorAll<HTMLButtonElement>(".win")) {
  if (!win) {
    btn.disabled = true;
    continue;
  }
  const action = btn.dataset.win;
  btn.addEventListener("click", () => {
    if (action === "minimize") void win.minimize();
    else if (action === "maximize") void win.toggleMaximize();
    else void win.close();
  });
}

// ---------------------------------------------------------------- сцены

const wordmark = new WordmarkScene(q<HTMLElement>(".scene-wordmark"), PRODUCT_NAME);
const about = new AboutScene(q<HTMLElement>(".scene-about"), COPY.about);
const typewriter = new TypewriterScene(q<HTMLElement>(".scene-typewriter"), COPY.typewriter);
const name = new NameScene(q<HTMLElement>(".scene-name"));
const constitution = new ConstitutionScene(q<HTMLElement>(".scene-constitution"));
const keys = new KeysScene(q<HTMLElement>(".scene-keys"));
const mode_ = new ModeScene(q<HTMLElement>(".scene-mode"));
const legacy = new LegacyScene(q<HTMLElement>(".scene-legacy"));
const install = new InstallScene(q<HTMLElement>(".scene-install"));
const uninstall = new UninstallScene(q<HTMLElement>(".scene-uninstall"));
const installed = new InstalledScene(q<HTMLElement>(".scene-installed"));
type Scene = WordmarkScene | AboutScene | TypewriterScene | NameScene | ConstitutionScene | KeysScene | ModeScene | LegacyScene | InstallScene | UninstallScene | InstalledScene;
// Режим окна задаёт оболочка: установка — все сцены, снятие — одна.
const uninstallMode = (window as Window & { SETUP_MODE?: string }).SETUP_MODE === "uninstall" || new URLSearchParams(location.search).get("mode") === "uninstall";
// Сцена «прежняя версия» в маршрут не входит: её вставляет start(), и только
// если SCM действительно ответила, что служба прежнего поколения жива. Чистая
// машина и машина с живой Vera дают два разных маршрута.
const scenes: Scene[] = uninstallMode ? [wordmark, uninstall] : [wordmark, about, typewriter, name, constitution, keys, mode_, install];
let byName: Record<string, number> = uninstallMode ? { uninstall: 1 } : { about: 1, typewriter: 2, name: 3, constitution: 4, keys: 5, mode: 6, install: 7 };

/** Вставить сцену в маршрут и пересобрать имена для `?scene=`. */
function insertScene(scene: Scene, before: Scene, key: string) {
  const at = scenes.indexOf(before);
  if (at < 0 || scenes.includes(scene)) return;
  scenes.splice(at, 0, scene);
  const names = Object.keys(byName);
  const rebuilt: Record<string, number> = {};
  for (const n of names) rebuilt[n] = scenes.indexOf(byNameScene(n));
  rebuilt[key] = at;
  byName = rebuilt;
}

function byNameScene(key: string): Scene {
  const table: Record<string, Scene> = {
    about, typewriter, name, constitution, keys, mode: mode_, install, uninstall, legacy, installed,
  };
  return table[key];
}
const nextLabel = q<HTMLElement>(".edge-next-label");
let index = 0;
let busy = false;
let touched = false;
let hintTimer = 0;

function showHint(text: string, delayMs: number) {
  clearTimeout(hintTimer);
  hintTimer = window.setTimeout(() => {
    hint.textContent = text;
    hint.classList.add("show");
  }, delayMs);
}

function hideHint() {
  clearTimeout(hintTimer);
  hint.classList.remove("show");
}

function canGo(dir: Dir): boolean {
  // В визарде снятия одна сцена: назад к надписи не ходим, край не показываем.
  if (uninstallMode && dir < 0) return false;
  const n = index + dir;
  return n >= 0 && n < scenes.length;
}

/** Порыв: широкая мягкая полоса проходит через окно вместе с ветром. */
function blowGust(dir: Dir) {
  const w = innerWidth;
  const from = dir > 0 ? -0.55 * w : 1.2 * w;
  const to = dir > 0 ? 1.2 * w : -0.55 * w;
  void animate(
    gust,
    { x: [from, to], opacity: [0, 1, 1, 0] },
    { duration: 1.9 * T, ease: [0.45, 0, 0.55, 1], times: [0, 0.2, 0.7, 1] },
  );
}

async function go(dir: Dir): Promise<void> {
  touched = true;
  hideHint();
  if (busy) return;
  // Сцена может забрать шаг вперёд себе: машинка сначала дописывает текст.
  if (dir > 0 && scenes[index] === typewriter && typewriter.finishNow()) {
    refreshEdge();
    return;
  }
  // Сцена с вводом не отпускает вперёд, пока не заполнена; установка началась —
  // назад дороги нет, всё уже пишется на диск.
  const current = scenes[index];
  if (dir < 0 && current === install && install.locked) return;
  const reason = dir > 0 && "validate" in current ? current.validate() : null;
  if (reason || !canGo(dir)) {
    busy = true;
    // Подсказка «почему не пускает» висит, пока причину не устранили: раньше она
    // гасла через 3,2 с, и человек, отведя глаза, оставался без причины вовсе.
    if (reason) showHint(reason, 0);
    if ("nudge" in current) await current.nudge();
    busy = false;
    return;
  }
  busy = true;
  const from = scenes[index];
  const to = scenes[index + dir];
  index += dir;
  refreshEdge();
  const leaving = from.leave(dir);
  if (from === wordmark) blowGust(dir);
  // Следующее приходит, пока прошлое ещё уносит: склейки нет.
  await sleep((from === wordmark ? 1.2 : 0.35) * T * 1000);
  await Promise.all([leaving, to.enter(dir)]);
  busy = false;
  refreshEdge();
  if (to === about && !edgeSeen) showHint(COPY.hint, 1800);
}

/** Прыжок к сцене не по соседству — тем же ветром, что `go`. */
async function jumpTo(to: Scene): Promise<void> {
  const at = scenes.indexOf(to);
  if (at < 0 || busy) return;
  busy = true;
  touched = true;
  hideHint();
  const from = scenes[index];
  index = at;
  refreshEdge();
  const leaving = from.leave(1);
  await sleep(0.35 * T * 1000);
  await Promise.all([leaving, to.enter(1)]);
  busy = false;
  refreshEdge();
}

/** «Обновить» поверх стоящей установки: решения — из неё самой, как у `--update`. */
async function updateInstalled(): Promise<void> {
  const inst = machine.installed;
  if (!inst) return;
  let decided: Setup | null = null;
  try {
    decided = await installedSetup(inst.dir);
  } catch {
    decided = null;
  }
  if (!decided) {
    // Решений в установке нет (имя, конституция) — обычный мастер, как у `--update`.
    showHint("В установке не хватает решений — пройдём мастер, это тоже установка поверх.", 0);
    void go(1);
    return;
  }
  Object.assign(setup, decided, { dir: inst.dir });
  await jumpTo(install);
  install.start();
}

/** «Удалить» со сцены «уже установлена»: та же сцена снятия, что у `--uninstall`. */
async function removeInstalled(): Promise<void> {
  if (!scenes.includes(uninstall)) {
    scenes.push(uninstall);
    byName = { ...byName, uninstall: scenes.length - 1 };
  }
  await jumpTo(uninstall);
}

// ---------------------------------------------------------------- навигация

let lastX = -1;
let edgeSeen = false;

function isControl(target: EventTarget | null): boolean {
  return target instanceof Element
    ? !!target.closest("[data-control], button, input, textarea, select, a, [contenteditable='true']")
    : false;
}

function refreshEdge(overControl = false) {
  let edge = "";
  if (lastX >= 0 && !overControl && !busy) {
    const x = lastX / innerWidth;
    if (x < 0.25 && canGo(-1)) edge = "left";
    else if (x > 0.75 && canGo(1)) edge = "right";
  }
  if (edge) {
    edgeSeen = true;
    hideHint();
  }
  nextLabel.textContent = scenes[index] === typewriter ? typewriter.nextLabel() : "Далее";
  viewport.dataset.edge = edge;
}

viewport.addEventListener("mousemove", (e) => {
  lastX = e.clientX;
  refreshEdge(isControl(e.target));
});
viewport.addEventListener("mouseleave", () => {
  lastX = -1;
  refreshEdge();
});
viewport.addEventListener("click", (e) => {
  if (isControl(e.target)) return;
  const x = e.clientX / innerWidth;
  if (x < 0.25) void go(-1);
  else if (x > 0.75) void go(1);
});
// Заполнил поле — причина, по которой не пускало, снимается сама.
document.addEventListener("input", hideHint);
addEventListener("keydown", (e) => {
  const button = e.target instanceof HTMLButtonElement;
  if (isControl(e.target) && !button) return;
  // Enter и Space на кнопке — это её собственное нажатие (click от Enter/Space
  // гасится preventDefault). Пока мы перехватывали их и здесь, кнопки визарда
  // («Проверить ключ», «Войти в ChatGPT», «Установить», «Снять», карточки
  // выбора) с клавиатуры не нажимались вовсе, а сцена уезжала вперёд.
  if (button && (e.key === " " || e.key === "Enter")) return;
  if (["ArrowRight", " ", "Enter", "PageDown"].includes(e.key)) {
    e.preventDefault();
    void go(1);
  } else if (["ArrowLeft", "Backspace", "PageUp"].includes(e.key)) {
    e.preventDefault();
    void go(-1);
  }
});

// ---------------------------------------------------------------- старт

async function start() {
  try {
    await Promise.all([
      document.fonts.load('400 100px "Source Serif 4"'),
      document.fonts.load('400 16px "Golos Text"'),
      document.fonts.load('500 16px "Golos Text"'),
    ]);
  } catch {
    // без шрифтов сцена всё равно идёт — на запасных
  }
  if (win) {
    // Окно родилось невидимым: показать после первого кадра, без белой вспышки.
    // Показ не должен зависеть от того, поднялись ли шрифты и сцены.
    try {
      await new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r())));
      await win.show();
      await win.setFocus();
    } catch (e) {
      console.error("окно не показалось", e);
    }
  }
  const freeze = Number(params.get("freeze"));
  if (freeze > 0) {
    setTimeout(() => document.getAnimations().forEach((a) => a.pause()), freeze);
  }
  let shipped = "";
  try {
    const d = await loadDefaults();
    shipped = String(d.version || "");
    if (d.dir) setup.dir = d.dir;
    // Если что-то уже стоит — это обновление, и сводка перед кнопкой скажет об этом.
    machine.installed = d.installed ?? null;
    machine.inPlace = !!d.in_place;
    if (d.installed?.dir) setup.dir = d.installed.dir;
    // Система — по слову оболочки: по нему сцены прячут службу, тело и брандмауэр.
    machine.platform = String(d.platform || "").trim().toLowerCase();
  } catch {
    // без оболочки папка останется примером
  }
  // 26.09: Hélène уже стоит — первой сценой «уже установлена» (обновить / удалить /
  // настроить заново), а не мастер с именем и конституцией, как при первой установке.
  if (!uninstallMode && machine.installed) {
    insertScene(installed, about, "installed");
    installed.bind({
      update: () => void updateInstalled(),
      // На месте (установка NSIS) удаление — его uninstall.exe: он снимет службу,
      // файлы, ярлыки и запись в «Приложениях» и спросит про данные.
      remove: () => void (machine.inPlace
        ? uninstallLaunch().catch((e) => showHint("Не запустился uninstall.exe: " + String(e), 0))
        : removeInstalled()),
      fresh: () => void go(1),
    }, shipped);
  }
  // Прежние поколения продукта. Спрашиваем SCM ДО всех решений: если на машине
  // живёт служба Vera/Frame (снятие прошлой версии сносило файлы, а службу
  // оставляло), владелец узнаёт об этом здесь, а не после установки — её реле
  // держит тот же порт, и новый агент ушёл бы разговаривать с ним.
  // На macOS служб прежних поколений не бывало: оболочка отвечает пустым
  // списком, а сюда — вторая страховка, чтобы сцену не вставить и по ошибке.
  try {
    const home = machine.installed?.dir || setup.dir;
    if (!isMac() && (await legacy.look(home))) insertScene(legacy, uninstallMode ? uninstall : mode_, "legacy");
  } catch {
    // SCM не ответила — маршрут остаётся прежним, молча ничего не снимаем
  }
  const jump = params.get("scene");
  if (jump && jump in byName) {
    index = byName[jump];
    wordmark.root.hidden = true;
    const target = scenes[index] as Exclude<Scene, WordmarkScene>;
    if (params.has("static")) target.setStatic();
    else await target.enter(1);
    refreshEdge();
    return;
  }
  if (params.has("static")) {
    wordmark.root.hidden = false;
    wordmark.settle();
    wordmark.state = "settled";
    return;
  }
  const settled = await wordmark.appear();
  if (!settled) return; // человек уже пошёл дальше сам
  await sleep(1400 * T);
  if (wordmark.state === "settled" && !touched) void go(1);
}

declare global {
  interface Window {
    __frame?: {
      go: typeof go;
      index: () => number;
      busy: () => boolean;
      about: AboutScene;
      animate: typeof animate;
    };
  }
}
// Отладочная ручка для проверки из встроенного браузера; в продукте безвредна.
window.__frame = { go, index: () => index, busy: () => busy, about, animate };

void start();
