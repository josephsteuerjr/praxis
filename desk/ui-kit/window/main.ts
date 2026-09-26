// Окно как таковое: полка слева (разделы и чаты), переписка в центре, ход агента
// справа; состояние агента одной фразой в шапке; настройки экраном, а не файлом.
// Большой надписи с именем продукта нет — только подпись внизу полки (слово
// владельца 07.09).
//
// ⚠ ПОЧЕМУ ЭТО ФУНКЦИЯ, А НЕ СКРИПТ. До 10.09 файл был телом окна Элен и
// выполнялся при импорте, а издание к серверу поднималось тем же файлом с ветками
// `cfg.needs_remote` внутри. Теперь окно поднимает ИЗДАНИЕ: `start()` строит всё
// общее, а издание приносит своё — экран настроек и, если нужно, перехват
// первого запуска. Веток про удалённый харнесс здесь нет ни одной.
import "./styles/app.css";
import { api, cfg, connect, inTauri, onConnection, onEvent, post, shell } from "../../ui-kit/window/api";
import { applyTheme } from "../../ui-kit/dom";
import { watchShellVersion } from "../../ui-kit/version";
import { setResultFetcher } from "../../ui-kit/steps";
import { bindFail, esc, failHTML, fmtAge, fmtDur, fmtK, fmtTs, humanError, q, toast } from "../../ui-kit/window/lib";
import { LEGACY_WINDOW_KEY, PRODUCT_NAME, S, setLocalAgent, setProductName, WINDOW_ROOM, foreignHarness, isWindowRoom, runIsRecent, type AgentState, type Pending, type Room, type View } from "../../ui-kit/window/state";
import { hostInfo } from "../../ui-kit/window/host";
import { clientIsMac, isMacPlatform, kbdLabel, platformOf } from "../../ui-kit/platform";
import { buildRooms, createRoom, deleteRoom, fetchRooms, renameRoom } from "../../ui-kit/window/rooms";
import { mountSwitch } from "../../ui-kit/window/agents";
import * as panel from "../../ui-kit/window/panel";
import * as now from "../../ui-kit/window/views/now";
import * as talk from "../../ui-kit/window/views/talk";
import * as wakes from "../../ui-kit/window/views/wakes";
import * as plans from "../../ui-kit/window/views/plans";
import * as frame from "../../ui-kit/window/views/frame";
import * as files from "../../ui-kit/window/views/files";
import * as journal from "../../ui-kit/window/views/journal";
import * as anatomy from "../../ui-kit/window/views/anatomy";
import * as learn from "../../ui-kit/window/views/learn";
import * as settings from "../../ui-kit/window/views/settings-frame";

/** Чем издание отличается от голого окна. */
export interface WindowOptions {
  /** Издание экрана «Настройки»: какие карточки рисовать и что писать в конфиг. */
  settingsEdition: settings.EditionFactory;
  /**
   * Перехват первого запуска. Вернуло `true` — издание заняло экран собой, и
   * окно к каналу НЕ подключается: связываться пока не с кем.
   *
   * Нужен Praxis: у него нет установщика по замыслу (поставка распаковывается),
   * и адрес сервера спрашивается прямо в окне. У Элен харнесс рядом, и первый
   * запуск перехватывать незачем.
   */
  firstRun?: (view: HTMLElement) => boolean;
  /**
   * Как зовётся ЭТО приложение, пока канал не сказал иначе.
   *
   * Нужно потому, что приложений два: «Hélène», вшитая в общий слой, подписывала
   * бы Praxis чужим именем — в заголовке окна, на полке и в служебных плашках
   * чата. Живое имя из канала (`cfg.product`) главнее.
   */
  productName: string;
  /**
   * Живёт ли агент на ЭТОЙ машине. У Элен — да, у окна к серверу — нет.
   *
   * По этому флагу гейтятся карточки, которые за каналом не про что: папки
   * этого компьютера, управление им. По умолчанию `true`, чтобы старое
   * издание не потеряло свои карточки молча.
   */
  localAgent?: boolean;
}

