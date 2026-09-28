// Состояние установки и мост к оболочке.
//
// Всё, что человек вводит по сценам, живёт здесь одним объектом; установка в
// конце отдаёт его оболочке целиком. В браузерном превью оболочки нет —
// команды отвечают заглушками, чтобы сцены можно было смотреть и править.
import soulCanon from "../../../resources/SOUL.md?raw";

export type Provider = "api" | "anthropic" | "chatgpt" | "local";
export type Effort = "" | "low" | "medium" | "high" | "xhigh";

/** Ограда рук — ключ `agent_mode` в helene.json. ДВА значения, больше нет.
 *
 * ⚠ «Служба» сюда не входит и никогда не входила по смыслу: она опция ПОВЕРХ
 * любой ограды (поле `service` ниже). Пока их держали одним списком из трёх,
 * у владельца со службой И песочницей выходило `agent_mode: "service"`, а оно
 * означало «ограды нет» — ограда снималась молча. Разбор — в шапке
 * localharness/modes.py.
 *
 * ⚠ Именно `agent_mode`, а НЕ `mode`: `mode` в helene.json занят под
 * местожительство харнесса (`local` | `remote`), и его читают оболочка
 * (shell/src/main.rs) и служба (svc/src/main.rs). Записать туда «sandbox» —
 * это окно, которое не поднимает ни трубу, ни руннер, и служба, которая
 * отказывается стартовать. */
export type AgentMode = "sandbox" | "interactive";

export interface Setup {
  agent: string;
  owner: string;
  constitution: string;
  accepted: boolean;
  provider: Provider;
  chatgpt_model: string;
  reasoning_effort: Effort;
  api: { base_url: string; model: string; key: string };
  anthropic: { base_url: string; model: string; key: string };
  local: { base_url: string; model: string };
  telegram: { bot_token: string; owner_id: string };
  agent_mode: AgentMode;
  /** Ставить ли службу Windows. САМОСТОЯТЕЛЬНОЕ решение владельца, поверх
   *  любой ограды: служба даёт защиту папки установки, жизнь без окна и
   *  брокера прав, а ограду не трогает вовсе. Требует прав администратора —
   *  один раз, при установке. */
  service: boolean;
  /** Галочка внутри опции службы: доступ агента к правам системы. В
   *  helene.json уезжает в `service.session0` — туда, где её УЖЕ читает служба
   *  (svc/src/main.rs::load_plan). Без службы не действует: исполнять некому. */
  session0: boolean;
  /** Вторая галочка опции службы: ставит ли служба правило брандмауэра для
   *  кнопки «Телефон». Ключ `service.firewall`.
   *
   *  На экране установщика её НЕТ намеренно: владелец просил одну галочку
   *  (нулевая сессия), а у этой оба положения не равноценны — выключенная
   *  только добавляет окно UAC на кнопку «Телефон». Тонкая настройка живёт в
   *  Настройках; сюда значение приезжает умолчанием из modes.FIREWALL_DEFAULT,
   *  чтобы в установщике не завелась вторая правда о нём. */
  firewall: boolean;
  /** Управление компьютером — опция ПОВЕРХ любого режима: тело руки `computer`
   *  (окна, экран, клавиатура и мышь) харнесс поднимает снаружи ограды. В
   *  helene.json уезжает в `computer.enabled`; четыре права (`computer.scopes`)
   *  установщик не спрашивает — все четыре, сузить можно в Настройках. */
  computer: boolean;
  dir: string;
  /** 1.2: «для меня» (`user`) или «для всех» (`machine`, Program Files, один запрос прав). */
  scope?: "" | "user" | "machine";
  /** 1.2: продолжить с найденной памятью из другой папки — её data/ и helene.json
   *  КОПИРУЮТСЯ в новую установку, источник не трогается. */
  carry_from?: string;
}

/** Находка на машине (1.2): установка, остаток памяти после снятия, копия владельца. */
export interface Found {
  kind: "installed" | "unconfigured" | "leftover" | "backup";
  dir: string;
  agent: string;
  owner: string;
  version: string;
  scope: "" | "user" | "machine";
  program: boolean;
  complete: boolean;
  decisions: boolean;
  data_mb: number;
  last: string;
  agents: string[];
}

export interface Installed {
  dir: string;
  version: string;
  agent: string;
}

export interface Defaults {
  dir: string;
  /** Мастер на месте (папка установки NSIS): папка фиксирована, «Удалить» — uninstall.exe. */
  in_place?: boolean;
  payload: string | null; // папка поставки рядом с установщиком, если она есть
  version: string;
  installed: Installed | null; // что уже стоит на машине: это обновление, а не первая установка
  /** `windows` | `macos` | `linux` (std::env::consts::OS оболочки). По этому
   *  слову визард прячет то, чего на системе нет: службу, тело, брандмауэр.
   *  Старая оболочка поля не шлёт — тогда пусто, и не прячется ничего. */
  platform?: string;
  arch?: string;
  /** 1.2: что лежит на машине. */
  found?: Found[];
  elevated?: boolean;
  user_dir?: string;
  machine_dir?: string;
  /** Поставка — хвост установщика (один файл setup.exe). */
  tail?: boolean;
  /** Рядом uninstall.exe установщика NSIS 1.1.x. */
  nsis?: boolean;
}

