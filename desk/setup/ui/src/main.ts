// Установщик Frame — сцена первого запуска.
//
// Кадр 1920×1080 масштабируется пропорционально до порога, ниже — обрезается.
// Навигация — невидимые четверти экрана слева и справа; явные контролы
// (кнопки окна, тема, поля ввода) четвертям не отдаются. Тема следует системе,
// ручное переопределение — кнопкой в верхней полосе.
import "./styles.css";
// Облик «Почерк» (28.09) — слоем поверх: бумага, линия, терракота, почерк.
import "./paper.css";
import { animate } from "motion";
import { COPY, MIN_SCALE, STAGE, isPraxis } from "./config";
import { ConstitutionScene } from "./scenes/constitution";
import { FoundScene, resumable } from "./scenes/found";
import { InstallScene } from "./scenes/install";
import { InstalledScene } from "./scenes/installed";
import { UninstallScene } from "./scenes/uninstall";
import { KeysScene } from "./scenes/keys";
import { LegacyScene } from "./scenes/legacy";
import { NameScene } from "./scenes/name";
import { ModeScene } from "./scenes/mode";
import { WhereScene } from "./scenes/where";
import { installedSetup, isMac, loadDefaults, machine, setup, uninstallLaunch, type Found, type Setup } from "./setup";
import { T, sleep, type Dir } from "./wind";
import { paperButton, paperDialog } from "../../../ui-kit/paper-dialog";
import { setupWindowClose } from "./window-close";
import { startSetupTips } from "./tips";

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

const stage = q<HTMLElement>("#stage");
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
    else void requestClose();
  });
}

// ---------------------------------------------------------------- сцены

const name = new NameScene(q<HTMLElement>(".scene-name"));
const constitution = new ConstitutionScene(q<HTMLElement>(".scene-constitution"));
const keys = new KeysScene(q<HTMLElement>(".scene-keys"));
const mode_ = new ModeScene(q<HTMLElement>(".scene-mode"));
const legacy = new LegacyScene(q<HTMLElement>(".scene-legacy"));
const install = new InstallScene(q<HTMLElement>(".scene-install"));
const update = new InstallScene(q<HTMLElement>(".scene-update"), true);
const uninstall = new UninstallScene(q<HTMLElement>(".scene-uninstall"));
const installed = new InstalledScene(q<HTMLElement>(".scene-installed"));
const found = new FoundScene(q<HTMLElement>(".scene-found"));
const where = new WhereScene(q<HTMLElement>(".scene-where"));
type Scene = NameScene | ConstitutionScene | KeysScene | WhereScene | ModeScene | LegacyScene | InstallScene | UninstallScene | InstalledScene | FoundScene;
// Режим окна задаёт оболочка: установка — все сцены, снятие — одна.
const uninstallMode = (window as Window & { SETUP_MODE?: string }).SETUP_MODE === "uninstall" || new URLSearchParams(location.search).get("mode") === "uninstall";
// Сцена «прежняя версия» в маршрут не входит: её вставляет start(), и только
// если SCM действительно ответила, что служба прежнего поколения жива. Чистая
// машина и машина с живой Vera дают два разных маршрута.
// 1.2: «Для кого» (для меня / для всех) — перед экраном режима: сначала где агенту
// жить, потом что ему можно.
// Praxis (окно к своему серверу) — короче: ни имени, ни конституции, ни модели — это
// всё живёт на сервере; адрес и ключ канала окно спросит само при первом запуске.
// 28.09 (Егор: «визарды остопиздели ужасно»): заставки-слова, презентации и печатной
// машинки больше нет — мастер начинается сразу с дела.
const scenes: Scene[] = uninstallMode
  ? [uninstall]
  : isPraxis()
    ? [where, install]
    : [name, constitution, keys, where, mode_, install];