export function start(opts: WindowOptions): void {
  setProductName(opts.productName);
  setLocalAgent(opts.localAgent !== false);


  // Длинный результат руки или её слово дочитываются файлом прогона по кнопке в ленте шагов.
  setResultFetcher((run, rid) => api(`/api/run/${encodeURIComponent(run)}/result/${encodeURIComponent(rid)}`));

  const view = q<HTMLElement>("#view");
  const app = q<HTMLElement>("#app");
  const railNav = q<HTMLElement>("#rail-nav");
  const railBottom = q<HTMLElement>("#rail-bottom");
  const railSign = q<HTMLElement>("#rail-sign");
  const roomsBox = q<HTMLElement>("#rooms");
  const roomAdd = q<HTMLButtonElement>("#room-add");
  const headKicker = q<HTMLElement>("#head-kicker");
  const headTitle = q<HTMLElement>("#head-title");
  const statePill = q<HTMLElement>("#state");
  const stateText = q<HTMLElement>("#state-text");
  const stateAction = q<HTMLButtonElement>("#state-action");
  const pulseBox = q<HTMLElement>("#pulse");
  const alarmBox = q<HTMLElement>("#alarm");
  const composer = q<HTMLElement>("#composer");
  const composerTarget = q<HTMLElement>("#composer-target");
  const composerNote = q<HTMLElement>("#composer-note");
  const say = q<HTMLTextAreaElement>("#say");
  const send = q<HTMLButtonElement>("#send");
  const attachBtn = q<HTMLButtonElement>("#attach");
  const attachInput = q<HTMLInputElement>("#attach-input");
  const composerFiles = q<HTMLElement>("#composer-files");
  const composerBox = q<HTMLElement>(".composer-box");

  // ---------------------------------------------------------------- вложения (0.5.0)
  // Картинка к реплике: скрепка, вставка из буфера, перетаскивание. Файл уезжает в
  // /api/say как base64 рядом с текстом; канал кладёт его в desk_inbox, руннер — в
  // медиа-спул, дерево — в кадр, а модель переключается на зрячую до вызова.
  // Только то, что модель читает (PNG/JPEG/WebP/GIF), до четырёх и до 8 МБ.
  // Голосовое (0.6.0) — запись микрофона тем же путём: руннер расшифровывает её
  // whisper-ом, как голосовые из Telegram, и кладёт текст в реплику.
  interface Attachment { name: string; mime: string; data: string; url: string; size: number; kind: "image" | "audio"; seconds?: number }
  const ATTACH_MIME = new Set(["image/png", "image/jpeg", "image/webp", "image/gif"]);
  const AUDIO_MIME = new Set(["audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav"]);
  const ATTACH_MAX = 4;
  const ATTACH_BYTES = 8 * 1024 * 1024;
  let attachments: Attachment[] = [];
  const baseMime = (t: string) => String(t || "").split(";", 1)[0].trim().toLowerCase();

  function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = () => reject(r.error);
      r.onload = () => resolve(String(r.result || "").split(",", 2)[1] || "");
      r.readAsDataURL(file);
    });
  }

  async function addFiles(files: Iterable<File>, seconds?: number) {
    for (const f of files) {
      const mime = baseMime(f.type);
      const kind: Attachment["kind"] = AUDIO_MIME.has(mime) ? "audio" : "image";
      if (kind === "image" && !ATTACH_MIME.has(mime)) { toast(`${f.name || "файл"}: модель читает только PNG, JPEG, WebP и GIF; голосовое — кнопкой микрофона`); continue; }
      if (f.size > ATTACH_BYTES) { toast(`${f.name || "файл"}: больше 8 МБ`); continue; }
      if (attachments.length >= ATTACH_MAX) { toast(`Не больше ${ATTACH_MAX} вложений за раз`); break; }
      try {
        const data = await fileToBase64(f);
        attachments.push({ name: f.name || (kind === "audio" ? "voice" : "image"), mime, data, url: URL.createObjectURL(f), size: f.size, kind, seconds });
      } catch (e) {
        toast("Не прочиталось: " + humanError(e).text);
      }
    }
    paintFiles();
  }

  function dropFile(i: number) {
    const [gone] = attachments.splice(i, 1);
    if (gone) URL.revokeObjectURL(gone.url);
    paintFiles();
  }

  function clearFiles() {
    for (const a of attachments) URL.revokeObjectURL(a.url);
    attachments = [];
    paintFiles();
  }

  function paintFiles() {
    composerFiles.hidden = attachments.length === 0;
    composerFiles.innerHTML = attachments
      .map((a, i) => a.kind === "audio"
        ? `<span class="chip chip-voice"><span class="chip-ico" aria-hidden="true">🎤</span><span>голосовое${a.seconds ? " · " + fmtDur(a.seconds) : ""}</span> <span class="muted">${fmtK(a.size)}</span><button type="button" data-i="${i}" title="Убрать" aria-label="Убрать">×</button></span>`
        : `<span class="chip"><img src="${a.url}" alt=""><span>${esc(a.name)}</span> <span class="muted">${fmtK(a.size)}</span><button type="button" data-i="${i}" title="Убрать" aria-label="Убрать">×</button></span>`)
      .join("");
    composerFiles.querySelectorAll<HTMLButtonElement>("button[data-i]").forEach((b) => {
      b.addEventListener("click", () => dropFile(Number(b.dataset.i)));
    });
  }

  attachBtn.addEventListener("click", () => attachInput.click());
  attachInput.addEventListener("change", () => {
    void addFiles(attachInput.files ? Array.from(attachInput.files) : []);
    attachInput.value = "";
  });

  // ---------------------------------------------------------------- голосовое (0.6.0)
  // Кнопка микрофона есть только в окне Элен (app/index.html): у издания к серверу
  // нет раннера, расшифровывать некому. Запись — MediaRecorder в webm/opus, до
  // пяти минут; вторая кнопка — стоп; результат ложится вложением рядом с текстом.
  // Готовность слуха спрашивается у канала (`/api/voice`) ДО записи: записывать то,
  // что некому расшифровать, — обещание без исполнения, и владелец узнал бы об
  // этом уже из ответа агента.
  const micBtn = document.querySelector<HTMLButtonElement>("#mic");
  const MIC_MAX_SEC = 300;
  let rec: MediaRecorder | null = null;
  let recStream: MediaStream | null = null;
  let recChunks: Blob[] = [];
  let recStart = 0;
  let recTick = 0;
  const micIdle = () => {
    if (!micBtn) return;
    micBtn.classList.remove("recording");
    micBtn.title = "Записать голосовое";
    micBtn.setAttribute("aria-label", "Записать голосовое");
    if (recTick) { clearInterval(recTick); recTick = 0; }
    if (recStream) { for (const t of recStream.getTracks()) t.stop(); recStream = null; }
    rec = null;
  };
  async function micToggle() {
    if (!micBtn) return;
    if (rec) {
      if (rec.state !== "inactive") rec.stop();
      return;
    }
    try {
      const v = await api<{ ready?: boolean; why?: string }>("/api/voice");
      if (!v.ready) { toast(`Голосовое некому расшифровать: ${v.why || "слух не поднят"}. Настройки → Голос.`); return; }
    } catch {
      // Канал не ответил — не запрещаем: причину, если что, назовёт руннер в реплике.
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      toast("В этом окне нет доступа к микрофону");
      return;
    }
    try {
      recStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      toast("Микрофон не дали: " + humanError(e).text);
      return;
    }
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"].find((m) => MediaRecorder.isTypeSupported(m)) || "";
    try {
      rec = new MediaRecorder(recStream, mime ? { mimeType: mime } : undefined);
    } catch (e) {
      toast("Запись не началась: " + humanError(e).text);
      micIdle();
      return;
    }
    recChunks = [];
    recStart = Date.now();
    rec.ondataavailable = (e) => { if (e.data && e.data.size) recChunks.push(e.data); };
    rec.onerror = () => { toast("Запись оборвалась"); micIdle(); };
    rec.onstop = () => {
      const seconds = Math.max(1, Math.round((Date.now() - recStart) / 1000));
      const type = baseMime(rec?.mimeType || mime || "audio/webm") || "audio/webm";
      const blob = new Blob(recChunks, { type });
      micIdle();
      if (blob.size < 1024 || seconds < 1) { toast("Запись слишком короткая"); return; }
      const ext = type === "audio/ogg" ? "ogg" : type === "audio/mp4" ? "m4a" : "webm";
      const stamp = new Date().toISOString().replace(/[-:]/g, "").slice(0, 15);
      void addFiles([new File([blob], `voice-${stamp}.${ext}`, { type })], seconds);
    };
    rec.start(250);
    micBtn.classList.add("recording");
    recTick = window.setInterval(() => {
      const s = Math.round((Date.now() - recStart) / 1000);
      micBtn.title = `Идёт запись · ${fmtDur(s)} · нажми, чтобы закончить`;
      micBtn.setAttribute("aria-label", micBtn.title);
      if (s >= MIC_MAX_SEC && rec && rec.state !== "inactive") { toast("Пять минут — предел одного голосового"); rec.stop(); }
    }, 500);
  }
  micBtn?.addEventListener("click", () => void micToggle());
  say.addEventListener("paste", (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    const files: File[] = [];
    for (const it of Array.from(items)) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      void addFiles(files);
    }
  });
  for (const ev of ["dragenter", "dragover"] as const) {
    composerBox.addEventListener(ev, (e) => {
      if (e.dataTransfer?.types.includes("Files")) {
        e.preventDefault();
        composerBox.classList.add("dropping");
      }
    });
  }
  composerBox.addEventListener("dragleave", () => composerBox.classList.remove("dropping"));
  composerBox.addEventListener("drop", (e) => {
    composerBox.classList.remove("dropping");
    if (e.dataTransfer?.files?.length) {
      e.preventDefault();
      void addFiles(Array.from(e.dataTransfer.files));
    }
  });
  const panelBox = q<HTMLElement>("#panel");
  const menu = q<HTMLElement>("#menu");

  // ---------------------------------------------------------------- тема

  // Тема — как в системе (слово владельца 07.09): светлая днём, тёмная ночью,
  // без переключателя в окне.
  applyTheme("system");

  // Веб-версия окна (Praxis за каналом) обновляется сама, когда на сервере новая
  // сборка; в оболочке helene:// сверка тихо не срабатывает — там статика с диска.
  if (!inTauri) watchShellVersion();

  // ---------------------------------------------------------------- окно

  if (inTauri) {
    document.documentElement.classList.add("native");
    import("@tauri-apps/api/window").then(({ getCurrentWindow }) => {
      const w = getCurrentWindow();
      for (const b of document.querySelectorAll<HTMLButtonElement>(".win")) {
        const action = b.dataset.win;
        b.addEventListener("click", () => {
          if (action === "minimize") void w.minimize();
          else if (action === "maximize") void w.toggleMaximize();
          else void w.close();
        });
      }
    });
  }

  // ---------------------------------------------------------------- разделы

  const ICONS: Record<View, string> = {
    now: '<path d="M3.5 10h3l2-5 3 10 2-5h3"/>',
    wakes: '<circle cx="10" cy="10.5" r="5.5"/><path d="M10 7.5v3l2 1.5M6 3.5 3.5 5.5M14 3.5l2.5 2"/>',
    talk: '<path d="M4 5.5h12v8H8l-4 3z"/>',
    plans: '<rect x="3.5" y="4" width="13" height="12" rx="2"/><path d="M6.5 2.8v2.5M13.5 2.8v2.5M6.5 8h7M6.5 11h4"/>',
    frame: '<path d="M3.5 6.5V4.8c0-.7.6-1.3 1.3-1.3h1.7M13.5 3.5h1.7c.7 0 1.3.6 1.3 1.3v1.7M16.5 13.5v1.7c0 .7-.6 1.3-1.3 1.3h-1.7M6.5 16.5H4.8c-.7 0-1.3-.6-1.3-1.3v-1.7"/><circle cx="10" cy="10" r="2.6"/>',
    files: '<path d="M5 3.5h7l3 3V16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1Z"/><path d="M11.8 3.8v3h3M6.7 10h6.6M6.7 13h4.5"/>',
    journal: '<path d="M10 3.2 17 16H3L10 3.2Z"/><path d="M10 7.5v4M10 14.1v.1"/>',
    anatomy: '<circle cx="10" cy="10" r="6.7"/><path d="M10 9v4M10 6.7v.1"/>',
    learn: '<path d="M4 4.5h5a2 2 0 0 1 2 2V16a1.6 1.6 0 0 0-1.6-1.6H4Z"/><path d="M16 4.5h-3.4a2 2 0 0 0-1.6.8V16a1.6 1.6 0 0 1 1.6-1.6H16Z"/>',
    settings: '<circle cx="10" cy="10" r="2.6"/><path d="M10 2.8v2M10 15.2v2M2.8 10h2M15.2 10h2M4.9 4.9l1.4 1.4M13.7 13.7l1.4 1.4M4.9 15.1l1.4-1.4M13.7 6.3l1.4-1.4"/>',
  };

  const SECTIONS: Array<{ id: View; label: string; kicker: string; key: string }> = [
    { id: "now", label: "Сейчас", kicker: "Что агент делает", key: "1" },
    { id: "talk", label: "Чат", kicker: "", key: "2" },
    { id: "plans", label: "Задачи", kicker: "Агенда, доска, субагенты", key: "3" },
    { id: "wakes", label: "Пробуждения", kicker: "Пробуждения по расписанию", key: "4" },
    { id: "frame", label: "Контекст", kicker: "Из чего собран кадр (тень)", key: "5" },
    { id: "files", label: "Файлы", kicker: "Память агента в файлах", key: "6" },
    { id: "journal", label: "Журнал", kicker: "Ошибки и пропуски", key: "7" },
    { id: "anatomy", label: "Система", kicker: "Как это устроено", key: "8" },
  ];
  // ⚠ Подвал полки — ОТДЕЛЬНЫЙ массив, а не хвост SECTIONS: по SECTIONS строятся кнопки
  // полки и раскладка Ctrl+1…8, и «Настройки» появились бы в полке дважды. Но и одинокой
  // константой кикера он быть перестал: разделов внизу теперь два, и каждый, кто не нашёл
  // себя в SECTIONS, получал ЧУЖОЕ имя в шапке и диктору — «Что поручить» звалось бы
  // «Настройками». Без подзаголовка строка кикера схлопывается
  // (`.head-kicker:empty { display: none }`), и заголовок подпрыгивает на каждый Ctrl+`,`.
  const FOOT: Array<{ id: View; label: string; kicker: string; key: string }> = [
    { id: "learn", label: "Что поручить", kicker: "С чего начать и как идёт ход", key: "9" },
    { id: "settings", label: "Настройки", kicker: "Как настроена программа", key: "," },
  ];

  // Подписи клавиш — по КЛАВИАТУРЕ, на которой открыто окно (⌘ на Mac): это
  // знает браузер, и ответа оболочки ждать не надо. Обработчик ниже слушает и
  // metaKey, и ctrlKey, так что подпись и жест совпадают на обеих.
  const clientMac = clientIsMac(navigator);

  function railButton(id: View, label: string, key: string): HTMLButtonElement {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "rail-item";
    b.dataset.view = id;
    b.innerHTML = `<svg viewBox="0 0 20 20" aria-hidden="true">${ICONS[id]}</svg><span>${esc(label)}</span><kbd>${esc(kbdLabel("Ctrl+" + key, clientMac))}</kbd>`;
    b.addEventListener("click", () => void show(id));
    return b;
  }

  for (const s of SECTIONS) railNav.append(railButton(s.id, s.label, s.key));
  for (const f of FOOT) railBottom.append(railButton(f.id, f.label, f.key));
  // Кого показывает окно — первой строкой полки, и только когда агентов в
  // установке больше одного (см. agents.ts).
  mountSwitch(q<HTMLElement>("#rail"));

  // Панели сворачиваются как в IDE и помнят состояние.
  const railBtn = q<HTMLButtonElement>("#toggle-rail");
  const panelBtn = q<HTMLButtonElement>("#toggle-panel");
  // Подсказки кнопок из index.html написаны с Ctrl+ — на Mac переписываем на ⌘.
  for (const b of [railBtn, panelBtn, roomAdd]) b.title = kbdLabel(b.title, clientMac);
  function readFlag(key: string): boolean {
    try {
      return localStorage.getItem(key) === "1";
    } catch {
      return false;
    }
  }
  // Движение — ТОЛЬКО когда рычаг дёрнул человек. Восстановление из памяти при запуске
  // и смена вкладки обязаны быть мгновенными: владелец просил панели плавные, а вкладки
  // быстрые, и это разные требования.
  function setCollapsed(which: "rail" | "panel", on: boolean, animate = false) {
    app.classList.toggle("no-anim", !animate);
    if (animate) {
      // Класс вернётся сам, когда ход закончится; таймер — страховка на случай, когда
      // transitionend не придёт (панель уже была в этом положении).
      const done = () => app.classList.add("no-anim");
      app.addEventListener("transitionend", function once(e: TransitionEvent) {
        if (e.propertyName !== "--rail-track" && e.propertyName !== "--panel-track") return;
        app.removeEventListener("transitionend", once);
        done();
      });
      window.setTimeout(done, 600);
    }
    app.classList.toggle(which + "-collapsed", on);
    try {
      localStorage.setItem("frame." + which, on ? "1" : "0");
    } catch {
      // без хранилища панели просто не запомнятся
    }
    (which === "rail" ? railBtn : panelBtn).setAttribute("aria-pressed", String(!on));
  }
  setCollapsed("rail", readFlag("frame.rail"));
  setCollapsed("panel", readFlag("frame.panel"));
  railBtn.addEventListener("click", () => setCollapsed("rail", !app.classList.contains("rail-collapsed"), true));
  panelBtn.addEventListener("click", () => setCollapsed("panel", !app.classList.contains("panel-collapsed"), true));

  document.addEventListener("keydown", (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    if (e.shiftKey) return;
    if (e.code === "KeyB") {
      e.preventDefault();
      railBtn.click();
    } else if (e.code === "KeyJ") {
      e.preventDefault();
      // Панель живёт только на «Чате». `click()` на скрытой кнопке срабатывает, и без
      // этой строки Ctrl+J на любой другой вкладке молча переворачивал состояние,
      // которого не видно, и запоминал его.
      if (S.view !== "talk") return;
      panelBtn.click();
    } else if (e.code === "KeyN") {
      e.preventDefault();
      void newRoom();
    } else if (e.code === "Comma") {
      e.preventDefault();
      void show("settings");
    } else if (e.code === "Digit9") {
      // Подвал полки: раздел «Что поручить». Ctrl+1…8 остаются за SECTIONS —
      // это мышечная память, её не двигают.
      e.preventDefault();
      void show("learn");
    } else if (/^Digit[1-8]$/.test(e.code)) {
      const s = SECTIONS[Number(e.code.slice(5)) - 1];
      if (s) {
        e.preventDefault();
        void show(s.id);
      }
    }
  });

  function syncRail() {
    for (const b of document.querySelectorAll<HTMLButtonElement>(".rail-item")) {
      b.setAttribute("aria-current", b.dataset.view === S.view ? "page" : "false");
    }
  }

  const views: Record<View, { render: (root: HTMLElement) => Promise<void> }> = {
    now,
    talk,
    plans,
    wakes,
    frame,
    files,
    journal,
    anatomy,
    learn,
    // Экран настроек — общий каркас плюс ИЗДАНИЕ. Карточки местного агента
    // приносит `agentEdition`: они читают и пишут helene.json рядом с окном и
    // спрашивают харнесс на этой же машине. У Praxis здесь стоит своё издание.
    settings: { render: (root: HTMLElement) => settings.render(root, opts.settingsEdition) },
  };

  // Диктору говорим отдельной строкой: aria-live на #view зачитывал бы весь чат
  // заново каждые полторы секунды.
  const liveRegion = q<HTMLElement>("#live");
  function announce(text: string) {
    liveRegion.textContent = text;
  }

  // Два конкурирующих show() (клик по полке во время незавершённого чтения)
  // писали в один #view; поколение отсекает опоздавшего.
  let showSeq = 0;

  /**
   * @param quiet — «обнови содержимое», а не «покажи другой раздел»: без
   *   промежуточного «читаю…» и с сохранением прокрутки.
   */
  // ⚠ 17.09. РАЗДЕЛ — ЖИВОЙ УЗЕЛ, А НЕ ПЕРЕРИСОВАННАЯ СТРОКА.
  //
  // Раньше `show()` синхронно выжигал #view словом «читаю…» ДО всякой сети, а `.empty`
  // схлопывает страницу в одно центрированное слово и разворачивает обратно. Это и был
  // «рывок»: на каждый щелчок по вкладке окно схлопывалось и распрямлялось, даже когда
  // содержимое уже было прочитано секунду назад.
  //
  // Теперь у каждого раздела свой узел: он отсоединяется и возвращается целым, вместе со
  // своей прокруткой. Картинка встаёт в том же кадре, что и щелчок; перечитывание идёт
  // фоном и подменяет содержимое молча.
  const pages = new Map<View, HTMLElement>();
  const scrolls = new Map<View, number>();

  function pageFor(id: View): HTMLElement {
    let page = pages.get(id);
    if (!page) {
      page = document.createElement("div");
      page.className = "page";
      page.dataset.page = id;
      pages.set(id, page);
    }
    return page;
  }

  /** Держит ли страница несохранённый ввод владельца. */
  function isDirty(page: HTMLElement): boolean {
    const active = document.activeElement;
    if (active instanceof HTMLElement && page.contains(active)
        && (active.isContentEditable || active.matches("input, textarea, select"))) {
      return true;
    }
    return [...page.querySelectorAll<HTMLTextAreaElement>("textarea")]
      .some((t) => t.value !== t.defaultValue);
  }

  function dropPage(id: View) {
    pages.delete(id);
    scrolls.delete(id);
  }

  /**
   * Где раздел открывается, если владелец в нём ещё не листал.
   *
   * ⚠ ЖИВОЙ СЛУЧАЙ 17.09. Переписка открывалась НАЧАЛОМ архива — сообщением
   * девятидневной давности, — и до сегодняшней реплики надо было крутить колесо
   * полминуты. `talk` в конце своего рендера честно просил конец, но просил у
   * своего узла, а прокручивается общий `#view`, и каркас следом ставил 0.
   * Лента живёт последней репликой: у неё дом — низ, у остальных — верх.
   */
  function homeScroll(id: View): number {
    const saved = scrolls.get(id);
    if (saved !== undefined) return saved;
    return id === "talk" ? view.scrollHeight : 0;
  }

  async function show(id: View, opts: { quiet?: boolean } = {}) {
    // Раздела с таким именем может не быть: сюда ведут и строки из состояния харнесса, и
    // разметка. Раньше это кончалось пустым экраном с «Не получилось» и `reading 'render'`
    // в подробностях — сообщением, по которому владельцу нечего понять. Лучше показать
    // «Сейчас» и назвать промах вслух.
    if (!views[id]) {
      toast(`Раздела «${id}» нет — открываю «Сейчас».`);
      id = "now";
    }
    const gen = ++showSeq;
    const from = S.view;
    // Прокрутку помним, только если в #view правда лежит узел ТОГО раздела: в него умеет
    // писать напрямую ветка отказа загрузки комнат, и чужая прокрутка уехала бы в память.
    if (from !== id && pages.get(from)?.parentNode === view) scrolls.set(from, view.scrollTop);
    S.view = id;
    syncRail();
    const section = SECTIONS.find((s) => s.id === id) ?? FOOT.find((s) => s.id === id);
    headKicker.textContent = section ? section.kicker : "";
    if (id !== "talk") {
      headTitle.textContent = section?.label ?? "";
      headTitle.classList.remove("hand");
    }
    const talking = id === "talk";
    composer.hidden = !talking;
    panelBox.hidden = !talking;
    app.classList.toggle("with-panel", talking);
    panelBtn.hidden = !talking;
    const page = pageFor(id);
    const blank = !page.firstChild;
    // Читающий конец ленты не должен уезжать от новой реплики. Меряем ДО подмены
    // содержимого: после неё высота уже другая, и «был ли внизу» не спросить.
    const wasAtEnd = view.scrollHeight - view.scrollTop - view.clientHeight < 80;
    // Смена вкладки — мгновенная: движение владелец просил у панелей, а не здесь.
    app.classList.add("no-anim");
    if (view.firstChild !== page) view.replaceChildren(page);
    if (blank) page.classList.add("page-in");
    view.scrollTop = opts.quiet ? view.scrollTop : homeScroll(id);
    // Начатую правку фоновое перечитывание не сносит: у «Файлов» это открытый редактор,
    // у «Настроек» — заполненная форма. Раньше их стирало молча, через полсекунды после
    // того, как владелец увидел свой текст на месте.
    if (!blank && isDirty(page)) {
      announce(section?.label ?? "");
      return;
    }
    try {
      await views[id].render(page);
      if (gen !== showSeq) return;
      if (!opts.quiet) view.scrollTop = homeScroll(id);
      else if (wasAtEnd) view.scrollTop = view.scrollHeight;
      announce(section?.label ?? "");
    } catch (e) {
      if (gen !== showSeq) return;
      page.innerHTML = failHTML(e);
      bindFail(page, () => void show(id));
      announce(humanError(e).text);
    } finally {
      page.classList.remove("page-in");
    }
  }

  // ---------------------------------------------------------------- комнаты

  let roomPicked = false;
  //: Ключ комнаты, чьё имя правят прямо сейчас. Пока он занят, список не
  //: перерисовывается: `replaceChildren` вынес бы поле правки вместе со строкой,
  //: а удалённый из DOM input НЕ шлёт blur — правка пропадала бы молча. Список
  //: перерисовывают события хода (`loadRooms` на каждый run), так что попасть под
  //: это можно было просто медленно печатая.
  let renaming = "";
  function selectRoom(room: Room) {
    roomPicked = true;
    S.room = room.key;
    S.roomName = room.name;
    renderRooms();
    // ⚠ Единственное место, где сохранённый узел соврал бы: комната сменилась, а в «Чате»
    // на кадр осталась бы чужая переписка. Узел и прокрутку выбрасываем.
    dropPage("talk");
    void show("talk");
  }

  function roomButton(room: Room): HTMLButtonElement {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "room" + (room.stub ? " stub" : "");
    b.dataset.key = room.key;
    b.setAttribute("aria-current", String(room.key === S.room));
    // Имя агента над его комнатой — его почерком; остальные — гротеском.
    const hand = room.key === WINDOW_ROOM;
    b.innerHTML =
      `<span class="dot ${room.live ? "live" : ""}"></span>` +
      `<span class="room-name${hand ? " hand" : ""}" title="${esc(room.name)}">${esc(room.name)}</span>` +
      (room.count && !hand ? `<span class="room-count">${room.count}</span>` : "");
    if (room.kind === "window" && room.key !== WINDOW_ROOM) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "icon-btn room-more";
      more.setAttribute("aria-label", "Действия с чатом");
      more.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="5" cy="10" r="1.3"/><circle cx="10" cy="10" r="1.3"/><circle cx="15" cy="10" r="1.3"/></svg>';
      more.addEventListener("click", (e) => {
        e.stopPropagation();
        openMenu(room, more.getBoundingClientRect());
      });
      b.append(more);
      b.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        openMenu(room, new DOMRect(e.clientX, e.clientY, 0, 0));
      });
    }
    b.addEventListener("click", () => selectRoom(room));
    return b;
  }

  function renderRooms() {
    if (renaming) return;
    const nodes: HTMLElement[] = [];
    let group = "";
    for (const room of S.rooms) {
      if (room.kind === "telegram" && group !== "telegram") {
        group = "telegram";
        const label = document.createElement("div");
        label.className = "rooms-group";
        label.textContent = "Telegram";
        nodes.push(label);
      }
      nodes.push(roomButton(room));
    }
    roomsBox.replaceChildren(...nodes);
    roomAdd.hidden = false;
  }

  async function loadRooms() {
    const { runs, chats } = await fetchRooms();
    S.runs = runs;
    S.rooms = buildRooms(runs, chats);
    // Чужой харнесс (Praxis): комната окна у неё пуста — чат открывается
    // на самой свежей комнате, пока владелец не выбрал сам.
    if (foreignHarness() && !roomPicked && S.room === WINDOW_ROOM) {
      const fresh = S.rooms.find((r) => r.kind === "telegram" && r.count > 0) ?? S.rooms.find((r) => r.kind === "telegram");
      if (fresh) {
        S.room = fresh.key;
        S.roomName = fresh.name;
      }
    }
    const current = S.rooms.find((r) => r.key === S.room);
    if (current) S.roomName = current.name;
    else if (S.room !== WINDOW_ROOM) {
      // Комнату убрали (другое окно, харнесс) — возвращаемся в основную.
      S.room = WINDOW_ROOM;
      S.roomName = S.agent;
    }
    renderRooms();
  }

  async function newRoom() {
    try {
      const room = await createRoom("Новый чат");
      S.rooms.splice(S.rooms.findIndex((r) => r.kind === "telegram") < 0 ? S.rooms.length : S.rooms.findIndex((r) => r.kind === "telegram"), 0, room);
      // Новая комната сразу первой среди чатов окна, после основной.
      const i = S.rooms.indexOf(room);
      S.rooms.splice(i, 1);
      S.rooms.splice(1, 0, room);
      selectRoom(room);
      if (room.stub) {
        toast("Этот код агента ещё не умеет несколько чатов: чат создан как заглушка, писать в него нельзя. Появится в 0.3.3.");
      }
      startRename(room);
    } catch (e) {
      toast("Чат не создался: " + humanError(e).text);
    }
  }
  roomAdd.addEventListener("click", () => void newRoom());

  /** Переименование на месте: поле вместо имени, Enter — сохранить, Esc — отмена. */
  function startRename(room: Room) {
    const b = roomsBox.querySelector<HTMLElement>(`.room[data-key="${CSS.escape(room.key)}"]`);
    const name = b?.querySelector<HTMLElement>(".room-name");
    if (!b || !name) return;
    renaming = room.key;
    const input = document.createElement("input");
    input.className = "field-input";
    input.value = room.name;
    input.style.height = "26px";
    input.style.flex = "1";
    input.style.minWidth = "0";
    input.style.padding = "0 8px";
    input.style.fontSize = "13px";
    name.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (save: boolean) => {
      if (done) return;
      done = true;
      const value = input.value.trim();
      if (save && value && value !== room.name) {
        try {
          await renameRoom(room, value);
          // Пока правили, список мог перечитаться: в S.rooms уже ДРУГОЙ объект с
          // тем же ключом, и без этой строки новое имя показалось бы только после
          // следующего чтения канала.
          const live = S.rooms.find((r) => r.key === room.key);
          if (live) live.name = room.name;
          if (S.room === room.key) S.roomName = room.name;
          // Заголовок и панель хода носят имя комнаты — перерисовать тихо.
          if (S.view === "talk" && S.room === room.key) void show("talk", { quiet: true });
        } catch (e) {
          toast(humanError(e).text);
        }
      }
      renaming = "";
      renderRooms();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        void finish(true);
      } else if (e.key === "Escape") {
        e.preventDefault();
        void finish(false);
      }
      e.stopPropagation();
    });
    input.addEventListener("blur", () => void finish(true));
    input.addEventListener("click", (e) => e.stopPropagation());
  }

  function openMenu(room: Room, at: DOMRect) {
    menu.replaceChildren();
    const item = (text: string, cls: string, fn: () => void) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = cls;
      b.textContent = text;
      b.addEventListener("click", () => {
        closeMenu();
        fn();
      });
      menu.append(b);
    };
    item("Переименовать", "", () => startRename(room));
    item("Убрать чат", "danger", () => void removeRoom(room));
    menu.hidden = false;
    const x = Math.min(at.left, innerWidth - menu.offsetWidth - 8);
    const y = Math.min(at.bottom + 4, innerHeight - menu.offsetHeight - 8);
    menu.style.left = x + "px";
    menu.style.top = y + "px";
    // ⚠ Закрывать меню НА ЛЮБОМ pointerdown нельзя, и это стоило самих пунктов.
    // Нажатие на «Переименовать» — тоже pointerdown: он всплывал до document,
    // меню пряталось (display:none), и к моменту отпускания кнопки под курсором
    // был уже другой элемент. Браузер шлёт click общему предку — то есть мимо
    // пункта, и его обработчик не звался НИКОГДА. Снаружи это выглядело как
    // «переименовать и удалить чат нельзя» (жалоба 15.09). Своё нажатие меню
    // пропускает; закрывает его пункт сам, отработав.
    setTimeout(() => document.addEventListener("pointerdown", closeMenuOnAway), 0);
    document.addEventListener("keydown", closeMenuOnEsc);
  }

  function closeMenu() {
    menu.hidden = true;
    document.removeEventListener("pointerdown", closeMenuOnAway);
    document.removeEventListener("keydown", closeMenuOnEsc);
  }
  function closeMenuOnAway(e: PointerEvent) {
    if (e.target instanceof Node && menu.contains(e.target)) return;
    closeMenu();
  }
  function closeMenuOnEsc(e: KeyboardEvent) {
    if (e.key === "Escape") closeMenu();
  }

  async function removeRoom(room: Room) {
    try {
      await deleteRoom(room);
      S.rooms = S.rooms.filter((r) => r.key !== room.key);
      if (S.room === room.key) {
        S.room = WINDOW_ROOM;
        S.roomName = S.agent;
        void show("talk");
      }
      renderRooms();
      toast(room.stub ? "Чат убран." : "Чат убран: переписка отложена в архив агента, не удалена.");
    } catch (e) {
      toast(humanError(e).text);
    }
  }

  // ---------------------------------------------------------------- состояние

  /**
   * Перезапуск честно: если агента держит служба Windows, оболочка своих детей не
   * поднимала, и перезапуск окна — нулевое действие.
   */
  async function restartHarness() {
    // 26.09: сначала — просьба движку через канал. Движок сам выходит между ходами
    // кодом «перезапусти меня», а поднимает его тот, кто держит, — окно или служба, —
    // с перечитанными настройками. Окно при этом остаётся: перезапуск окна под службой
    // был нулевым действием («какого чёрта он не перезапускается с ней»).
    try {
      const answer = await post<{ ok?: boolean; note?: string }>("/api/supervisor/restart", { target: "all" });
      if (answer?.ok) {
        toast("Движок перезапускается — окно остаётся. Секунд через десять агент снова на связи.");
        return;
      }
    } catch {
      // канал не ответил — движка нет или он не поднялся; ниже — как раньше
    }
    let svc = "";
    // Служба есть на обеих системах (SCM и демон launchd) — спрашиваем всегда.
    // Вне приложения (Пульт в браузере) ручки нет, и это не служба «не стоит»,
    // а «спросить не у кого»: пустая строка, и перезапуск идёт как обычно.
    try {
      svc = await shell<string>("service_state");
    } catch {
      // вне приложения (веб) — служба не при делах
    }
    if (svc === "running") {
      toast(`Агента держит ${isMacPlatform(S.platform) ? "служба" : "служба Windows"}, а движок на просьбу не ответил — служба поднимет его сама. Не поднялся за минуту: Настройки → Режим, сними и поставь службу заново.`);
      void show("settings");
      return;
    }
    try {
      await shell("restart_self");
    } catch (e) {
      toast("Не перезапустилось: " + humanError(e).text);
    }
  }

  /**
   * Чужой харнесс (Praxis): сердцебиения Hélène нет, и канал честно
   * отвечает «Не запущен». Для окна это не тревога, а другой способ судить о
   * жизни: свежий вызов модели и идущие прогоны.
   */
  function foreignState(s: AgentState): { level: string; phrase: string } {
    const running = S.runs.find((r) => r.status === "running" && runIsRecent(r));
    if (running) return { level: "live", phrase: "Ведёт ход" + (running.chat_title ? ` · ${running.chat_title}` : "") };
    const at = s.brain?.last_call_at;
    if (!at) return { level: "warn", phrase: "Вызовов модели ещё не было" };
    const ageMin = (Date.now() / 1000 - at) / 60;
    const age = fmtAge(at);
    const when = age === "только что" ? age : `${age} назад`;
    if (ageMin < 15) return { level: "ok", phrase: `На связи · модель отвечала ${when}` };
    return { level: "warn", phrase: `Модель молчит ${age}` };
  }

  function renderState(s: AgentState | null, connected: boolean) {
    paintPulse(connected);
    if (!connected) {
      statePill.dataset.level = "off";
      stateText.textContent = "Нет связи с кодом агента";
      stateAction.hidden = true;
      return;
    }
    if (!s) return;
    if (foreignHarness()) {
      const f = foreignState(s);
      statePill.dataset.level = f.level;
      stateText.textContent = f.phrase;
      statePill.title = "Дерево ведёт чужой код агента: снимка Hélène нет, состояние — по вызовам модели и запускам.";
      stateAction.hidden = true;
      alarmBox.hidden = true;
      return;
    }
    statePill.dataset.level = s.level;
    stateText.textContent = s.phrase;
    statePill.title = s.phrase;
    if (s.action) {
      stateAction.hidden = false;
      stateAction.textContent = s.action.label;
      stateAction.onclick = () => {
        if (s.action?.target === "settings") void show("settings");
        else if (s.action?.target === "restart") void restartHarness();
      };
    } else {
      stateAction.hidden = true;
    }
    // «Долгое молчание» без долга — сигнал разработчику, не тревога для человека.
    const alarms = (s.alarms || []).filter((a) => a.kind !== "long_silence");
    alarmBox.hidden = !alarms.length;
    alarmBox.innerHTML = alarms.map((a) => `<span>⚠ ${esc(a.text)}</span>`).join(" · ");
  }

  async function refreshState() {
    try {
      const s = await api<AgentState>("/api/state");
      const wasBusy = !!S.agentState?.runner?.busy;
      S.agentState = s;
      // Имя: снимок харнесса знает его лучше всех; без снимка (чужой харнесс)
      // имя даёт config.js страницы — иначе Праксис звалась бы «Агент».
      const named = s.anatomy === false && (cfg.agent || "").trim() ? (cfg.agent || "").trim() : s.agent;
      if (named) {
        s.agent = named;
        S.agent = named;
        // Комната окна — по имени агента: переименовали в настройках — сменилась
        // и она, в списке и в шапке, если открыта именно она.
        const win = S.rooms.find((r) => r.key === WINDOW_ROOM);
        if (win && win.name !== s.agent) {
          win.name = s.agent;
          if (S.room === WINDOW_ROOM) {
            S.roomName = s.agent;
            if (S.view === "talk") headTitle.textContent = s.agent;
          }
          renderRooms();
        }
      }
      // Связь берём настоящую: api() умеет уйти на HTTP-фолбэк при мёртвом сокете.
      renderState(s, S.connected);
      paintRailSign();
      const busy = foreignHarness()
        ? S.runs.some((r) => r.status === "running" && runIsRecent(r))
        : !!s.runner?.busy && !!s.runner?.alive;
      if (busy || wasBusy) {
        panel.tick(busy);
      }
      now.tick();
    } catch {
      // связь решает пилюля через onConnection
    }
  }

  // Последняя строка расхода и время, когда её подтвердили. При обрыве связи
  // цифры не выдаём за текущие.
  let pulseHTML = "";
  let pulseStamp = "";
  function paintPulse(connected: boolean) {
    if (!pulseHTML) {
      pulseBox.textContent = "";
      return;
    }
    pulseBox.classList.toggle("stale", !connected);
    pulseBox.innerHTML = connected ? pulseHTML : `<span class="muted">данные от ${esc(pulseStamp)}</span> · ${pulseHTML}`;
  }

  async function refreshPulse() {
    try {
      const p = await api("/api/pulse");
      const l = p.last || {};
      if (!l.ts) {
        pulseHTML = "";
        pulseBox.textContent = "";
        return;
      }
      const total = (l.in || 0) + (l.cached || 0);
      const share = total ? Math.round((100 * (l.cached || 0)) / total) : 0;
      pulseHTML =
        `${fmtTs(l.ts)} · <b>${esc(l.model || "")}</b> · кэш <span class="cachebar"><i style="width:${share}%"></i></span>${share}% · ` +
        `${fmtK(total)} → ${fmtK(l.out || 0)}${l.err ? ' · <span class="err-msg">ошибка</span>' : ""}`;
      pulseStamp = new Date().toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
      if (p.calls_day != null) {
        pulseBox.title = `За сутки: вызовов ${p.calls_day}` +
          (p.cache_day != null ? `, доля кэша ${p.cache_day}%` : "") +
          (p.cache_now != null ? `; сейчас ${p.cache_now}%` : "");
      }
      paintPulse(S.connected);
    } catch {
      paintPulse(false);
    }
  }

  // ---------------------------------------------------------------- композер

  // Черновик переживает падение канала и закрытие окна.
  const draftKey = (room: string) => "frame.draft." + room;
  function saveDraft(room: string, text: string) {
    try {
      if (text) localStorage.setItem(draftKey(room), text);
      else localStorage.removeItem(draftKey(room));
    } catch {
      // без хранилища черновик живёт до перезапуска
    }
  }
  function readDraft(room: string): string {
    try {
      return localStorage.getItem(draftKey(room)) || "";
    } catch {
      return "";
    }
  }
  function autoGrow() {
    say.style.height = "auto";
    say.style.height = Math.min(say.scrollHeight, 180) + "px";
  }

  let composerRoom = "";
  function syncComposer() {
    const room = S.rooms.find((r) => r.key === S.room);
    composerTarget.textContent = isWindowRoom(S.room) ? "" : `в «${S.roomName}»`;
    say.placeholder = room?.stub ? "Чат-заглушка: писать сюда пока нельзя" : isWindowRoom(S.room) ? "Написать агенту…" : `Написать в «${S.roomName}»…`;
    say.disabled = !!room?.stub;
    send.disabled = !!room?.stub || sending;
    // Черновик подставляем только при настоящей смене комнаты: событие
    // frame-room летит на каждой перерисовке чата.
    if (composerRoom !== S.room) {
      saveDraft(composerRoom, composerRoom ? say.value : "");
      composerRoom = S.room;
      say.value = readDraft(S.room);
      composerNote.textContent = "";
      autoGrow();
    }
  }

  say.addEventListener("input", () => {
    autoGrow();
    saveDraft(S.room, say.value);
  });
  // Первый замер мог пройти до стилей (dev-сервер подключает CSS асинхронно) —
  // поле раздувалось до потолка. Перемеряем, когда страница собралась.
  addEventListener("load", autoGrow);
  say.addEventListener("keydown", (e) => {
    // sending и isComposing: без них два быстрых Enter давали два хода агента по
    // одному тексту, а Enter подтверждения IME при кириллице отправлял недописанное.
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !sending) {
      e.preventDefault();
      void doSend();
    }
  });
  send.addEventListener("click", () => void doSend());

  let sending = false;
  let pendSeq = 0;

  /** Куда делась реплика владельца — словами, по настоящему состоянию агента. */
  function sendNote(chat: string, midturn: boolean, sleeping = false): string {
    // Ревью 26.09 (W3 S1): сон — не ход; записка ждёт его конца, а не «читается сейчас».
    if (sleeping) return "агент спит — прочтёт, когда проснётся";
    if (chat && !isWindowRoom(chat)) return midturn ? `ушло в «${S.roomName}»` : `ждёт хода в «${S.roomName}»`;
    if (midturn) return "агент читает сейчас";
    const st = S.agentState;
    // «Квитанции не было ни разу» — это не «агент выключен», а «этот агент окно не
    // читает»: так выглядит окно к серверу, где записка ложится в дерево и ждёт
    // читателя, которого там нет. Обещать «прочитает в следующий ход» здесь — врать.
    if (st && st.runner && st.runner.ever === false) return "лежит в дереве: этот агент окно не читает";
    if (st && st.runner && !st.runner.alive) return "ждёт запуска агента";
    return "ждёт следующего хода";
  }

  async function doSend() {
    if (sending) return;
    const typedText = say.value.trim();
    const files = attachments.slice();
    if (!typedText && !files.length) return;
    // Пустая реплика с картинкой — тоже реплика: в ленте и в памяти она названа словами.
    const voices = files.filter((f) => f.kind === "audio").length;
    const text = typedText || (files.length === 1
      ? (voices ? "[голосовое]" : "[картинка]")
      : voices === files.length ? `[голосовые: ${files.length}]` : voices ? `[вложения: ${files.length}]` : `[картинки: ${files.length}]`);
    const room = S.room;
    const current = S.rooms.find((r) => r.key === room);
    if (current?.stub) {
      toast("Этот чат — заглушка: код агента ещё не умеет несколько чатов. Пиши в основной.");
      return;
    }
    const chat = room === WINDOW_ROOM ? "" : room;
    // Канал принимает записку в telegram-комнату и без бота, а руннер потом молча
    // выбрасывает её. Состояние это знает заранее — спрашиваем его.
    if (chat && !isWindowRoom(chat) && S.agentState?.telegram && !S.agentState.telegram.enabled) {
      toast(`Telegram не подключён: везти сообщение в «${S.roomName}» некуда. Настройки → Telegram.`);
      return;
    }
    // Оптимистичное эхо: реплика ложится в ленту сразу.
    const pending: Pending = {
      id: ++pendSeq,
      room,
      text,
      at: new Date().toISOString(),
      state: "sending",
      note: "отправляется…",
    };
    S.pending.push(pending);
    sending = true;
    send.disabled = true;
    const typed = say.value;
    say.value = "";
    autoGrow();
    saveDraft(room, "");
    talk.paintPending();
    const slow = window.setTimeout(() => {
      if (pending.state === "sending") {
        pending.note = "агент не отвечает уже пять секунд…";
        talk.paintPending();
      }
    }, 5000);
    try {
      const payload: Record<string, unknown> = { text: typedText };
      if (chat) payload.chat = chat;
      if (files.length) payload.attachments = files.map((a) => ({ name: a.name, mime: a.mime, data: a.data }));
      const data = await post("/api/say", payload);
      if (files.length) clearFiles();
      pending.state = "queued";
      pending.note = sendNote(chat, !!data?.midturn, !!data?.sleeping);
      composerNote.textContent = pending.note;
      talk.paintPending();
      talk.afterSend();
    } catch (e) {
      // Текст не теряем: пузырь убираем, набранное возвращаем в поле.
      S.pending = S.pending.filter((p) => p.id !== pending.id);
      talk.paintPending();
      if (S.room === room) {
        say.value = typed;
        autoGrow();
        saveDraft(room, typed);
        say.focus();
      } else {
        saveDraft(room, typed);
      }
      toast("Не ушло: " + humanError(e).text);
    }
    clearTimeout(slow);
    sending = false;
    send.disabled = false;
  }

  // ---------------------------------------------------------------- события

  onConnection((ok) => {
    S.connected = ok;
    if (ok) {
      void refreshState();
      void loadRooms().then(() => {
        if (S.view === "talk") void show("talk");
      });
      void refreshPulse();
    } else {
      renderState(S.agentState, false);
    }
  });

  let skipsTimer = 0;
  let skipsPaintedAt = 0;
  function paintJournal() {
    skipsPaintedAt = Date.now();
    if (S.view === "journal") void show("journal", { quiet: true });
  }
  onEvent((ev) => {
    if (ev.t === "health") void refreshState();
    if (ev.t === "llm") {
      void refreshPulse();
      panel.onLlm();
      now.onEvent("llm");
    }
    if (ev.t === "run") {
      if (ev.run_id) S.evCache.delete(String(ev.run_id));
      void loadRooms().then(() => now.onEvent("run"));
      talk.onRunEvent(String(ev.run_id ?? ""));
      void refreshState();
    }
    // Дебаунс под штормом откладываний: журнал — экран ровно для этого случая.
    if (ev.t === "skips" && S.view === "journal") {
      clearTimeout(skipsTimer);
      if (Date.now() - skipsPaintedAt > 3000) paintJournal();
      else skipsTimer = window.setTimeout(paintJournal, 1500);
    }
  });

  // ---------------------------------------------------------------- старт

  S.agent = (cfg.agent || "").trim() || "Агент";
  // Подпись внизу полки и заголовок вкладки — имя продукта хостинга: у издания к
  // серверу это «Praxis» (config.js), у Hélène — Hélène (слово владельца 07.09).
  const product = (cfg.product || "").trim() || PRODUCT_NAME;
  railSign.textContent = product;
  document.title = product;
  // Версию говорит оболочка (`app_info`). Её нет ровно там, где окно открыто
  // браузером — Praxis на сервере, — и подпись оставалась одним именем продукта.
  // Тогда версию берём у канала: он называет свой пакет desk в /api/state.
  let shellVersion = "";
  function paintRailSign(): void {
    const v = shellVersion || S.agentState?.desk?.version || "";
    railSign.textContent = v ? `${product} ${v}` : product;
  }
  // Система агента: в оболочке окно живёт на той же машине, что и агент, поэтому
  // до ответа `app_info` берём слово клиента — иначе первый рендер «Системы» и
  // «Что поручить» проходил бы с открытым затвором, с Windows-словами на Mac, и
  // не перерисовывался. В браузере (Пульт) хост — сервер, там слово клиента
  // ничего не значит: остаётся "" , и не прячется ничего.
  if (inTauri && clientMac) S.platform = "macos";
  // Тот же ответ несёт систему агента (`platform`): по ней экраны прячут то,
  // чего на ней нет. Один вызов на окно — host.ts кэширует. Старая оболочка
  // поля не шлёт — тогда остаётся слово клиента. Если затвор от ответа
  // изменился, открытый раздел перерисовывается.
  void hostInfo().then((i) => {
    if (!i) return;
    shellVersion = i.version || "";
    const was = S.platform;
    S.platform = platformOf(i) || S.platform;
    paintRailSign();
    if (isMacPlatform(was) !== isMacPlatform(S.platform)) void show(S.view, { quiet: true });
  });
  syncComposer();
  addEventListener("frame-room", syncComposer);
  addEventListener("frame-go", (e) => void show((e as CustomEvent<View>).detail));
  addEventListener("frame-restart", () => void restartHarness());
  // Пока адрес сервера не вписан, связываться не с кем: канал не открываем и
  // состояние не спрашиваем — окно показывает карточку подключения (askForServer).
  if (!cfg.needs_remote) {
    connect();
    void refreshState();
  }
  addEventListener("frame-open-room", (e) => {
    const d = (e as CustomEvent<{ key: string; name: string }>).detail;
    const room = S.rooms.find((r) => r.key === d.key) ?? { key: d.key, name: d.name, kind: isWindowRoom(d.key) ? "window" : "telegram", live: false, count: 0, mtime: 0 } as Room;
    selectRoom(room);
  });
  // Ссылки с карточки хода (ui-kit/steps): открыть место в чате; надиктовать агенту просьбу в композер.
  function roomFor(key: string): Room {
    const k = key === LEGACY_WINDOW_KEY || key === WINDOW_ROOM ? WINDOW_ROOM : key;
    return S.rooms.find((r) => r.key === k) ?? ({ key: k, name: isWindowRoom(k) ? S.agent : k, kind: isWindowRoom(k) ? "window" : "telegram", live: false, count: 0, mtime: 0 } as Room);
  }
  addEventListener("steps-open", (e) => {
    const d = (e as CustomEvent<{ room: string; at: string }>).detail;
    selectRoom(roomFor(d.room));
    // Лента рисуется асинхронно: прыгаем, когда сообщение уже в DOM.
    let tries = 0;
    const hop = () => { if (talk.jumpTo(d.at) || ++tries > 20) return; window.setTimeout(hop, 150); };
    window.setTimeout(hop, 150);
  });
  addEventListener("steps-compose", (e) => {
    const d = (e as CustomEvent<{ room: string; text: string }>).detail;
    selectRoom(roomFor(d.room));
    window.setTimeout(() => { say.value = d.text; say.dispatchEvent(new Event("input")); say.focus(); }, 250);
  });
  /**
   * Рамка задачи из раздела «Что поручить» — в поле ввода.
   *
   * ⚠ Комната — ВСЕГДА окно этого агента, а не `S.room`: последним выбранным
   * мог остаться чат живого человека, и рамка с пропусками («Познакомься:
   * <имя> — <кто это мне>») приземлилась бы прямо ему, вместе с курсором.
   *
   * ⚠ Черновик не затирается: человек мог уже что-то писать. Рамка встаёт
   * следом за написанным, а не вместо него.
   */
  addEventListener("frame-template", (e) => {
    const text = String((e as CustomEvent<string>).detail || "");
    if (!text) return;
    selectRoom(roomFor(WINDOW_ROOM));
    window.setTimeout(() => {
      const had = say.value.replace(/\s+$/, "");
      say.value = had ? had + "\n\n" + text : text;
      say.dispatchEvent(new Event("input"));
      say.focus();
      say.setSelectionRange(say.value.length, say.value.length);
    }, 250);
  });
  /**
   * Первый запуск Praxis к своему серверу: адрес и ключ спрашиваются в окне.
   *
   * У варианта Praxis установщика нет по замыслу — поставка распаковывается. До
   * 0.5.0 окно с пустым адресом открывалось «как есть» и билось об ошибки связи,
   * а человек должен был догадаться отредактировать helene.json рядом с exe.
   * Пишем тем же путём, что «Настройки»: config_save → restart_self.
   */

  // Первый запуск издания: занял экран — окно не подключается и комнат не
  // грузит, потому что подключаться пока не к чему.
  if (opts.firstRun?.(view)) return;
  connect();
  void refreshState();
  // 26.09: окно, открытое мастером сразу после установки, заставало канал ещё не
  // поднятым и оставалось на этом экране до клика владельца — а обещало «продолжит
  // попытки само». Держим слово: пробуем снова, пока на экране этот же экран.
  const openFirst = (): void => {
    loadRooms()
      .then(() => show("now"))
      .catch(() => {
        view.innerHTML = `<div class="empty" data-first-wait><b>${esc(S.agent)} сейчас не на связи</b>Окно продолжит попытки само. Можно оставить его открытым.</div>`;
        renderState(null, false);
        window.setTimeout(() => {
          if (view.querySelector("[data-first-wait]")) openFirst();
        }, 4000);
      });
  };
  openFirst();
  void refreshPulse();
  setInterval(() => void refreshState(), 8000);
  setInterval(() => void refreshPulse(), 20000);
}