export interface Receipt {
  dir: string;
  exe: string;
  // running | stopped | absent (нет прав) | missing (нет службы в поставке) |
  // skipped | failed: <причина>
  service: string;
  /** 1.2: `user` | `machine`. */
  scope?: string;
  /** 1.2: снимок памяти перед обновлением (путь к zip). */
  backup?: string;
  // Предупреждение службы: она работает как СИСТЕМА и запускает код из папки,
  // куда пишет обычный пользователь. Приходит от helene-svc через файл рядом с
  // конфигом; на экране расписки его показывают отдельной строкой.
  warning?: string;
  steps: Array<{ label: string; ok: boolean; note?: string }>;
}

export interface Progress {
  step: number;
  total: number;
  label: string;
  /** 1.2: `check` | `backup` | `lay` | `rehearse` | `stop` | `swap` | `configure` |
   *  `register` | `service` | `done`; у снятия — `service` | `stop` | `fence` | `files` | `done`. */
  phase?: string;
  /** Доля внутри фазы 0..1 (раскладка). */
  frac?: number;
  detail?: string;
  /** Можно ли сейчас отменить — до подмены. */
  cancellable?: boolean;
}

export const setup: Setup = {
  agent: "",
  owner: "",
  constitution: "",
  accepted: false,
  provider: "api",
  chatgpt_model: "gpt-5.6-sol",
  reasoning_effort: "",
  api: { base_url: "https://api.openai.com/v1", model: "gpt-5.4", key: "" },
  anthropic: { base_url: "https://api.z.ai/api/anthropic", model: "glm-5.3", key: "" },
  local: { base_url: "http://127.0.0.1:11434/v1", model: "" },
  telegram: { bot_token: "", owner_id: "" },
  // Умолчание — песочница: ровно то, чем продукт живёт сегодня (в шаблоне
  // поставки `sandbox.enabled: true`, и ограда в fence.install включена по
  // умолчанию). Экран режима всё равно спрашивает явно, но невыбранный экран
  // не должен молча расширять права агента.
  agent_mode: "sandbox",
  // Служба — не умолчание: её ставят осознанно, под администратором.
  service: false,
  session0: false,
  // То же умолчание, что в modes.FIREWALL_DEFAULT. Значение приезжает сюда из
  // SERVICE_OPTION на сцене режима — здесь только первое, до её показа.
  firewall: true,
  // Умолчание — выключено (modes.COMPUTER_DEFAULT): включают осознанно,
  // прочитав оговорку. Значение приезжает из COMPUTER_OPTION на сцене режима.
  computer: false,
  dir: "",
  scope: "",
  carry_from: "",
};

/** Что уже установлено на машине: заполняется на старте ответом `defaults`.
 *  Установщик не читал существующую установку вовсе, и обновление выглядело
 *  как первое учреждение продукта. `platform` — оттуда же (см. `Defaults`). */
export const machine: {
  installed: Installed | null;
  platform: string;
  inPlace: boolean;
  /** 1.2: находки, установленная (с режимом), права, папки по умолчанию. */
  found: Found[];
  installedFound: Found | null;
  elevated: boolean;
  userDir: string;
  machineDir: string;
  nsis: boolean;
} = { installed: null, platform: "", inPlace: false, found: [], installedFound: null, elevated: false, userDir: "", machineDir: "", nsis: false };

/** Визард открыт на macOS. Службы Windows, тела тула `computer` и правила
 *  брандмауэра там нет по построению — их опции, строки сводки и слова про
 *  UAC не рисуются вовсе (решение владельца: не писать «на macOS этого нет»,
 *  а просто не показывать). Сцены строятся до ответа `defaults`, поэтому
 *  спрашивают это в `beforeEnter`, а не в конструкторе. */
export function isMac(): boolean {
  return machine.platform === "macos";
}

/** Каноническая конституция с подставленными именами (тот же текст, что читает boot.py).
 *  Замена — функцией, а не строкой: в строке замены `$&`, `$\``, `$'` и `$$` —
 *  управляющие, и имя агента «$&» оставляло в принятой конституции живой {{agent}}. */
export function constitutionFor(agent: string, owner: string): string {
  const a = agent.trim() || "Агент";
  const o = owner.trim() || "владелец";
  return soulCanon.replace(/\{\{agent\}\}/g, () => a).replace(/\{\{owner\}\}/g, () => o);
}