let byName: Record<string, number> = uninstallMode
  ? { uninstall: 0 }
  : isPraxis()
    ? { where: 0, install: 1 }
    : { name: 0, constitution: 1, keys: 2, where: 3, mode: 4, install: 5 };

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
    name, constitution, keys, where, mode: mode_, install, update, uninstall, legacy, installed, found,
  };
  return table[key];
}
const nextLabel = q<HTMLElement>(".step-next-label");
const backBtn = q<HTMLButtonElement>(".step-back");
const nextBtn = q<HTMLButtonElement>(".step-next");
const stepCount = q<HTMLElement>(".step-count");
backBtn.addEventListener("click", () => void go(-1));
nextBtn.addEventListener("click", () => void go(1));
let index = 0;
let busy = false;
let hintTimer = 0;
let closeDialog: HTMLDialogElement | null = null;
function closingDuringInstall(): boolean {
  const current = scenes[index];
  if (current !== install && current !== update || !current.isRunning) return false;
  if (closeDialog?.open) { closeDialog.focus(); return true; }
  const ui = paperDialog(current === update ? "Обновление ещё идёт" : "Установка ещё идёт"); closeDialog = ui.dialog;
  const words = document.createElement("p"); words.className = "paper-lead";
  words.textContent = current.canCancel ? "Чтобы закончить сейчас, отмени операцию. Установщик дождётся отката и покажет результат."
    : "Сейчас версии меняются местами. Дождись результата — окно снова можно будет закрыть.";
  ui.body.append(words); ui.footer.append(paperButton("Вернуться", () => ui.dialog.close(), true));
  if (current.canCancel) ui.footer.append(paperButton(current === update ? "Отменить обновление" : "Отменить установку", () => { ui.dialog.close(); current.requestCancel(); }));
  ui.dialog.addEventListener("close", () => { closeDialog = null; }, { once: true }); return true;
}
const requestClose = setupWindowClose(win, closingDuringInstall, () => {
  hint.textContent = "Окно не закрылось. Попробуй ещё раз."; hint.classList.add("show");
});

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
  const n = index + dir;
  return n >= 0 && n < scenes.length;
}

async function go(dir: Dir): Promise<void> {
  hideHint();
  if (busy) return;
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
  // Следующее приходит, пока прошлое ещё уходит: склейки нет.
  await sleep(0.12 * T * 1000);
  await Promise.all([leaving, to.enter(dir)]);
  busy = false;
  refreshEdge();
}

/** Прыжок к сцене не по соседству — тем же ветром, что `go`. */
async function jumpTo(to: Scene): Promise<void> {
  const at = scenes.indexOf(to);
  if (at < 0 || busy) return;
  busy = true;
  hideHint();
  const from = scenes[index];
  index = at;
  refreshEdge();
  if (to === update || from === update) {
    // An update is one continuous sheet, with no overlapping wizard headings.
    from.root.hidden = true;
    to.setStatic();
    const heading = to.root.querySelector<HTMLElement>("h2");
    if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    busy = false; refreshEdge(); return;
  }
  const leaving = from.leave(1);
  await sleep(0.12 * T * 1000);
  await Promise.all([leaving, to.enter(1)]);
  busy = false;
  refreshEdge();
}

/** «Обновить» поверх стоящей установки: решения — из неё самой, как у `--update`. */
async function updateInstalled(): Promise<void> {
  const inst = machine.installed;
  if (!inst || busy || update.locked) return;
  if (!scenes.includes(update)) { scenes.push(update); byName.update = scenes.length - 1; }
  update.prepare(shippedVersion);
  await jumpTo(update);
  busy = true;
  let decided: Setup | null = null;
  try {
    decided = await installedSetup(inst.dir);
  } catch (error) {
    busy = false;
    update.preparationFailed("Не удалось прочитать текущие настройки: " + String(error), () => void updateInstalled(), () => void jumpTo(installed));
    refreshEdge(); return;
  }
  if (!decided && isPraxis()) {
    // Praxis: решать нечего — только папка и режим стоящего.
    Object.assign(setup, { dir: inst.dir, scope: machine.installedFound?.scope || "" });
    busy = false; update.start(); refreshEdge();
    return;
  }
  if (!decided) {
    // Решений в установке нет (имя, конституция) — обычный мастер, как у `--update`.
    busy = false;
    update.preparationFailed("Не удалось прочитать настройки установленной Hélène. Обновление не началось; текущая версия и её данные на месте.", () => void updateInstalled(), () => void jumpTo(installed));
    refreshEdge();
    return;
  }
  Object.assign(setup, decided, { dir: inst.dir, scope: machine.installedFound?.scope || "" });
  busy = false; update.start(); refreshEdge();
}

