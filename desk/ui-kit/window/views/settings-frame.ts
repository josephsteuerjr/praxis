// Каркас экрана «Настройки», общий обоим приложениям.
//
// ⚠⚠ ПРАВИЛО ЭТОГО ЭКРАНА: БЛОКИ КОНФИГА СЛИВАЮТСЯ, А НЕ ПЕРЕСОБИРАЮТСЯ.
// Владелец правит helene.json ещё и руками, а харнесс кладёт туда своё — всё,
// чего этот экран не знает, обязано пережить «Сохранить». Дважды пересборка
// уже стоила живых данных: сперва `relay.instructions` (23 КБ чужого промпта
// возвращались агенту), потом `sandbox.mounts` — вся работа по монтированию
// обнулялась одним кликом. Поэтому объектные блоки пишутся только через
// `keepBlock`, и на этом стоит стенд app/test/config-blocks.test.mjs.
//
// ⚠ ПОЧЕМУ ЭТОТ ФАЙЛ ОБЩИЙ, А КАРТОЧКИ — НЕТ. До 10.09 экран был один на два
// приложения, и разница между ними держалась ветками `if (remote)` — сорок
// четыре штуки в одном файле. Появились они 09.09 как заплатка (издание к серверу
// ругалось на чужой харнесс пустыми полями и красными строками), и заплаткой же и
// оставались: окно Элен таскало в себе логику издания к серверу, а издание —
// карточки агента, которого рядом нет.
//
// Теперь разница объявлена, а не выведена: каркас грузит конфиг и рисует то,
// что есть у обоих (имена, телефон, перенос, автозапуск, о программе,
// «Сохранить»), а издание приносит свои карточки и свою часть записи в конфиг.
// Веток `remote` здесь нет ни одной — если понадобилась, значит разрез прошёл
// не там.
import { api, cfg, inTauri, post, shell } from "../api";
import { keepBlock } from "../config";
import { bindFail, el, esc, failHTML, humanError, toast } from "../lib";
import { button, card, field, toggle } from "../../dom";
import { PRODUCT_NAME, S } from "../state";
import { hostInfo, type HostInfo } from "../host";
import { lookCard } from "../look";
import { deskTrialHTML, type UpdateState } from "./update-card";
import { isMacPlatform, platformOf } from "../../platform";
import { relayAuthCard } from "../relay-auth-card";
import { collectConnection } from "../server-connection";
import { paperDialog } from "../../paper-dialog";

/** Блоки конфига, которые движок читает только на старте, — по имени для расписки. */
export const RESTART_BLOCKS: Array<[string, string]> = [
  ["telegram", "Telegram"],
  ["computer", "тело"],
  ["voice", "голос"],
  ["agents", "агенты"],
  ["sandbox", "ограда"],
];

/** Какие из RESTART_BLOCKS изменились между сохранённым и новым конфигом (стабильный JSON). */
export function blocksNeedingRestart(before: unknown, after: unknown): string[] {
  const stable = (v: unknown): string => JSON.stringify(v ?? null, Object.keys((v && typeof v === "object") ? (v as object) : {}).sort());
  const deep = (v: unknown): string => {
    if (!v || typeof v !== "object") return JSON.stringify(v ?? null);
    if (Array.isArray(v)) return "[" + v.map(deep).join(",") + "]";
    const o = v as Record<string, unknown>;
    return "{" + Object.keys(o).sort().map((k) => JSON.stringify(k) + ":" + deep(o[k])).join(",") + "}";
  };
  void stable;
  const b = (before && typeof before === "object") ? (before as Record<string, unknown>) : {};
  const a = (after && typeof after === "object") ? (after as Record<string, unknown>) : {};
  const out: string[] = [];
  for (const [key, title] of RESTART_BLOCKS) {
    if (deep(b[key]) !== deep(a[key])) out.push(title);
  }
  return out;
}

export interface Config {
  agent?: { name?: string };
  // `external` — внешний адрес канала (06.10): сервер с белым IP, под которым
  // этот компьютер виден из любой сети. QR по нему работает и вне этой Wi-Fi.
  phone?: { enabled?: boolean; mode?: string; external?: string };
  update?: { url?: string };
  // 1.2: копии памяти по расписанию (common/backup.rs): раз в `every_days` дней (0 —
  // выключено), хранить `keep` снимков, папка `dir` (пусто — backups рядом с программой).
  backup?: { every_days?: number; keep?: number; dir?: string };
  owner?: { name?: string; room?: string };
  model?: { framework?: string; base_url?: string; model?: string; key?: string; keys?: Record<string, string>; max_tokens?: number; reasoning_effort?: string; fallback_model?: string; fallback_framework?: string; fallback_base_url?: string; fallback_key?: string; vision_model?: string; pinned?: boolean };
  // `instructions` экран не показывает, но обязан сохранить: этой ручкой
  // оболочка гасит 23 КБ чужого системного промпта Codex CLI перед конституцией
  // (shell/src/main.rs, RELAY_INSTRUCTIONS). Раньше блок relay пересобирался
  // заново, и ручка исчезала при первом же «Сохранить».
  relay?: { enabled?: boolean; port?: number; instructions?: string; [k: string]: unknown };
  images?: { enabled?: boolean; model?: string; quality?: string; size?: string; background?: string; [k: string]: unknown };
  telegram?: { bot_token?: string; owner_id?: number | string; mode?: string; api_id?: string | number; api_hash?: string; phone?: string; status_message?: boolean; allow_from?: "owner" | "listed" | "any"; allowed_ids?: Array<string | number>; proxy?: { enabled?: boolean; url?: string; key?: string }; history_initial_limit?: number };
  // ⚠ `mounts` и `mounts_denied` карточка монтирования ТОЖЕ пишет, а
  // `[k: string]` держит и то, чего экран не знает: блок обязан СЛИВАТЬСЯ при
  // сохранении, иначе список смонтированных папок исчезает при первом же клике.
  sandbox?: { enabled?: boolean; network?: boolean; mounts?: unknown; mounts_denied?: unknown; [k: string]: unknown };
  // Режим агента — ОГРАДА РУК и только она: sandbox | interactive. Значение
  // "service" здесь больше не пишется никем: служба — не режим, а опция поверх
  // любой ограды. Ключ именно `agent_mode` — `mode` в этом файле занят под
  // местожительство харнесса (local | remote), и режим, записанный туда,
  // выключает и окно, и службу.
  agent_mode?: string | { name?: string; [k: string]: unknown };
  service?: { session0?: boolean; firewall?: boolean; [k: string]: unknown };
  computer?: { enabled?: boolean; scopes?: unknown; port?: number; [k: string]: unknown };
  // Голос: локальный whisper на процессоре (localharness/voice.py). Модель в
  // поставку не входит — её выбирает и качает владелец, поэтому здесь только
  // выбор и ручки, а «скачана или нет» спрашивается у канала (`/api/voice`).
  voice?: { enabled?: boolean; model?: string; language?: string; threads?: number;
            keep_loaded?: boolean; compute_type?: string; beam_size?: number; [k: string]: unknown };
  installed?: { service?: boolean; [k: string]: unknown };
  // Местожительство харнесса: `local` — дети окна; `remote` — окно ходит в
  // трубу на сервере по `base` и `key`. Это НЕ режим агента — тот в `agent_mode`.
  mode?: string;
  base?: string;
  key?: string;
  [k: string]: unknown;
}

