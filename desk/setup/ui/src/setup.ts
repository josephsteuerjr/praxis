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
}

export interface Receipt {
  dir: string;
  exe: string;
  // running | stopped | absent (нет прав) | missing (нет службы в поставке) |
  // skipped | failed: <причина>
  service: string;
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
};

/** Что уже установлено на машине: заполняется на старте ответом `defaults`.
 *  Установщик не читал существующую установку вовсе, и обновление выглядело
 *  как первое учреждение продукта. `platform` — оттуда же (см. `Defaults`). */
export const machine: { installed: Installed | null; platform: string; inPlace: boolean } = { installed: null, platform: "", inPlace: false };

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
    return { dir: "C:\\Users\\…\\AppData\\Local\\Programs\\Hélène", payload: null, version: "превью", installed: null, platform: "windows" };
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
    // На Mac ярлыков и записи в «Приложениях» нет — как и шагов про них (install.rs).
    const labels = isMac()
      ? ["Копирую файлы программы", "Записываю настройки и конституцию", "Готово"]
      : [
        "Копирую файлы программы",
        "Записываю настройки и конституцию",
        "Создаю ярлыки",
        "Регистрирую удаление",
        "Готово",
      ];
    for (const [i, label] of labels.entries()) {
      onProgress({ step: i + 1, total: labels.length, label });
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

/** Снятие из визарда; в превью — заглушка. */
export async function runUninstall(purge: boolean): Promise<string> {
  if (!inTauri) return "Превью: снятие доступно в установщике";
  return invoke("uninstall_run", { purge });
}

export async function openFrame(exe: string): Promise<void> {
  if (!inTauri) return;
  await invoke("open_frame", { exe });
}