/** «Продолжить с <имя>» (1.2): решения — из найденной памяти, сама память уходит в
 *  установку копией; дальше — «для кого» и установка. Решений не хватает (нет имени
 *  или конституции) — обычный маршрут, но с найденной памятью. */
async function resumeFound(f: Found): Promise<void> {
  let decided: Setup | null = null;
  if (f.decisions) {
    try {
      decided = await installedSetup(f.dir);
    } catch {
      decided = null;
    }
  }
  if (decided) Object.assign(setup, decided);
  setup.carry_from = f.dir;
  setup.dir = "";
  if (f.scope) setup.scope = f.scope;
  if (!scenes.includes(where)) return;
  if (decided) {
    await jumpTo(where);
  } else {
    showHint("В найденной памяти не хватает решений — пройдём имя, конституцию и модель.", 0);
    await jumpTo(name);
  }
}

/** «Удалить» со сцены «уже установлена»: та же сцена снятия, что у `--uninstall`. */
async function removeInstalled(): Promise<void> {
  uninstall.setDir(machine.installedFound?.dir || machine.installed?.dir || "");
  if (!scenes.includes(uninstall)) {
    scenes.push(uninstall);
    byName = { ...byName, uninstall: scenes.length - 1 };
  }
  await jumpTo(uninstall);
}

// ---------------------------------------------------------------- навигация

function isControl(target: EventTarget | null): boolean {
  return target instanceof Element
    ? !!target.closest("[data-control], button, input, textarea, select, a, [contenteditable='true']")
    : false;
}

/**
 * Полоса шагов внизу: где ты, сколько осталось, можно ли назад и вперёд.
 * Сцены со своими кнопками (уже установлена, нашлась память, установка, снятие) ведут
 * сами — «Далее» там не нужно; начатая установка назад не пускает.
 */
function refreshEdge() {
  const current = scenes[index];
  const own = current === installed || current === found || current === install || current === update || current === uninstall;
  const locked = (current === install && install.locked) || (current === update && update.locked) || (current === uninstall && uninstall.locked);
  backBtn.hidden = !canGo(-1) || locked || current === update;
  nextBtn.hidden = own || !canGo(1);
  backBtn.disabled = busy;
  nextBtn.disabled = busy;
  const steps = scenes.filter((s) => s !== installed && s !== found && s !== legacy && s !== update);
  const at = steps.indexOf(current);
  stepCount.textContent = at >= 0 && steps.length > 1 ? `Шаг ${at + 1} из ${steps.length}` : "";
  nextLabel.textContent = scenes[index + 1] === install ? "К установке" : "Далее";
}