export interface Loaded {
  config: Config;
  path: string;
  tree: string;
  exe_dir: string;
  /** Отпечаток файла на момент чтения (КОНТРАКТ-B→A §2); старая оболочка его не шлёт. */
  mtime_ns?: string | number;
}

/**
 * Черновик ПОСЛЕ того, как каркас завёл обязательные блоки.
 *
 * Это не косметика для типов, а объявленный контракт: каркас делает
 * `draft.agent = draft.agent || {}` (и так для четырёх блоков) до того, как
 * позвать издание, и издание вправе на это опереться. Без объявления каждая
 * из полутора сотен строк карточек писала бы `draft.model!.…` — то есть
 * глушила бы проверку там, где гарантия настоящая.
 */
export type Draft = Config & Required<Pick<Config, "agent" | "owner" | "model" | "telegram">>;

/** Что каркас даёт изданию: черновик, сохранённый конфиг и ответ оболочки. */
export interface EditionContext {
  /** Черновик: издание правит его на месте, как и каркас. */
  draft: Draft;
  /** Конфиг, как он лежит на диске сейчас. */
  saved: Config;
  loaded: Loaded;
  /** Ответ `app_info` оболочки; null — оболочки нет или она не ответила. */
  host: HostInfo | null;
  /** Система агента по контракту (`windows` | `macos` | `linux`); "" — неизвестно.
   *  По ней издание прячет карточки того, чего на системе нет. */
  platform: string;
  /**
   * Признать запись в helene.json, сделанную САМОЙ карточкой этого экрана
   * (немедленная запись верхней ступени, 05.10): пара `base`/`fresh`, где
   * base — отпечаток, по которому карточка читала файл. Рамка принимает
   * fresh как свой только при совпадении base со своим отпечатком открытия —
   * так своя запись не рождает ложный конфликт (06.10), а чужие правки файла
   * по-прежнему легализовать нельзя. Заполняется рамкой ПОСЛЕ построения
   * издания: до этого звонить некому.
   */
  freshness: { accept: ((base: string | null, fresh: string | null) => void) | null };
}

/**
 * Группа настроек — вкладка над карточками.
 *
 * ⚠ Порог группировки решает ИЗДАНИЕ, а не догадка по узлам. Считать группы
 * самим было бы соблазнительно (заголовки карточек все на виду), но заголовок
 * приходит из трубы и может прийти другим, а у издания к серверу карточек
 * этого компьютера нет вовсе: он получил бы четыре вкладки, чужие карточки в
 * «Мозге и связи» и ПУСТУЮ вкладку «Права на этом ПК».
 */
export interface SettingsGroup {
  id: string;
  label: string;
}

/**
 * Имена групп одной строкой на всех: каркас метит свои карточки, издание —
 * свои, и разойтись опечаткой им негде.
 */
export const GROUP = {
  agent: "agent",
  brain: "brain",
  rights: "rights",
  app: "app",
  look: "look",
} as const;

/** Пометить карточку группой. Непомеченная не теряется — см. `mountSettings`. */
export function inGroup(el: HTMLElement, group: string): HTMLElement {
  el.dataset.group = group;
  return el;
}

/** Что издание даёт каркасу. */
export interface Edition {
  /** Карточки издания. Встают между «Именами» и «Телефоном». */
  cards: HTMLElement[];
  /**
   * Группы и их порядок. Пусто — карточки идут одним списком, как раньше:
   * это и есть ответ издания к серверу, у которого группировать нечего.
   */
  groups?: SettingsGroup[];
  /**
   * Дописать в конфиг то, что знает издание.
   *
   * Возвращает ПУСТУЮ строку, если всё хорошо, и текст отказа, если сохранять
   * нельзя (например, токен Telegram без числового id владельца — бот включится
   * и будет молчать на всё). Каркас показывает этот текст и не пишет файл:
   * бросать исключение здесь значило бы уронить экран на ожидаемой развилке.
   */
  collect(out: Config): string;
  /** Подпись под карточкой «Имена»: у изданий она разная. */
  namesHint: string;
  /**
   * Адрес сервера для карточки телефона, если окно ходит к харнессу на нём.
   * Пусто — телефон подключается к этой машине.
   */
  phoneBase: string;
  /** Хвост расписки «Сохранено»: что ещё осталось сделать. Может быть пуст. */
  note(): string;
  /**
   * Нарисовать QR ссылки на телефон.
   *
   * ⚠ Приезжает от издания, а не берётся здесь: `ui-kit` — плоский общий слой
   * без своего package.json (его делят окно, телефон, мини-апп и установщик), и
   * заводить ему зависимость ради двух вызовов значило бы добавить всем
   * четверым шаг установки. У изданий `qrcode` и так в зависимостях.
   */
  qrSvg(text: string): Promise<string>;
}

export type EditionFactory = (ctx: EditionContext) => Promise<Edition>;

/**
 * Адрес выпусков по умолчанию — тот же, что сборка кладёт в `helene.json`
 * (installer/build_dist.py: HELENE_JSON и PRAXIS_JSON). Нужен, потому что
 * конфиг мог быть собран руками и без этого поля: оболочка на пустом адресе
 * честно отвечает «адрес обновлений не задан», и кнопка «Проверить обновления»
 * мертва (живой Praxis, 09.09). Поле в настройках по-прежнему главнее.
 */
export const UPDATE_URL_DEFAULT =
  "https://api.github.com/repos/josephsteuerjr/praxis/releases/latest";