const inTauri = "__TAURI_INTERNALS__" in window;

async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

export async function loadDefaults(): Promise<Defaults> {
  if (!inTauri) {
    // Превью: `?platform=macos` показывает сцены глазами Mac — без службы и тела.
    if (new URLSearchParams(location.search).get("platform") === "macos") {
      return { dir: "/Users/…/Applications/Helene", payload: null, version: "превью", installed: null, platform: "macos", arch: "aarch64" };
    }
    const q = new URLSearchParams(location.search);
    const found: Found[] = q.has("found")
      ? [
          { kind: "leftover", dir: "C:\\Users\\…\\AppData\\Local\\Programs\\Helene", agent: "Мира", owner: "Егор", version: "1.1.1", scope: "user", program: false, complete: true, decisions: true, data_mb: 71, last: "2026-09-27", agents: [] },
          { kind: "backup", dir: "C:\\Users\\…\\AppData\\Local\\Helene-backup-20260926", agent: "Мира", owner: "Егор", version: "1.0.3", scope: "", program: false, complete: true, decisions: true, data_mb: 72, last: "2026-09-26", agents: ["Джарвис"] },
        ]
      : [];
    // `?installed` — глазами сцену «уже установлена» (обновление поверх, 28.09).
    const installed = q.has("installed") ? { dir: "C:\\Program Files\\Helene", version: "1.2.3", agent: "Джарвис" } : null;
    return {
      dir: "C:\\Users\\…\\AppData\\Local\\Programs\\Helene",
      payload: null,
      version: q.has("installed") ? "1.2.5" : "превью",
      installed,
      platform: "windows",
      found,
      user_dir: "C:\\Users\\…\\AppData\\Local\\Programs\\Helene",
      machine_dir: "C:\\Program Files\\Helene",
      tail: true,
    };
  }
  return invoke<Defaults>("defaults");
}

/** «Удалить» на месте: uninstall.exe установщика NSIS; мастер закрывается сам. */
export async function uninstallLaunch(): Promise<void> {
  if (!inTauri) return;
  await invoke("uninstall_launch");
}

/** Решения уже стоящей установки — для «Обновить» поверх (те же, что у `--update`).
 *  null — решений нет (имена, конституция): мастер идёт обычным маршрутом. */
export async function installedSetup(dir: string): Promise<Setup | null> {
  if (!inTauri) return null;
  return (await invoke<Setup | null>("installed_setup", { dir })) ?? null;
}

/** Установка: оболочка копирует поставку, пишет конфиг и конституцию, ставит ярлыки. */
export async function runInstall(onProgress: (p: Progress) => void): Promise<Receipt> {
  if (!inTauri) {
    previewCancel = false;
    const plan: Array<[string, string, boolean]> = [
      ["check", "Проверяю установщик", true],
      ["lay", "Раскладываю новую версию рядом", true],
      ["swap", "Меняю версии местами", false],
      ["configure", "Записываю настройки и конституцию", false],
      ["register", "Ярлыки и запись в «Приложениях»", false],
      ["done", "Готово", false],
    ];
    const labels = plan.map((p) => p[1]);
    for (const [i, [phase, label, cancellable]] of plan.entries()) {
      if (phase === "lay") {
        for (let f = 0; f <= 1.0001; f += 0.05) {
          if (previewCancel) throw "Отменено — прежняя версия на месте, ничего не изменилось";
          onProgress({ step: i + 1, total: plan.length, label, phase, frac: f, detail: `${Math.round(f * 15342)} файлов · ${Math.round(f * 649)} МБ`, cancellable });
          await new Promise((r) => setTimeout(r, 160));
        }
        continue;
      }
      if (previewCancel && cancellable) throw "Отменено — прежняя версия на месте, ничего не изменилось";
      onProgress({ step: i + 1, total: plan.length, label, phase, cancellable });
      await new Promise((r) => setTimeout(r, 650));
    }
    return {
      dir: setup.dir,
      exe: isMac() ? setup.dir + "/Helene.app" : setup.dir + "\\helene.exe",
      service: setup.service ? "running" : "skipped",
      steps: labels.map((label) => ({ label, ok: true })),
    };
  }
  const { listen } = await import("@tauri-apps/api/event");
  const stop = await listen<Progress>("install-progress", (e) => onProgress(e.payload));
  try {
    return await invoke<Receipt>("install", { setup });
  } finally {
    stop();
  }
}

/** Может ли эта учётная запись дать права администратора.
 *
 * Нужно ровно для одного: карточка «Служба» должна быть недоступна с
 * названной ПРИЧИНОЙ, а не просто серой. Проверка нарочно осторожная —
 * `certain: false` значит «Windows не ответила», и тогда карточку не
 * запрещаем: настоящей заставой всё равно остаётся UAC при установке. */