// Заполнил поле — причина, по которой не пускало, снимается сама.
document.addEventListener("input", hideHint);
addEventListener("keydown", (e) => {
  if (scenes[index] === update || q<HTMLElement>("#setup-boot").hidden === false) return;
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

let shippedVersion = "";
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
    machine.found = d.found ?? [];
    machine.installedFound = machine.found.find((f) => f.kind === "installed" && (!d.installed || f.dir.toLowerCase() === d.installed.dir.toLowerCase())) ?? machine.found.find((f) => f.kind === "installed") ?? null;
    machine.elevated = !!d.elevated;
    machine.userDir = d.user_dir || d.dir || "";
    machine.machineDir = d.machine_dir || "";
    machine.nsis = !!d.nsis;
    if (d.installed?.dir) setup.dir = d.installed.dir;
    // Система — по слову оболочки: по нему сцены прячут службу, тело и брандмауэр.
    machine.platform = String(d.platform || "").trim().toLowerCase();
  } catch (error) {
    if (inTauri) {
      const boot = q<HTMLElement>("#setup-boot"); boot.replaceChildren();
      const title = document.createElement("h2"); title.className = "form-head"; title.textContent = "Не удалось проверить установку";
      const reason = document.createElement("p"); reason.className = "form-lead"; reason.textContent = String(error);
      const retry = document.createElement("button"); retry.className = "form-button primary"; retry.textContent = "Повторить"; retry.addEventListener("click", () => void start()); boot.append(title,reason,retry); return;
    }
  }
  shippedVersion = shipped;
  // 26.09: Hélène уже стоит — первой сценой «уже установлена» (обновить / удалить /
  // настроить заново), а не мастер с именем и конституцией, как при первой установке.
  if (!uninstallMode && machine.installed) {
    insertScene(installed, scenes[0], "installed");
    installed.bind({
      update: () => void updateInstalled(),
      // На месте (установка NSIS) удаление — его uninstall.exe: он снимет службу,
      // файлы, ярлыки и запись в «Приложениях» и спросит про данные.
      // 1.2: uninstall.exe есть только у установок NSIS 1.1.x; остальное снимает сам мастер.
      remove: () => void (machine.inPlace && machine.nsis
        ? uninstallLaunch().catch((e) => showHint("Не запустился uninstall.exe: " + String(e), 0))
        : removeInstalled()),
      fresh: () => void go(1),
    }, shipped);
  } else if (!uninstallMode && !isPraxis() && resumable(machine.found).length) {
    // 1.2: программы нет, а память агента нашлась — предложить продолжить с ней.
    insertScene(found, name, "found");
    found.bind({
      resume: (f) => void resumeFound(f),
      fresh: () => void go(1),
    });
  }
  // Прежние поколения продукта. Спрашиваем SCM ДО всех решений: если на машине
  // живёт служба Vera/Frame (снятие прошлой версии сносило файлы, а службу
  // оставляло), владелец узнаёт об этом здесь, а не после установки — её реле
  // держит тот же порт, и новый агент ушёл бы разговаривать с ним.
  // На macOS служб прежних поколений не бывало: оболочка отвечает пустым
  // списком, а сюда — вторая страховка, чтобы сцену не вставить и по ошибке.
  try {
    const home = machine.installed?.dir || setup.dir;
    if (!isMac() && !isPraxis() && (await legacy.look(home))) insertScene(legacy, uninstallMode ? uninstall : mode_, "legacy");
  } catch {
    // SCM не ответила — маршрут остаётся прежним, молча ничего не снимаем
  }
  // Сразу к делу: первая сцена маршрута (или та, что названа в `?scene=`).
  const jump = params.get("scene");
  if (jump && jump in byName) index = byName[jump];
  const first = scenes[index];
  refreshEdge();
  // The first visible route is already laid out. Boot has its own honest frame,
  // so no wizard controls flash while defaults/installed detection are pending.
  first.setStatic();
  q<HTMLElement>("#setup-boot").hidden = true;
  q<HTMLElement>(".stepbar").hidden = false;
  refreshEdge();
}

declare global {
  interface Window {
    __frame?: {
      go: typeof go;
      index: () => number;
      busy: () => boolean;
      animate: typeof animate;
    };
  }
}
// Отладочная ручка для проверки из встроенного браузера; в продукте безвредна.
window.__frame = { go, index: () => index, busy: () => busy, animate };

void start();
if (!uninstallMode) {
  const stopTips = startSetupTips(stage, () => hint.classList.contains("show") || !!closeDialog?.open);
  addEventListener("pagehide", stopTips, { once: true });
}