export async function render(container: HTMLElement, edition: EditionFactory): Promise<void> {
  delete container.dataset.settingsDirty; // Explicit reload discards the old draft.
  if (!inTauri) {
    const center = el("div", "center");
    // Тумблер телефона в вебе был пустышкой: черновик выбрасывался в мусор,
    // кнопки «Сохранить» в этой ветке нет вовсе — человек щёлкал, и ничего не
    // происходило, и никто не говорил, что не происходит.
    center.append(el("div", "card muted", `Настройки доступны в приложении ${PRODUCT_NAME} на том компьютере, где живёт агент: здесь окно смотрит на удалённый код агента. Тема — как в системе.`));
    // «Вид» — удобство этого окна, а не настройка агента: доступен и здесь.
    center.append(lookCard());
    mountSettings(container, center);
    return;
  }
  let loaded: Loaded;
  try {
    loaded = await shell<Loaded>("config_load");
  } catch (e) {
    container.innerHTML = failHTML(e);
    bindFail(container, () => void render(container, edition));
    return;
  }
  // Срез «как лежит файл» — let: рамка тянет его вперёд за записями самой
  // карточки (freshness.accept ниже); черновик и издание остаются при срезе
  // открытия, а базой расписки перезапуска становится живой файл.
  let c = loaded.config;
  // Обязательные блоки заводим ЗДЕСЬ и объявляем это типом `Draft`: издание
  // опирается на них полутора сотнями строк карточек, и «наверное, есть» в
  // каждой из них было бы глушением проверки на настоящей гарантии.
  const draft = JSON.parse(JSON.stringify(c)) as Draft;
  draft.agent = draft.agent || {};
  draft.owner = draft.owner || {};
  draft.model = draft.model || {};
  draft.telegram = draft.telegram || {};
  const center = el("div", "center");

  // Первый запуск: без ключа модели движок не поднимается вовсе (это контракт
  // харнесса, не моя догадка), а человек видит «нет связи с агентом» и не знает,
  // что делать. Экран обязан сказать это вслух до того, как человек начнёт тыкать.
  if (cfg.needs_local_setup) {
    center.append(el("div", "notice",
      "Первый запуск. Чтобы агент поднялся, заполни карточку «Модель» в разделе «Мозг и связь»: " +
      "выбери пресет, вставь ключ и сохрани. Без ключа модели движок не запускается, " +
      "и окно останется без связи с агентом. Имена и остальные разделы можно заполнить потом."));
  }

  // Система агента — от оболочки, один раз (host.ts): на macOS автозапуск
  // зовётся иначе, службы и брандмауэра нет, и издание прячет свои карточки
  // по тому же слову.
  // Старая оболочка `platform` не шлёт — тогда слово, которое окно уже знает
  // (S.platform: клиент в оболочке = хост).
  const host = await hostInfo();
  const platform = platformOf(host) || S.platform;
  const mac = isMacPlatform(platform);
  const linux = platform === "linux";
  const posix = mac || linux;

  // Издание приносит свои карточки и свою часть записи в конфиг. Всё, что
  // ему нужно спросить у трубы (режим, снимок устройства), оно спрашивает
  // само: каркасу это знать незачем, а изданию к серверу — и подавно.
  // Свежесть — коробка на момент построения: рамка заполнит accept ниже,
  // когда заведёт seenMtime (карточки зовут её позже, по факту своей записи).
  const freshness: { accept: ((base: string | null, fresh: string | null) => void) | null } = { accept: null };
  const built = await edition({ draft, saved: c, loaded, host, platform, freshness });


  // --- имена
  const names = el("div", "form-grid two");
  names.append(
    field("Имя агента", String(draft.agent.name || ""), (v) => (draft.agent!.name = v)),
    field("Твоё имя", String(draft.owner.name || ""), (v) => (draft.owner!.name = v)),
  );
  // Настройки пишут только helene.json. Конституцию (data/soul/SOUL.md) не
  // переписывает никто, кроме установщика, — а в ней старые имена остаются
  // навсегда, и агент в своём K-слое читает именно их. Обещать обратное нельзя.
  center.append(inGroup(card("Имена", names, built.namesHint), GROUP.agent));

  for (const box of built.cards) center.append(box);

  center.append(inGroup(phoneCard(draft, !!c.phone?.enabled, String(c.phone?.mode || ""), built.phoneBase, built.qrSvg), GROUP.brain));

  // --- перенос: экспорт агента одним архивом и окно к харнессу на сервере
  center.append(inGroup(transferCard(draft, posix), GROUP.app));
  if (c.mode === "remote" && c.base) {
    center.append(inGroup(relayAuthCard(String(c.base)), GROUP.app));
  }

  // --- копии памяти (1.2): расписание, «сейчас», последние снимки
  center.append(inGroup(backupCard(draft), GROUP.app));

  // --- автозапуск
  const auto = el("div");
  // На macOS это LaunchAgent при входе в систему — «Windows» в подписи был бы чужим словом.
  const autoToggle = toggle(posix ? "Запускать при входе в систему" : "Запускать при входе в Windows", false, async (v) => {
    try {
      await shell("autostart_set", { on: v });
      toast(v ? "Автозапуск включён" : "Автозапуск выключен");
    } catch (e) {
      toast(humanError(e).text);
    }
  });
  shell<boolean>("autostart_get").then((v) => autoToggle.setAttribute("aria-checked", String(v))).catch(() => {});
  auto.append(autoToggle);
  center.append(inGroup(card("Автозапуск", auto), GROUP.app));

  // Тема — только как в системе (слово владельца 07.09): переключателя нет. Палитры дня
  // и ночи, фактура, текст и физика ленты — в «Виде» (28.09), применяются сразу.
  center.append(inGroup(lookCard(), GROUP.look));


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
    .catch(() => {
      // Оболочки нет — окно открыто браузером (Praxis на сервере). Версию
      // тогда называет канал: пакет desk, которым он поднят.
      const d = S.agentState?.desk;
      ver.textContent = d?.version
        ? `канал: desk ${d.version} (${d.digest})`
        : "версия видна в окне программы";
    });
  const updOut = el("span", "receipt");
  // Кнопка создаётся один раз и переключается. Раньше её добавляли внутрь
  // обработчика: три нажатия «Проверить обновления» — три кнопки «Скачать» в
  // ряд, и она оставалась висеть даже рядом с «Это последняя версия».
  let updUrl = "";
  let updSha = "";
  // 25.09 (K): «обновить, даже если моё расширение не пройдёт» — явное слово
  // владельца. Без него установщик остановится до подмены папок и назовёт причину;
  // отчёт репетиции — в карточке «Расширения».
  let forceExt = false;
  const forceToggle = toggle("Обновлять, даже если мои расширения не пройдут проверку", false, (v) => { forceExt = v; });
  // Скачать и поставить — оболочка (КОНТРАКТ A→B §5: update_download →
  // update_install). Несовпадение суммы оболочка отвергает сама (throw, файл
  // удалён); `sha_ok: null` — сверять было не с чем. Установщик гасит
  // программу — «установщик запущен» говорим ДО вызова. Старая оболочка без
  // этих команд — кнопка честно открывает ссылку на выпуск.
  const dlBtn = button(linux ? "Открыть выпуск Linux" : "Скачать и установить", "primary", async () => {
    if (!updUrl) return;
    if (linux) {
      await shell("open_path", { path: updUrl }).catch((e) => toast(humanError(e).text));
      updOut.textContent = "Установи новый .deb/.rpm через пакетный менеджер. Данные останутся в твоём доме.";
      return;
    }
    dlBtn.disabled = true;
    updOut.className = "receipt";
    updOut.textContent = "Скачиваю в «Загрузки»…";
    try {
      const got = await shell<{ path: string; bytes?: number; sha256?: string; sha_ok: boolean | null }>("update_download", { url: updUrl, sha256: updSha });
      const checked = got.sha_ok === true ? "отпечаток сошёлся" : got.sha_ok === null ? "отпечатка в выпуске нет, сверить было не с чем" : "отпечаток проверен";
      updOut.textContent = `Скачано (${checked}). Установщик запущен — программа закроется сама и откроется новой.`;
      // 25.09 (K): установщик сначала репетирует расширения владельца под новой
      // версией и без явного слова не подменяет папки, если хоть одно не грузится.
      // ⚠ Имена аргументов команд Tauri — camelCase (как `mtimeNs` у config_save):
      // `force_extensions` молча превращался бы в None (ревью 25.09, A4/A7 F1).
      await shell("update_install", { path: got.path, forceExtensions: forceExt });
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (/not found|неизвестн|unknown|command/i.test(text)) {
        updOut.textContent = mac
          ? "Эта версия оболочки ещё не умеет ставить обновление сама — открыл страницу выпуска, скачай архив для macOS и запусти из него Helene Setup."
          : "Эта версия оболочки ещё не умеет ставить обновление сама — открыл страницу выпуска, скачай и запусти helene-setup.exe.";
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
  forceToggle.hidden = true;
  aboutRow.append(
    ver,
    button("Проверить обновления", "quiet", async () => {
      updOut.className = "receipt";
      updOut.textContent = "спрашиваю…";
      try {
        const r = await shell<{ current: string; latest: string; newer: boolean; url: string; notes: string; sha256?: string }>("update_check", {
          url: String(draft.update?.url || "").trim() || UPDATE_URL_DEFAULT,
        });
        if (r.newer) {
          updOut.className = "receipt ok";
          updOut.textContent = `Есть версия ${r.latest}. ${r.notes || ""}`.trim();
          updUrl = r.url || "";
          updSha = r.sha256 || "";
          dlBtn.hidden = !updUrl;
          forceToggle.hidden = linux || !updUrl;
        } else {
          updOut.textContent = `Это последняя версия (${r.current}).`;
          updUrl = "";
          updSha = "";
          dlBtn.hidden = true;
          forceToggle.hidden = true;
        }
      } catch (e) {
        updOut.className = "receipt err";
        updOut.textContent = humanError(e).text;
        updUrl = "";
        dlBtn.hidden = true;
        forceToggle.hidden = true;
      }
    }),
    dlBtn,
    forceToggle,
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
  // Выпуск менял интерфейс, прежняя папка отложена рядом (КОНТРАКТ A→B §5).
  const prev = String((c.installed && (c.installed as Record<string, unknown>).static_prev) || "").trim();
  if (prev) {
    about.append(el("p", "field-hint", `Интерфейс обновлён этим выпуском; твоя прежняя версия статики лежит рядом: ${prev}. Ключ исчезнет при следующей установке, если папки нет.`));
  }
  // Испытание новой версии после обновления (1.2.5): его ведёт установщик, агент проверяет
  // себя делом; владелец может сказать своё поверх — «Всё хорошо» / «Вернуть прежнюю».
  const trialBox = el("div");
  const drawTrial = async () => {
    try {
      trialBox.innerHTML = deskTrialHTML(await api<UpdateState>("/api/update"));
    } catch {
      trialBox.innerHTML = "";
    }
  };
  void drawTrial();
  about.append(
    trialBox,
    aboutRow,
    field("Адрес обновлений", String(draft.update?.url || ""), (v) => (draft.update!.url = v), {
      mono: true,
      placeholder: "https://api.github.com/repos/<владелец>/helene/releases/latest",
      hint: "Адрес выпусков на GitHub (…/releases/latest) или свой JSON с полями version, url и notes. Программа только сообщает о новой версии и даёт ссылку, сама ничего не подменяет.",
    }),
    logsRow,
  );
  center.append(inGroup(card("О программе", about), GROUP.app));

  // --- сохранить
  const save = el("div", "actions");
  const saveOut = el("span", "receipt");
  save.append(
    button("Сохранить", "primary", async () => {
      const out: Config = JSON.parse(JSON.stringify(draft));
      // Отказ издания — ожидаемая развилка, а не поломка: показываем словами
      // и НЕ пишем файл. Бросать здесь исключение значило бы уронить экран
      // там, где человек просто не дозаполнил поле.
      const refused = built.collect(out);
      if (refused) {
        saveOut.className = "receipt err";
        saveOut.textContent = refused;
        return;
      }
      out.phone = keepBlock(out.phone, { enabled: !!draft.phone?.enabled, mode: draft.phone?.mode || "automatic" });
      const connectionError = collectConnection(out, draft);
      if (connectionError) {
        saveOut.className = "receipt err";
        saveOut.textContent = connectionError;
        return;
      }
      out.setup_complete = true;
      try {
        const relayNote = await writeConfig(out);
        saveOut.className = "receipt ok";
        // 25.09 (C.1): мозг (модель, ключ, адрес, запасной) движок перечитывает сам
        // в течение нескольких секунд, реле оболочка и служба поднимают заново по
        // новым настройкам — ни перезапуск программы, ни снятие службы для этого
        // больше не нужны. Что ещё требует перезапуска (ограда, песочница,
        // Telegram-аккаунт) — говорит хвост расписки от издания (`built.note()`).
        const modeNote = built.note();
        const relayWords = relayNote ? ` ${relayNote}.` : "";
        // Ревью 25.09 (A7 F2): движок на тике перечитывает мозг и пару галочек агента —
        // Telegram, тело, голос, агентов и ограду он читает один раз на старте. Если эти
        // блоки изменились, расписка обязана сказать «перезапуском» и показать кнопку.
        const restartBlocks = blocksNeedingRestart(c, out);
        const restartNote = restartBlocks.length
          ? ` ${restartBlocks.join(", ")} — применится перезапуском движка (кнопка ниже).`
          : "";
        saveOut.textContent =
          "Сохранено. Модель и ключ движок применит сам через несколько секунд, реле — сразу." +
          relayWords + restartNote + modeNote;
        if (cfg.needs_local_setup) saveOut.textContent = "Настройки сохранены. Перезапусти окно кнопкой ниже, чтобы впервые запустить агента.";
        // Пустой ключ при первом запуске — не «настройки сохранены», а полдела:
        // движок поднимется, но агент без мозга молчит. Обещать «впервые запустить
        // агента» здесь было бы ложью; кнопка перезапуска в этой тропе не нужна.
        if (cfg.needs_local_setup && !String(out.model?.key || "").trim()) {
          saveOut.className = "receipt err";
          saveOut.textContent =
            "Сохранено, но ключ модели пуст — агент не сможет думать и отвечать. " +
            "Вставь ключ в карточке «Модель», сохрани снова и перезапусти окно.";
          restartBtn.hidden = true;
        } else {
          restartBtn.hidden = !(modeNote || restartNote || cfg.needs_local_setup);
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
  // Свои записи карточек (немедленная запись верхней ступени) — не чужие, но
  // и не «подтверждение черновика»: fresh принимается ТОЛЬКО при совпадении
  // base с отпечатком открытия (ревью 06.10, P1). Без сверки файл мог править
  // Блокнот между открытием экрана и согласием — принятие свежего отпечатка
  // молча легализовало бы затирание его правки ближайшим «Сохранить». base не
  // совпал (чужая правка или «Сохранить» успело первым) — отпечаток НЕ
  // трогаем: конфликт покажется честно, перечитывание тут было бы той же
  // легализацией. Пустые base/fresh — старая оболочка без отпечатков: там
  // stale-механизма нет вовсе, принимать нечего.
  freshness.accept = (base, fresh) => {
    if (base != null && fresh != null && base === seenMtime) {
      seenMtime = fresh;
      // Срез файла тянем за своей записью (аудит 06.10). Без этого «Сохранить»
      // сравнивал черновик со срезом ОТКРЫТИЯ экрана, и немедленная запись
      // ступени самой карточкой навсегда выглядела «изменением чужих
      // настроек»: хвост «применится перезапуском» при нулевой чистой смене.
      // У «Перезаписать своим» база — свежий config_load; здесь та же база,
      // но принять её можно только по совпавшему отпечатку: не сошёлся mtime —
      // файл менял не этот экран, срез не трогаем (чужую правку не легализуем).
      void shell<Loaded>("config_load").then((r) => {
        if (r && typeof r === "object" && r.config && typeof r.config === "object"
          && r.mtime_ns != null && String(r.mtime_ns) === seenMtime) c = r.config;
      }).catch(() => {
        // Не перечиталось — срез остаётся срезом открытия; худшее, что даёт
        // эта миллисекундная гонка, — один лишний хвост у расписки.
      });
    }
  };
  class StaleConfig extends Error {}
  const writeConfig = async (out: Config, force = false): Promise<string> => {
    const revision = container.dataset.settingsRevision;
    const args: Record<string, unknown> = { config: JSON.stringify(out) };
    if (seenMtime && !force) args.mtimeNs = seenMtime;
    try {
      const r = await shell<{ ok?: boolean; code?: string; error?: string; mtime_ns?: string | number; relay?: string } | null>("config_save", args);
      // КОНТРАКТ A→B §1: `{ok: false, code: "stale", mtime_ns, error}` — файл
      // менял кто-то ещё, черновик не записан.
      if (r && typeof r === "object" && r.ok === false && r.code === "stale") throw new StaleConfig(r.error || "stale");
      if (r && typeof r === "object" && r.ok === false) throw new Error(r.error || "Настройки не сохранились");
      if (r && typeof r === "object" && r.mtime_ns != null) seenMtime = String(r.mtime_ns);
      if (revision === container.dataset.settingsRevision) delete container.dataset.settingsDirty;
      // Фикс-волна 06.10 (P3-2): срез `c` тянем за СОБСТВЕННОЙ записью тем же
      // гребнем, что freshness.accept. Без этого «Сохранить» сравнивал черновик
      // со срезом открытия, а легаси-нормализация collect оставляла вечный
      // хвост «ключей, которых нет в черновике» — перезапуск предлагался при
      // нулевой чистой смене. base для «Сохранить» — не отпечаток открытия, а
      // только что возвращённый mtime записи: срез тянет ровно та запись.
      await shell<Loaded>("config_load").then((r2) => {
        if (r2 && typeof r2 === "object" && r2.config && typeof r2.config === "object"
          && r2.mtime_ns != null && seenMtime != null && String(r2.mtime_ns) === seenMtime) {
          c = r2.config;
        }
      }).catch(() => {
        // Не перечиталось — срез остаётся прежним; худшее, что даёт эта
        // миллисекундная гонка, — один лишний хвост у расписки.
      });
      // 25.09: оболочка применила настройки реле сразу и сказала, что сделала.
      return r && typeof r === "object" && typeof r.relay === "string" ? r.relay : "";
    } catch (e) {
      if (e instanceof StaleConfig) throw e;
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (/^stale:/.test(text)) throw new StaleConfig(text);
      throw e;
    }
  };
  const conflictBox = el("div");
  conflictBox.hidden = true;
  conflictBox.innerHTML = `<div class="notice err" style="margin-top:12px"><span class="dot failed"></span>
    <span>Пока настройки были открыты, helene.json изменил кто-то ещё — установщик, код агента или ты ${mac ? "в редакторе" : "в Блокноте"}. Если сохранить как есть, его правка пропадёт.</span></div>`;
  const conflictRow = el("div", "actions");
  conflictRow.style.marginTop = "10px";
  conflictRow.append(
    button("Перечитать (мои правки потеряются)", "primary", () => void render(container, edition)),
    button("Перезаписать своим", "quiet", async () => {
      conflictBox.hidden = true;
      try {
        // Что лежит в файле ПРЯМО СЕЙЧАС: перезапись затирает именно это, и
        // предложение перезапуска обязано зависеть от разницы с черновиком.
        // До 06.10 кнопка говорила «перезапусти программу» ВСЕГДА — даже
        // когда владелец перезаписывал файл самим собой, не изменив ничего.
        let before: unknown = c;
        try {
          const fresh = await shell<Loaded>("config_load");
          if (fresh && typeof fresh === "object" && fresh.config && typeof fresh.config === "object") {
            before = fresh.config;
            if (fresh.mtime_ns != null) seenMtime = String(fresh.mtime_ns);
          }
        } catch {
          // Не перечитался — причину скажет сама запись ниже; сравнивать
          // остаётся с тем, что было на открытии экрана.
        }
        const out: Config = JSON.parse(JSON.stringify(draft));
        // Тот же путь записи, что у «Сохранить»: отказ издания — словами и
        // без записи файла, дальние ключи (mode/base/key) — по тем же правилам.
        const refused = built.collect(out);
        if (refused) {
          saveOut.className = "receipt err";
          saveOut.textContent = refused;
          // Кнопка перезапуска от прошлой расписки не должна висеть рядом с
          // отказом: файл не записан, перезапускать нечего (ревью 06.10).
          restartBtn.hidden = true;
          return;
        }
        out.phone = keepBlock(out.phone, { enabled: !!draft.phone?.enabled, mode: draft.phone?.mode || "automatic" });
        const connectionError = collectConnection(out, draft);
        if (connectionError) {
          saveOut.className = "receipt err";
          saveOut.textContent = connectionError;
          restartBtn.hidden = true;
          return;
        }
        out.setup_complete = true;
        const relayNote = await writeConfig(out, true);
        saveOut.className = "receipt ok";
        const modeNote = built.note();
        const restartBlocks = blocksNeedingRestart(before, out);
        const restartNote = restartBlocks.length
          ? ` ${restartBlocks.join(", ")} — применится перезапуском движка (кнопка ниже).`
          : "";
        saveOut.textContent =
          "Перезаписано. Модель и ключ движок применит сам через несколько секунд, реле — сразу." +
          (relayNote ? ` ${relayNote}.` : "") + restartNote + modeNote;
        // Первый запуск — те же слова, что у «Сохранить»: кнопка ниже зовёт
        // restart_self и поднимает агента впервые (симметрия, ревью 06.10).
        if (cfg.needs_local_setup) saveOut.textContent = "Настройки перезаписаны. Перезапусти окно кнопкой ниже, чтобы впервые запустить агента.";
        restartBtn.hidden = !(modeNote || restartNote || cfg.needs_local_setup);
      } catch (e) {
        saveOut.className = "receipt err";
        saveOut.textContent = humanError(e).text;
      }
    }),
  );
  conflictBox.append(conflictRow);
  const restartBtn = button("Перезапустить сейчас", "quiet", () => {
    // 05.10: кнопка обязана уходить с экрана в момент нажатия — иначе владелец
    // жмёт её снова и снова, не зная, взялся ли перезапуск. Движок выйдет на
    // границе хода и поднимется с новыми настройками; ошибки придут тостом.
    restartBtn.hidden = true;
    saveOut.className = "receipt";
    saveOut.textContent = "Перезапускаю движок — настройки применятся на его старте.";
    dispatchEvent(new Event("frame-restart"));
  });
  restartBtn.hidden = true;
  save.append(restartBtn);
  const saveCard = el("section", "card save-bar");
  saveCard.append(save, conflictBox, el("p", "field-hint", `Файл настроек: ${loaded.path}`));
  center.append(saveCard);

  mountSettings(container, center, built.groups || []);
}

/**
 * Экран настроек — карточки и оглавление слева. Оглавление собирается из
 * заголовков карточек: ни одной второй копии списка.
 */
function mountSettings(container: HTMLElement, center: HTMLElement, groups: SettingsGroup[] = []) {
  const wrap = el("div", "settings");
  const nav = el("nav", "settings-nav");
  nav.setAttribute("aria-label", "Разделы настроек");
  const body = el("div", "settings-body");
  center.className = "";
  body.append(center);
  const cards = [...center.querySelectorAll<HTMLElement>("section.card")].filter((c) => c.querySelector(":scope > h3"));

  // ---- раскладка по группам
  //
  // Порядок вкладок — тот, что объявило издание. Пустых вкладок не рисуем:
  // вкладка «Права на этом ПК» без единой карточки прав — обещание экрана,
  // которого за ним нет. Карточка без группы не пропадает молча, а встаёт в
  // хвост первой живой группы: молча потерять настройку хуже, чем показать её
  // не там.
  const buckets = new Map<string, HTMLElement[]>();
  for (const g of groups) buckets.set(g.id, []);
  const orphans: HTMLElement[] = [];
  for (const c of cards) {
    const bucket = buckets.get(c.dataset.group || "");
    if (bucket) bucket.push(c);
    else orphans.push(c);
  }
  const live = groups.filter((g) => (buckets.get(g.id) || []).length);
  if (orphans.length && live.length) buckets.get(live[0].id)!.push(...orphans);
  const grouped = live.length >= 2;

  let visible = cards;
  if (grouped) {
    const host = el("div", "settings-groups");
    cards[0].before(host);
    const tabs = el("div", "settings-tabs");
    tabs.setAttribute("role", "tablist");
    const boxes = new Map<string, HTMLElement>();
    for (const g of live) {
      const box = el("div", "settings-group");
      box.hidden = true;
      for (const c of buckets.get(g.id)!) box.append(c);
      host.append(box);
      boxes.set(g.id, box);
    }
    host.before(tabs);
    let saved = "";
    try {
      saved = localStorage.getItem("settings.group") || "";
    } catch {
      // без хранилища вкладка просто не запомнится
    }
    const start = live.some((g) => g.id === saved) ? saved : live[0].id;
    const pick = (id: string) => {
      for (const g of live) {
        const box = boxes.get(g.id)!;
        const on = g.id === id;
        box.hidden = !on;
        // Карточки не пересобираются — меняется только видимость; 140 мс
        // проявления хватает, чтобы переход не выглядел рывком.
        if (on) {
          box.classList.remove("group-in");
          void box.offsetWidth;
          box.classList.add("group-in");
        }
      }
      for (const t of tabs.querySelectorAll<HTMLElement>("[role=tab]")) {
        t.setAttribute("aria-selected", String(t.dataset.group === id));
      }
      visible = buckets.get(id) || [];
      // «Вид» применяется сразу — полоса «Сохранить» там не нужна и закрывала бы низ карточки.
      wrap.dataset.group = id;
      buildNav();
      try {
        localStorage.setItem("settings.group", id);
      } catch {
        // см. выше
      }
    };
    for (const g of live) {
      const t = el("button", "settings-tab", g.label) as HTMLButtonElement;
      t.type = "button";
      t.dataset.group = g.id;
      t.setAttribute("role", "tab");
      t.addEventListener("click", () => pick(g.id));
      tabs.append(t);
    }
    // Первый показ — после сборки навигации ниже: `pick` её и построит.
    queueMicrotask(() => pick(start));
  }

  const links: HTMLAnchorElement[] = [];
  function buildNav() {
    links.length = 0;
    nav.replaceChildren();
    visible.forEach((c, i) => {
      const title = c.querySelector(":scope > h3")!.textContent || "";
      c.id = "s-" + i + "-" + (c.dataset.group || "x");
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
  }
  buildNav();

  wrap.append(nav, body);
  container.replaceChildren(wrap);
  if ("IntersectionObserver" in window && cards.length) {
    const seen = new Map<Element, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target, e.isIntersecting);
        const first = visible.find((c) => seen.get(c));
        if (!first) return;
        for (const l of links) l.setAttribute("aria-current", String(l.getAttribute("href") === "#" + first.id));
      },
      // ⚠ Корень — тот, кто ВПРАВДУ прокручивается. До 17.09 контейнером был сам #view
      // (он же `.scroll`), и `root: container` случайно совпадал со скроллером. Теперь
      // раздел лежит в своём узле `.page` без собственного overflow — без этой строки
      // наблюдатель смотрел бы в неподвижный бокс, и оглавление настроек перестало бы
      // ехать за карточками МОЛЧА, без единой ошибки в консоли.
      { root: container.closest(".scroll") ?? container, rootMargin: "-10% 0px -70% 0px" },
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
/** «Копии памяти» (1.2, 27.09). Требование Егора: периодически — по умолчанию раз
 *  в неделю, настраиваемо (период, сколько хранить, папка), прежние не
 *  перезатирать. Снимает оболочка (агент живёт из окна) или служба — тем же
 *  правилом, что и установщик перед обновлением (`common/backup.rs`). Настройки
 *  уходят в helene.json кнопкой «Сохранить» вместе с остальными и применяются без
 *  перезапуска: расписание перечитывает файл на каждом тике. */
function backupCard(draft: Config): HTMLElement {
  draft.backup = draft.backup || {};
  const b = draft.backup;
  const box = el("div");
  const grid = el("div", "form-grid three");
  const every = field("Раз в сколько дней", String(b.every_days ?? 7), (v) => {
    const n = Math.max(0, Math.min(365, Math.floor(Number(v))));
    b.every_days = Number.isFinite(n) ? n : 7;
  }, { hint: "0 — не снимать по расписанию" });
  const keep = field("Сколько хранить", String(b.keep ?? 8), (v) => {
    const n = Math.max(1, Math.min(1000, Math.floor(Number(v))));
    b.keep = Number.isFinite(n) ? n : 8;
  }, { hint: "старые снимки по расписанию уходят, твои «сейчас» — никогда" });
  const dir = field("Папка", String(b.dir ?? ""), (v) => (b.dir = v.trim()), {
    mono: true,
    placeholder: "backups рядом с программой",
    hint: "пусто — рядом с программой, вне папки агента",
  });
  grid.append(every, keep, dir);
  const out = el("span", "receipt");
  const listBox = el("ul", "backup-list");
  let where = "";
  const refresh = () => {
    shell<{ dir: string; items: Array<{ name: string; bytes: number; when: number }> }>("backup_list")
      .then((r) => {
        where = r.dir;
        listBox.replaceChildren();
        const kinds: Record<string, string> = { auto: "по расписанию", manual: "по кнопке", before: "перед обновлением" };
        for (const it of r.items.slice(0, 6)) {
          const kind = it.name.includes("-auto") ? kinds.auto : it.name.includes("-manual") ? kinds.manual : it.name.includes("-before-") ? kinds.before : "";
          const when = it.when ? new Date(it.when * 1000).toLocaleString("ru-RU", { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : it.name;
          listBox.append(el("li", "", `${when}${kind ? " · " + kind : ""} · ${(it.bytes / 1048576).toFixed(1)} МБ`));
        }
        // Период 0 — расписание выключено: обещать «снимется сама» было бы неправдой.
        if (!r.items.length)
          listBox.append(el("li", "muted", (b.every_days ?? 7) === 0
            ? "Копий ещё нет, а по расписанию они выключены (0 дней): снимет кнопка выше и установщик перед обновлением."
            : "Копий ещё нет — первая снимется сама, в течение получаса после запуска."));
      })
      .catch(() => {
        // Окно без оболочки (браузер, Praxis): копии снимает сервер, здесь смотреть нечего.
        listBox.replaceChildren(el("li", "muted", "Список копий виден в окне программы на компьютере агента."));
      });
  };
  const now = button("Сделать копию сейчас", "quiet", async () => {
    now.disabled = true;
    out.className = "receipt";
    out.textContent = "Снимаю…";
    try {
      const r = await shell<{ path: string; files: number }>("backup_now");
      out.className = "receipt ok";
      out.textContent = `Готово: ${r.files} файлов — ${r.path}`;
      refresh();
    } catch (e) {
      out.className = "receipt err";
      out.textContent = humanError(e).text;
    } finally {
      now.disabled = false;
    }
  });
  const open = button("Открыть папку копий", "quiet", () => {
    if (where) void shell("reveal_path", { path: where }).catch((e) => toast(humanError(e).text));
  });
  const row = el("div", "actions");
  row.append(now, open, out);
  box.append(
    grid,
    row,
    listBox,
    el(
      "p",
      "field-hint",
      "Копия — один zip: память, конституция, рабочая папка агента, расширения, настройки и соседние агенты. " +
        "Вход в ChatGPT, журналы и голосовые модели в копию не идут. Перед каждым обновлением установщик снимает свою копию сам. " +
        "Вернуть — распаковать нужное из zip на место при закрытой программе.",
    ),
  );
  refresh();
  return card("Копии памяти", box);
}

function transferCard(draft: Config, posix = false): HTMLElement {
  const box = el("div");
  // Команда обратного импорта — путём питона ЭТОЙ системы (runtime/python.exe
  // против runtime/bin/python3): подсказка, которую копируют в консоль, обязана
  // работать как есть.
  const importCmd = posix
    ? "runtime/bin/python3 app/localharness/carry.py import --config helene.json --archive <архив>"
    : "runtime\\python.exe app\\localharness\\carry.py import --config helene.json --archive <архив>";
  const exportOut = el("span", "receipt");
  const exportBtn = button("Экспорт агента", "quiet", async () => {
    exportBtn.disabled = true;
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
    } finally { exportBtn.disabled = false; }
  });
  const exportRow = el("div", "actions");
  exportRow.append(exportBtn, exportOut);
  const steps = el("ol", "field-hint");
  steps.append(el("li", "", "Для переноса на сервер открой «Онбординг» → «Телефон, Telegram и сервер». Приложение проверит сервер, сохранит архив и подключит окно."),
    el("li", "", "Кнопка ниже собирает отдельный архив памяти, навыков, настроек и входов. Она сама не переносит и не останавливает агента."),
    el("li", "", "Ручные настройки ниже нужны, если адрес и ключ уже работающего сервера у тебя есть."));
  box.append(button("Настроить подключения","primary",()=>dispatchEvent(new CustomEvent("frame-go",{detail:"learn"}))));
  const details = el("details");
  details.append(el("summary", "field-label", "Что переносится и как вернуться на этот ПК"),
    el("p", "field-hint", "Архив содержит ключи модели, вход ChatGPT и сессию Telegram. Храни его у себя. Журналы, ключ окна, модели голоса и подключённые папки остаются на прежней машине."),
    el("p", "field-hint", `Обратный импорт при закрытой программе: ${importCmd}. Прежняя папка данных останется рядом как data.before-<штамп>.`));
  box.append(steps, exportRow, details);

  // --- окно к харнессу на сервере
  const remote = el("div");
  remote.style.marginTop = "12px";
  const remoteToggle = toggle("Подключить окно к агенту на сервере", draft.mode === "remote", (v) => {
    draft.mode = v ? "remote" : "local";
    syncRemote();
  });
  const baseField = field("Адрес канала на сервере", String(draft.base || ""), (v) => (draft.base = v), {
    mono: true,
    placeholder: "https://helene.example.com",
    hint: "HTTPS-адрес уже работающего агента. Для нового сервера используй пошаговый перенос в знакомстве с приложением.",
  });
  const keyField = field("Ключ окна", String(draft.key || ""), (v) => (draft.key = v), {
    type: "password",
    mono: true,
    hint: "Ключ, который выдала серверная установка. Он даёт этому окну доступ к агенту.",
  });
  const remoteNote = el("p", "field-hint");
  const syncRemote = () => {
    const on = draft.mode === "remote";
    baseField.hidden = !on;
    keyField.hidden = !on;
    remoteNote.textContent = on
      ? "После сохранения перезапусти окно. Для подключения с проверкой связи используй «Настроить подключения» выше. Если откроешь местную копию, в ней будут данные на момент переноса; сервер продолжит работать."
      : "Сейчас окно работает с агентом на этом компьютере. Экспорт создаёт копию и сам по себе ничего не переключает.";
  };
  remote.append(remoteToggle, baseField, keyField, remoteNote);
  syncRemote();
  box.append(remote);
  return card("Перенос", box, "Применяется перезапуском.");
}

/**
 * Карточка «Телефон»: QR, по которому телефон получает свой ключ.
 *
 * `remoteBase` не пуст — окно ходит к харнессу на сервере, и тогда всё здесь
 * другое: слушать сеть решает сервер (тумблера нет), QR ведёт на его адрес по
 * https, правило брандмауэра этой машины ни при чём. До 09.09 карточка знала
 * только местную раскладку: кнопка звала `/pair/new` на сервер и получала
 * «только с этой машины (403)», а если бы и получила пару — свела бы QR на
 * локальный адрес, куда телефону идти незачем.
 */
interface PhoneState { state: string; message: string; url?: string; telegram?: string; bot_url?: string }

export function phoneCard(draft: Config, savedEnabled: boolean, savedMode: string,
                   remoteBase: string, qrSvg: (text: string) => Promise<string>, opts:{showToggle?:boolean}={}): HTMLElement {
  draft.phone = draft.phone || {};
  draft.phone.mode = "automatic";
  const remote = !!remoteBase;
  const phone = el("div");
  const hint = el("p", "field-hint", remote
    ? "Телефон подключается к тому же каналу, что и это окно."
    : "Агент остаётся на этом компьютере. Hélène сама открывает защищённый HTTPS-доступ из любой сети — сервер, Tailscale и настройка роутера не нужны. Компьютер должен быть включён и подключён к интернету.");
  const receipt = el("p", "receipt");
  receipt.setAttribute("aria-live", "polite");
  const status: PhoneState = { state: remote ? "ready" : "waiting", message: "Проверяю подключение…", url: remoteBase };
  const devicesBox = el("div", "devices");
  const qrOut = el("div", "qr-out");
  qrOut.hidden = true;
  const drawDevices = async () => {
    try {
      const rows = await api<Array<{id: string; name: string; created: string}>>("/pair/devices");
      devicesBox.replaceChildren();
      if (!rows.length) return;
      devicesBox.append(el("p", "field-label", "Подключённые устройства"));
      for (const device of rows) {
        const row = el("div", "device-row");
        row.append(el("span", "", `${device.name} · ${new Date(device.created).toLocaleDateString("ru-RU")}`));
        row.append(button("Отвязать", "quiet", async () => {
          await post("/pair/revoke", {id:device.id}).catch(e => toast(humanError(e).text));
          void drawDevices();
        }));
        devicesBox.append(row);
      }
    } catch { /* state row below shows connection errors */ }
  };
  let busy = false;
  let qrDialog: HTMLDialogElement | null = null;
  let qrExpires = 0;
  const closeQR = () => { qrDialog?.close(); qrDialog = null; };
  const qrBtn = button("Показать QR", "quiet", async () => {
    busy = true;
    qrBtn.disabled = true;
    qrOut.hidden = false;
    qrOut.textContent = "Создаю код подключения…";
    try {
      const pair = await post<{path:string; url?:string; expires_in?:number; uses?:number}>("/pair/new", {});
      const expiresAt = Date.now() + (pair.expires_in || 600) * 1000;
      const base = remote ? (cfg.base || remoteBase) : pair.url;
      if (!base) throw new Error("Защищённый адрес ещё не готов. Дождись подключения и повтори.");
      const link = base.replace(/\/+$/, "") + pair.path;
      const svg = await qrSvg(link);
      if (!cardNode.isConnected) return;
      if (Date.now() >= expiresAt || (remote && base !== (cfg.base || remoteBase)) || (!remote && (status.state !== "ready" ||
          (status.url || "").replace(/\/+$/, "") !== base.replace(/\/+$/, "")))) {
        throw new Error("Подключение изменилось во время создания кода. Покажи QR заново.");
      }
      closeQR();
      const modal = paperDialog("Подключить телефон", true);
      qrDialog = modal.dialog;
      qrExpires = expiresAt;
      modal.body.innerHTML = `<div class="qr-pair"><div class="qr">${svg}</div><div class="qr-text">
        <p><b>Из любой сети.</b> Открой камеру телефона и наведи на код.</p>
        <p>Ссылка живёт ${Math.ceil((pair.expires_in || 600) / 60)} мин. и допускает подключений: ${pair.uses || 3}.</p>
        <p class="mono qr-url">${esc(link)}</p></div></div>
        <p class="field-hint">Для следующего входа открой кнопку в Telegram-боте или покажи свежий QR здесь. Автоматический HTTPS-адрес может измениться после перезапуска подключения.</p>`;
      qrOut.textContent = "Код показан в отдельном окне. Если он устареет, создай новый.";
      void drawDevices();
    } catch (e) {
      const error = humanError(e);
      qrOut.replaceChildren(el("p", "receipt err", "QR не создан: " + (error.detail || error.text)));
    } finally { busy = false; sync(); }
  });
  const retry = button("Повторить подключение", "quiet", async () => {
    await post("/api/phone/retry", {}).catch(e => toast(humanError(e).text));
    void refresh();
  });
  const botLink = el("a", "btn btn-quiet", "Открыть бота");
  botLink.target = "_blank";
  botLink.rel = "noopener noreferrer";
  botLink.hidden = true;
  function sync() {
    const saved = remote || (savedEnabled && savedMode === "automatic");
    const on = remote || !!draft.phone?.enabled;
    receipt.textContent = !on ? "Подключение выключено. Нажми «Сохранить», чтобы применить." : !saved
      ? "Нажми «Сохранить» — Hélène подготовит HTTPS-подключение сама." : status.message;
    receipt.classList.toggle("err", status.state === "error" || status.state === "retry");
    qrBtn.disabled = busy || !on || !saved || status.state !== "ready";
    retry.hidden = remote || !saved || !on || status.state === "ready";
    botLink.hidden = !status.bot_url || status.telegram !== "ready";
    if (status.bot_url) botLink.href = status.bot_url;
    if (!on || status.state !== "ready") qrOut.hidden = true;
    if (!on || status.state !== "ready") closeQR();
  }
  async function refresh() {
    if (remote || !savedEnabled || savedMode !== "automatic") { sync(); return; }
    try {
      const fresh = await api<PhoneState>("/api/phone");
      if (status.url && status.url !== fresh.url) { qrOut.replaceChildren(); closeQR(); }
      Object.assign(status, fresh);
    } catch (e) {
      const error = humanError(e);
      Object.assign(status, {state:"error", url:"", message:error.detail || error.text});
    }
    sync();
  }
  if (!remote&&opts.showToggle!==false) phone.append(toggle("Подключать телефон и миниапп Telegram", !!draft.phone.enabled, v => {
    draft.phone!.enabled = v;
    sync();
  }));
  phone.append(hint, receipt);
  const actions = el("div", "actions");
  actions.append(qrBtn, botLink, retry);
  phone.append(actions, qrOut, devicesBox);
  void refresh();
  void drawDevices();
  const cardNode = card("Телефон", phone);
  const poll = () => {
    if (!cardNode.isConnected) { closeQR(); return; }
    if (remote && status.url !== (cfg.base || remoteBase)) {
      status.url=cfg.base||remoteBase;closeQR();qrOut.replaceChildren();
    }
    if (qrDialog && Date.now() >= qrExpires) { closeQR(); qrOut.textContent = "Время кода истекло. Нажми «Показать QR», чтобы создать новый."; }
    if (!remote) void refresh();
    setTimeout(poll, 2000);
  };
  setTimeout(poll, 2000);
  return cardNode;
}