export interface AdminRights {
  /** Учётка состоит в группе администраторов: UAC она подтвердить сможет. */
  can: boolean;
  /** Windows ответила внятно. false — запрещать ничего нельзя. */
  certain: boolean;
  /** Установщик уже запущен с правами администратора. */
  elevated: boolean;
}

export async function adminRights(): Promise<AdminRights> {
  if (!inTauri) {
    // Превью: `?noadmin=1` показывает сцену глазами ограниченной учётки.
    if (new URLSearchParams(location.search).has("noadmin")) {
      return { can: false, certain: true, elevated: false };
    }
    return { can: true, certain: false, elevated: false };
  }
  return invoke<AdminRights>("admin_rights");
}

/** Живая проверка адреса и ключа через оболочку; в превью — заглушка. */
export async function probeModel(baseUrl: string, key: string, framework = "openai"): Promise<{ ok: boolean; note: string; models?: string[] }> {
  if (!inTauri) return { ok: true, note: "Превью: проверка доступна в установщике", models: ["пример-1", "пример-2"] };
  return invoke("probe_model", { baseUrl, key, framework });
}

/** Список моделей самого реле ChatGPT: реле поднимается на миг из поставки. */
export async function relayModels(): Promise<string[]> {
  if (!inTauri) return ["gpt-5.6-sol", "gpt-5.5"];
  return invoke("relay_models");
}

export async function relayLogin(): Promise<string> {
  if (!inTauri) return "Превью: вход доступен в установщике";
  return invoke("relay_login");
}

export async function relayStatus(): Promise<"authorized" | "pending" | "no-auth"> {
  if (!inTauri) return "no-auth";
  return invoke("relay_status");
}

/** Ссылка входа, которую напечатал помощник реле (1.2.3): null — входа нет или ссылки ещё нет. */
export async function relayLoginUrl(): Promise<string | null> {
  if (!inTauri) return null;
  return invoke("relay_login_url");
}

/** Открыть страницу входа рукой самого мастера. */
export async function openLoginPage(): Promise<void> {
  if (!inTauri) return;
  return invoke("open_login_page");
}

/** Служба прежнего поколения продукта, найденная в SCM. */
export interface LegacyService {
  name: string;
  state: string; // Running / Stopped / …
  start: string; // Auto / Manual / Disabled
  account: string; // LocalSystem — права системы
  path: string; // откуда стартует
  ours: boolean; // служба текущей установки: её снимает сам установщик
}

/** Опрос SCM по именам, под которыми жил этот продукт (Vera, Frame, Praxis,
 *  Helene). Ничего не снимает — только смотрит. */
export async function legacyServices(home: string): Promise<LegacyService[]> {
  if (!inTauri) {
    // Превью сцены: `?legacy=1` показывает её на выдуманной находке.
    if (!new URLSearchParams(location.search).has("legacy")) return [];
    return [
      {
        name: "Vera",
        state: "Running",
        start: "Auto",
        account: "LocalSystem",
        path: "C:\\Users\\…\\AppData\\Local\\Programs\\Vera\\vera-svc.exe run --config …",
        ours: false,
      },
    ];
  }
  return invoke("legacy_services", { home });
}

/** Снять названную службу — только по нажатию кнопки владельцем. */
export async function removeService(name: string): Promise<string> {
  if (!inTauri) return `Превью: служба «${name}» снимается в установщике`;
  return invoke("remove_service", { name });
}

/** Снятие из визарда; `dir` — какая установка (1.2: снимать можно и из нового
 *  установщика, не только мастером в папке программы). Ход — событиями. */
export async function runUninstall(purge: boolean, dir: string, onProgress?: (p: Progress) => void): Promise<string> {
  if (!inTauri) {
    const phases = ["service", "stop", "fence", "files", "done"];
    const labels = ["Снимаю службу", "Останавливаю программу", "Снимаю ограду песочницы", "Убираю файлы программы", "Готово"];
    for (const [i, label] of labels.entries()) {
      onProgress?.({ step: i + 1, total: labels.length, label, phase: phases[i] });
      await new Promise((r) => setTimeout(r, 450));
    }
    return "Превью: снятие доступно в установщике";
  }
  const { listen } = await import("@tauri-apps/api/event");
  const stop = await listen<Progress>("uninstall-progress", (e) => onProgress?.(e.payload));
  try {
    return await invoke<string>("uninstall_run", { purge, dir: dir || null });
  } finally {
    stop();
  }
}

let previewCancel = false;

/** «Отмена» установки: до подмены — откат, прежняя версия цела. */
export async function cancelInstall(): Promise<void> {
  if (!inTauri) {
    previewCancel = true;
    return;
  }
  await invoke("cancel_install");
}

export async function openFrame(exe: string): Promise<void> {
  if (!inTauri) return;
  await invoke("open_frame", { exe });
}
