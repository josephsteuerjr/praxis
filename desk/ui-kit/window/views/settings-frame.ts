import { t as uiText, UiError, locale } from "../../../lang/lang";
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
import { api, cfg, electron, inTauri, post, shell } from "../api";
import { componentsCard } from "../components-card";
import { serverBridgePanel } from '../server-settings';
import { useBridge, type ServerBridge } from '../server-bridge-config';
import { updateError } from "../../update-error";
import { requestAgentUpdate } from '../update-admission';
import { keepBlock } from "../config";
import { bindFail, el, failHTML, humanError, toast } from "../lib";
import { button, card, field, toggle } from "../../dom";
import { PRODUCT_NAME, S } from "../state";
import { hostInfo, type HostInfo } from "../host";
import { lookCard } from "../look";
import { deskTrialHTML, type UpdateState } from "./update-card";
import { isMacPlatform, platformOf } from "../../platform";
import { relayAuthCard } from "../relay-auth-card";
import { collectConnection } from "../server-connection";
import { paperDialog } from "../../paper-dialog";
import { ownerContextCard, validateOwnerContext } from "./owner-context-card";
import { selfReviewCard } from "./self-review-card";
import { workActivity, observeActivity, type WorkActivity, type UpdateActivity } from '../work-activity';
import '../styles/work-activity.css';
import { SERVER_TRANSFER_NOTICE } from '../feature-availability';
import { updateOperationControls } from '../update-operation-controls';
import { sourceModeCards } from '../source-mode-cards';
import '../styles/source-mode-cards.css';
import { t, saveLocale, localePreference, type Lang } from '../../../lang/lang';
import { languageField } from '../../../lang/controls';

/** Блоки конфига, которые движок читает только на старте, — по имени для расписки. */
export const RESTART_BLOCKS: Array<[string, () => string]> = [
  ["network", () => uiText("ui.ui.kit.window.views.settings.frame.47c853d7e7")],
  ["telegram", () => "Telegram"],
  ["computer", () => uiText("ui.ui.kit.window.views.settings.frame.735c0193ca")],
  ["agents", () => uiText("ui.ui.kit.window.views.settings.frame.1605473766")],
  ["sandbox", () => uiText("ui.ui.kit.window.views.settings.frame.c880a9aac2")],
  ["agent_mode", () => uiText("ui.ui.kit.window.views.settings.frame.00184094d8")],
  ["owner_context", () => uiText("ui.ui.kit.window.views.settings.frame.85e47c0e2a")],
];

/** Какие из RESTART_BLOCKS изменились между сохранённым и новым конфигом (стабильный JSON). */
export function blocksNeedingRestart(before: unknown, after: unknown): string[] {
  const deep = (v: unknown): string => {
    if (!v || typeof v !== "object") return JSON.stringify(v ?? null);
    if (Array.isArray(v)) return "[" + v.map(deep).join(",") + "]";
    const o = v as Record<string, unknown>;
    return "{" + Object.keys(o).sort().map((k) => JSON.stringify(k) + ":" + deep(o[k])).join(",") + "}";
  };
  const b = (before && typeof before === "object") ? (before as Record<string, unknown>) : {};
  const a = (after && typeof after === "object") ? (after as Record<string, unknown>) : {};
  const out: string[] = [];
  for (const [key, title] of RESTART_BLOCKS) {
    // Admission switches and folder/scopes grants are re-read by the running tools.
    const startup = (value: unknown): unknown => {
      if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
      const hot: Record<string, string[]> = {telegram:['status_message','allow_from','allowed_ids'],
        sandbox:['mounts','mounts_denied'],computer:['scopes']};
      return Object.fromEntries(Object.entries(value).filter(([name])=>!hot[key]?.includes(name)));
    };
    if (deep(startup(b[key])) !== deep(startup(a[key]))) out.push(title());
  }
  return out;
}

export type SettingsRestart = '' | 'engine' | 'window' | 'application';
export function restartForSettings(before: unknown, after: unknown, first = false): SettingsRestart {
  const b=(before || {}) as Record<string,unknown>, a=(after || {}) as Record<string,unknown>;
  // These bind the session owner itself. A worker restart cannot rebind its data/runtime.
  if (['tree','code','python','app','runner'].some(key=>a[key]!==b[key])) return 'application';
  // The session refreshes transport children, but the viewing window needs its new channel.
  if (first || (a.mode || 'local')!==(b.mode || 'local') || a.port!==b.port
      || (a.mode==='remote' && (a.base!==b.base || a.key!==b.key))) return 'window';
  return blocksNeedingRestart(before,after).length ? 'engine' : '';
}

export function configureRestart(button: HTMLButtonElement, scope: SettingsRestart, blocks: string[]): string {
  button.dataset.scope=scope;
  button.hidden=!scope;
  button.textContent=scope==='application' ? uiText("ui.ui.kit.window.views.settings.frame.a489a6740a") : scope==='window'
    ? uiText("ui.ui.kit.window.views.settings.frame.321f7b8033") : uiText("ui.ui.kit.window.views.settings.frame.bfa262aa90");
  if(scope==='application')return uiText("ui.ui.kit.window.views.settings.frame.6bc9ebc797");
  if(scope==='window')return uiText("ui.ui.kit.window.views.settings.frame.b82eb257f6");
  return scope ? uiText("ui.ui.kit.window.views.settings.frame.ff28b94a92", {p0: String(blocks.join(', '))}) : '';
}

export interface Config {
  network?: { proxy?: Record<string, unknown>; bridge_profile?:string; [k: string]: unknown };
  lang?: Lang | '';
  agent?: { name?: string; frame_language?: Lang; living_prose?: boolean; [key: string]: unknown };
  // `external` — внешний адрес канала (06.10): сервер с белым IP, под которым
  // этот компьютер виден из любой сети. QR по нему работает и вне этой Wi-Fi.
  phone?: { enabled?: boolean; mode?: string; external?: string; local_port?: number; bridge_profile?:string };
  port?: number;
  update?: { url?: string };
  // 1.2: копии памяти по расписанию (common/backup.rs): раз в `every_days` дней (0 —
  // выключено), хранить `keep` снимков, папка `dir` (пусто — backups рядом с программой).
  backup?: { every_days?: number; keep?: number; dir?: string };
  owner?: { name?: string; room?: string };
  model?: { framework?: string; provider?: string; provider_id?: string; api_mode?: string; base_url?: string; model?: string; key?: string; keys?: Record<string, string>; max_tokens?: number; reasoning_effort?: string; service_tier?: string; fallback_model?: string; fallback_framework?: string; fallback_base_url?: string; fallback_key?: string; fallback_api_mode?: string; fallback_provider_id?: string; vision_model?: string; pinned?: boolean };
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
  owner_context?: { messages?: number; characters?: number; tokens?: number; [k: string]: unknown };
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

export function focusUpdates(container: HTMLElement): void {
  container.querySelector<HTMLButtonElement>('[role="tab"][data-group="app"]')?.click();
  // The section navigation owns the custom scroller and sticky-tab offset.
  const about=Array.from(container.querySelectorAll<HTMLAnchorElement>('nav a'))
    .find(link=>link.textContent?.trim()===uiText("ui.ui.kit.window.views.settings.frame.254f667d9a"));
  if (about)about.click();
  else container.querySelector<HTMLElement>('[data-updates]')?.scrollIntoView({block:'start'});
}

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
  /** Refresh route-specific controls only after a confirmed config write. */
  didSave?(config: Config): void;
  networkSettings?: HTMLElement;
  useServerBridge?(bridge:ServerBridge,options?:{relay?:boolean}):void;
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
  const activityScope=()=>cfg.base+'\n'+cfg.key+'\n'+cfg.agent,settingsScope=activityScope();
  delete container.dataset.settingsDirty; // Explicit reload discards the old draft.
  if (!inTauri) {
    const center = el("div", "center");
    // Тумблер телефона в вебе был пустышкой: черновик выбрасывался в мусор,
    // кнопки «Сохранить» в этой ветке нет вовсе — человек щёлкал, и ничего не
    // происходило, и никто не говорил, что не происходит.
    center.append(el("div", "card muted", uiText("ui.ui.kit.window.views.settings.frame.f2b95e109e", {p0: String(PRODUCT_NAME)})));
    // «Вид» — удобство этого окна, а не настройка агента: доступен и здесь.
    center.append(selfReviewCard(), lookCard());
    mountSettings(container, center);
    return;
  }
  let loaded: Loaded;
  try {
    loaded = await shell<Loaded>("config_load");
  } catch (e) {
    if(activityScope()!==settingsScope)return;
    container.innerHTML = failHTML(e);
    bindFail(container, () => void render(container, edition));
    return;
  }
  if(activityScope()!==settingsScope)return;
  // Срез «как лежит файл» — let: рамка тянет его вперёд за записями самой
  // карточки (freshness.accept ниже); черновик и издание остаются при срезе
  // открытия, а базой расписки перезапуска становится живой файл.
  let c = loaded.config;
  // Обязательные блоки заводим ЗДЕСЬ и объявляем это типом `Draft`: издание
  // опирается на них полутора сотнями строк карточек, и «наверное, есть» в
  // каждой из них было бы глушением проверки на настоящей гарантии.
  const draft = JSON.parse(JSON.stringify(c)) as Draft;
  // This client can differ from the installation default saved by another window.
  draft.lang = localePreference();
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
      uiText("ui.ui.kit.window.views.settings.frame.3cd189bc2d") +
      uiText("ui.ui.kit.window.views.settings.frame.a5c8db35d2") +
      uiText("ui.ui.kit.window.views.settings.frame.1a1338d483")));
  }
  if (cfg.needs_subscription_login) {
    center.append(el('div','notice',uiText("ui.ui.kit.window.views.settings.frame.eb5131b7d6")));
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
    field(uiText("ui.ui.kit.window.views.settings.frame.ba527ef131"), String(draft.agent.name || ""), (v) => (draft.agent!.name = v)),
    field(uiText("ui.ui.kit.window.views.settings.frame.1a399e1b3a"), String(draft.owner.name || ""), (v) => (draft.owner!.name = v)),
  );
  // Настройки пишут только helene.json. Конституцию (data/soul/SOUL.md) не
  // переписывает никто, кроме установщика, — а в ней старые имена остаются
  // навсегда, и агент в своём K-слое читает именно их. Обещать обратное нельзя.
  center.append(inGroup(card(uiText("ui.ui.kit.window.views.settings.frame.140688c04f"), names, built.namesHint), GROUP.agent));
  const agentLanguage = languageField(t('language.frame'), draft.agent.frame_language || 'ru',
    value => { draft.agent.frame_language = value as Lang; });
  agentLanguage.dataset.setting = 'agent.frame_language';
  const prose = toggle(t('language.living_prose'), draft.agent.living_prose !== false,
    value => { draft.agent.living_prose = value; });
  prose.dataset.setting = 'agent.living_prose';
  center.append(inGroup(card(t('language.agent_section'), agentLanguage,
    t('language.frame_hint'), prose, t('language.living_prose_hint')), GROUP.agent));
  const uiLanguage = languageField(t('language.interface'), draft.lang ?? localePreference(),
    value => { draft.lang = value; }, true);
  uiLanguage.dataset.setting = 'lang';
  center.append(inGroup(card(t('language.interface'), uiLanguage, t('language.interface_hint')), GROUP.look));
  if (draft.mode !== "remote") center.append(inGroup(ownerContextCard(draft), GROUP.agent));

  for (const box of built.cards) center.append(box);

  center.append(inGroup(selfReviewCard({ draft, freshness }), GROUP.agent));

  const chooseBridge=(bridge:ServerBridge,options:{relay?:boolean}={})=>{
    if(draft.mode==='remote')throw new UiError(uiText("ui.ui.kit.window.views.settings.frame.18eb1ae156"));
    useBridge(draft,bridge,options);built.useServerBridge?.(bridge,options);
    phoneSettings.dispatchEvent(new CustomEvent('bridge-selected',{detail:bridge}));
    phoneSettings.dispatchEvent(new Event('input',{bubbles:true}));
    return uiText('bridge.routes.draft');
  };
  const phoneSettings=phoneCard(draft, !!c.phone?.enabled, String(c.phone?.mode || ""), built.phoneBase, built.qrSvg,{onBridge:chooseBridge});
  if (built.networkSettings && electron?.shell === 'python' && draft.mode !== 'remote') {
    const bridge = el('details','fold');
    bridge.append(el('summary','',uiText('connections.ssh_bridge')));
    const openBridge = () => {
      if (!bridge.open) return;
      bridge.append(serverBridgePanel(chooseBridge));
      bridge.removeEventListener('toggle',openBridge);
    };
    bridge.addEventListener('toggle',openBridge);
    built.networkSettings.append(bridge);
  }
  center.append(inGroup(phoneSettings, GROUP.brain));

  // --- перенос: экспорт агента одним архивом и окно к харнессу на сервере
  center.append(inGroup(transferCard(draft, posix), GROUP.app));
  if (c.mode === "remote" && c.base) {
    center.append(inGroup(relayAuthCard(String(c.base)), GROUP.brain));
  }

  if (electron?.shell === "python") {
    center.append(inGroup(componentsCard(), GROUP.app));
    const serverBody=el('div');
    const openServer=button(uiText("ui.ui.kit.window.views.settings.frame.01c36d809c"),'quiet',async()=>{
      openServer.disabled=true;
      try{await (await import('../connection-guide')).mountServerManagement(serverBody);}
      catch(error){serverBody.textContent=humanError(error).text;}
      finally{openServer.disabled=false;}
    });
    const placement=el('div');placement.append(openServer,serverBody);
    center.append(inGroup(card(uiText("ui.ui.kit.window.views.settings.frame.bd9a72f72b"),placement),GROUP.app));
  }

  // --- копии памяти (1.2): расписание, «сейчас», последние снимки
  center.append(inGroup(backupCard(draft), GROUP.app));

  // --- автозапуск
  const auto = el("div");
  let autoState: boolean | null = null;
  const readAutostart = async () => {
    const value = await shell<boolean>("autostart_get");
    if (typeof value !== "boolean") throw new UiError(uiText("ui.ui.kit.window.views.settings.frame.9e2d9b6b18"));
    autoState = value;
    autoToggle.setAttribute("aria-checked", String(value));
    return value;
  };
  // На macOS это LaunchAgent при входе в систему — «Windows» в подписи был бы чужим словом.
  const autoToggle = toggle(posix ? uiText("ui.ui.kit.window.views.settings.frame.9225bc7e7f") : uiText("ui.ui.kit.window.views.settings.frame.810f7494ef"), false, async (v) => {
    autoToggle.disabled = true;
    autoToggle.setAttribute("aria-busy", "true");
    let failure = "";
    try {
      await shell("autostart_set", { on: v });
    } catch (e) {
      failure = humanError(e).text;
    }
    try {
      const current = await readAutostart();
      toast(failure || (current !== v ? uiText("ui.ui.kit.window.views.settings.frame.ad4d371d77") : current ? uiText("ui.ui.kit.window.views.settings.frame.e9ffca82ae") : uiText("ui.ui.kit.window.views.settings.frame.3989033996")));
    } catch (e) {
      autoToggle.setAttribute("aria-checked", String(autoState ?? false));
      autoState = null;
      toast([failure, humanError(e).text].filter(Boolean).join(" · "));
    } finally {
      autoToggle.disabled = autoState === null;
      autoToggle.removeAttribute("aria-busy");
    }
  });
  autoToggle.disabled = true;
  readAutostart().then(() => { autoToggle.disabled = false; }).catch((e) => toast(humanError(e).text));
  auto.append(autoToggle);
  center.append(inGroup(card(uiText("ui.ui.kit.window.views.settings.frame.18d54d8fce"), auto), GROUP.app));

  // Тема — только как в системе (слово владельца 07.09): переключателя нет. Палитры дня
  // и ночи, фактура, текст и физика ленты — в «Виде» (28.09), применяются сразу.
  center.append(inGroup(lookCard(), GROUP.look));


  // --- о программе
  draft.update = draft.update || {};
  const about = el("div");
  about.dataset.updates='';
  const aboutRow = el("div", "actions");
  const ver = el("span", "mono", uiText("ui.ui.kit.window.views.settings.frame.eea5354739"));
  let aboutInfo: { version: string; exe_dir: string; log: string } | null = null;
  shell<{ version: string; exe_dir: string; log: string }>("app_info")
    .then((i) => {
      aboutInfo = i;
      ver.textContent = uiText("ui.ui.kit.window.views.settings.frame.0295e8b3df", {p0: String(i.version)});
    })
    .catch(() => {
      // Оболочки нет — окно открыто браузером (Praxis на сервере). Версию
      // тогда называет канал: пакет desk, которым он поднят.
      const d = S.agentState?.desk;
      ver.textContent = d?.version
        ? uiText("ui.ui.kit.window.views.settings.frame.6e12de1fc8", {p0: String(d.version), p1: String(d.digest)})
        : uiText("ui.ui.kit.window.views.settings.frame.804128629a");
    });
  const updOut = el("span", "receipt");
  // Кнопка создаётся один раз и переключается. Раньше её добавляли внутрь
  // обработчика: три нажатия «Проверить обновления» — три кнопки «Скачать» в
  // ряд, и она оставалась висеть даже рядом с «Это последняя версия».
  let updUrl = "";
  let updSha = "";
  // Скачать и поставить — оболочка (КОНТРАКТ A→B §5: update_download →
  // update_install). Несовпадение суммы оболочка отвергает сама (throw, файл
  // удалён); `sha_ok: null` — сверять было не с чем. Установщик гасит
  // программу; положительная квитанция появляется лишь ПОСЛЕ handoff.
  // Python обновляет рабочую папку собственным helper, без setup.
  const pythonUpdate = electron?.shell === 'python';
  let sourceMode: 'preserve' | 'clean' | undefined;
  const sourceChoices = el('div', 'field');
  const modeChoice = sourceModeCards(value => { sourceMode = value; showMode(); });
  modeChoice.configure([]);
  const sourceHint = el('p', 'field-hint', uiText("ui.ui.kit.window.views.settings.frame.5dfea6667f"));
  sourceHint.id = 'update-source-mode-hint';
  const reopenHint = el('p', 'field-hint', uiText("ui.ui.kit.window.views.settings.frame.9230125673"));
  reopenHint.id = 'update-reopen-hint';
  const developerDetails = el('details', 'muted');
  developerDetails.append(el('summary', '', uiText("ui.ui.kit.window.views.settings.frame.0cb53306a0")), el('p', 'field-hint', uiText("ui.ui.kit.window.views.settings.frame.67d4dbcc9e")));
  const showMode = () => {
    sourceHint.textContent = sourceMode === 'preserve'
      ? uiText("ui.ui.kit.window.views.settings.frame.979af03eb7")
      : sourceMode === 'clean'
        ? uiText("ui.ui.kit.window.views.settings.frame.6ca3b114d5")
        : uiText("ui.ui.kit.window.views.settings.frame.c97dc36c30");
    developerDetails.hidden = sourceMode !== 'preserve';
  };
  const preservedHint = el('p', 'field-hint', uiText("ui.ui.kit.window.views.settings.frame.af97e7e3c9"));
  sourceChoices.append(modeChoice.el, preservedHint, sourceHint, reopenHint, developerDetails); sourceChoices.hidden = !pythonUpdate;
  let agentProposalId = '';
  const confirmAgentUpdate = button(uiText("ui.ui.kit.window.views.settings.frame.c339b28365"), 'primary', async () => {
    if (!agentProposalId) return;
    confirmAgentUpdate.disabled = true;
    beginUpdateObservation();
    try {
      await shell('update_agent_confirm', {id: agentProposalId});
      updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.b45f90e702");
      confirmAgentUpdate.hidden = true;
      setTimeout(restoreUpdateProgress, 600);
    } catch (error) { confirmAgentUpdate.disabled = false; toast(humanError(error).text); }
  });
  confirmAgentUpdate.hidden = true;
  sourceChoices.append(confirmAgentUpdate);
  about.append(sourceChoices);
  const loadSourceModes = async () => {
    const row = await shell<{id?: string; state?: string; source_mode?: string; proposal_current?: boolean;
      source_modes?: string[]; default_source_mode?: 'preserve' | 'clean' | null; source_mode_error?: string}>('update_agent_request');
    const modes = (row.source_modes || []).filter(v => v === 'clean' || v === 'preserve');
    // A default, including clean-only legacy admission, is not an owner click.
    // Re-reading availability must also retain an explicit choice already made.
    if (sourceMode && !modes.includes(sourceMode)) sourceMode = undefined;
    const bound = !!row.id && !!row.proposal_current && ['awaiting', 'confirmed', 'running', 'handed_off', 'attention', 'paused'].includes(row.state || '');
    if (bound && (row.source_mode === 'clean' || row.source_mode === 'preserve')) sourceMode = row.source_mode;
    modeChoice.configure(modes, sourceMode, bound); showMode();
    if (row.state !== 'awaiting' || !row.id || !row.proposal_current || !sourceChoices.isConnected) return;
    if (row.source_mode !== 'clean' && row.source_mode !== 'preserve') return;
    if (!modes.includes(row.source_mode)) return;
    sourceMode = row.source_mode;
    agentProposalId = row.id;
    confirmAgentUpdate.hidden = false;
  };
  if (pythonUpdate) void loadSourceModes().catch(() => { sourceHint.textContent = uiText("ui.ui.kit.window.views.settings.frame.427c3fa715"); });
  // Reconnecting the old UI reads the same operation, not a fresh invented
  // progress counter. Poll only while that operation is nonterminal; leaving
  // Settings ends the observer without retaining the removed view.
  let observedUpdateId = '';
  let updateObservationGeneration = 0, issuedProgressRead = 0, appliedProgressRead = 0;
  const beginUpdateObservation = () => {
    observedUpdateId = '';
    updateObservationGeneration++;
  };
  const restoreUpdateProgress = async () => {
    if (!pythonUpdate) return;
    const scope=activityScope();
    const generation=updateObservationGeneration, sequence=++issuedProgressRead;
    try {
      const row = await shell<{ id?: string; stage: string; label?: string; error?: string; bytes?: number; source_update?: UpdateActivity } | null>('update_progress');
      if (!updOut.isConnected || scope!==activityScope() || scope!==settingsScope
          || generation!==updateObservationGeneration || sequence<appliedProgressRead) return;
      appliedProgressRead=sequence;
      if (!row) return;
      // An older in-flight overview reply may arrive after a new admission.
      // Never display or finish that prior operation as the accepted job.
      if (observedUpdateId && row.id !== observedUpdateId) {
        setTimeout(restoreUpdateProgress, 600);
        return;
      }
      updOut.textContent = [row.label || row.stage, row.error || ''].filter(Boolean).join(' · ');
      progressUpdate=row.source_update;renderSettingsWork();
      const controls=updateOperationControls(row.stage);
      cancelUpdate.hidden = !controls.cancellable;
      cancelUpdate.textContent = controls.cancelLabel;
      const active = controls.active;
      dlBtn.disabled = active;
      restoreUpstream.disabled = active;
      if (active) setTimeout(restoreUpdateProgress, 600);
    } catch (error) {
      if (updOut.isConnected && scope===activityScope() && scope===settingsScope
          && generation===updateObservationGeneration && sequence>=appliedProgressRead) {
        appliedProgressRead=sequence;
        updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.4a25df072c", {p0: String(humanError(error).text)});
      }
    }
  };
  const updateWork=el('section');about.append(updateWork);const renderUpdateWork=workActivity(updateWork);
  let activity: WorkActivity = {}, progressUpdate: UpdateActivity | undefined;
  const renderSettingsWork=()=>{
    if(activityScope()!==settingsScope){renderUpdateWork({});return;}
    const progress=progressUpdate;
    const update=progress && activity.update?.id!==progress.id ? progress : activity.update ?? progress;
    const reviewers=(activity.reviewers || []).filter(row=>!update || !!update.review_id && row.review_id===update.review_id);
    renderUpdateWork({...activity,update,reviewers});
  };
  if (pythonUpdate) setTimeout(restoreUpdateProgress, 0);
  const legacyLinux = linux && !pythonUpdate;
  let updateHandedOff = false;
  const cancelUpdate = button(uiText("ui.ui.kit.window.views.settings.frame.326245a16e"), 'quiet', async () => {
    try {
      await shell('update_cancel');
      cancelUpdate.hidden = true;
      updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.147cf6093c");
    } catch (error) { toast(humanError(error).text); }
  });
  cancelUpdate.hidden = true;
  const manualUpdate = button(uiText("ui.ui.kit.window.views.settings.frame.d72d1a2146"), 'quiet', async () => {
    await shell('open_path', { path: 'https://github.com/josephsteuerjr/praxis/releases/latest' });
  });
  manualUpdate.hidden = true;
  const dlBtn = button(legacyLinux ? uiText("ui.ui.kit.window.views.settings.frame.1ec432d1c1") : pythonUpdate ? uiText("ui.ui.kit.window.views.settings.frame.0381fb1a3b") : uiText("ui.ui.kit.window.views.settings.frame.be83518bd3"), "primary", async () => {
    if (!updUrl) return;
    if (pythonUpdate) {
      if (!sourceMode) { try { await loadSourceModes(); } catch { /* admission has not been sent */ } }
      if (!sourceMode) { toast(uiText("ui.ui.kit.window.views.settings.frame.c9ffe1b454")); return; }
      dlBtn.disabled = true;
      modeChoice.lock();
      updOut.className = 'receipt'; updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.30136bb0db");
      beginUpdateObservation();
      try {
        const admitted = await requestAgentUpdate(shell, updUrl, updSha, sourceMode);
        observedUpdateId = admitted.job.id;
        updateHandedOff = true;
        updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.c2537ad91a");
        setTimeout(restoreUpdateProgress, 600);
      } catch (error) {
        // A lost reply is not permission to submit another job or silently
        // fall back to the old window-owned download/installer path.
        updOut.className = 'receipt err';
        updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.cc872491ac", {p0: String(humanError(error).text)});
        setTimeout(restoreUpdateProgress, 600);
      }
      return;
    }
    if (legacyLinux) {
      await shell("open_path", { path: updUrl }).catch((e) => toast(humanError(e).text));
      updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.73ce8c4336");
      return;
    }
    dlBtn.disabled = true;
    updOut.className = "receipt";
    updOut.textContent = pythonUpdate ? uiText("ui.ui.kit.window.views.settings.frame.fb83912442") : uiText("ui.ui.kit.window.views.settings.frame.79700fcb96");
    let updateStage: 'download' | 'install' = 'download';
    // The Python owner emits the durable operation receipt throughout preclose
    // preparation. The small updater window keeps reading it after this exits.
    let operationId = '';
    const updateProgress = (event: Event) => {
      if (!updOut.isConnected) return;
      const row = (event as CustomEvent).detail;
      if (!row || typeof row.id !== 'string' || typeof row.label !== 'string') return;
      if (operationId && operationId !== row.id) return;
      operationId = row.id;
      cancelUpdate.hidden = !['waiting_review','waiting_agent'].includes(row.stage);
      const details: string[] = [];
      if (typeof row.bytes === 'number' && row.bytes > 0) details.push(uiText("ui.ui.kit.window.views.settings.frame.5fcdadeb1a", {p0: String((row.bytes / 1048576).toFixed(1))}));
      if (typeof row.files === 'number' && row.files > 0) details.push(uiText("ui.ui.kit.window.views.settings.frame.d6d7c420ce", {p0: String(row.files)}));
      updOut.textContent = [row.label, ...details].join(' · ');
    };
    if (pythonUpdate) window.addEventListener('helene:package-progress', updateProgress);
    try {
      const got = await shell<{ path: string; bytes?: number; sha256?: string; sha_ok: boolean | null }>("update_download", { url: updUrl, sha256: updSha });
      const checked = got.sha_ok === true ? uiText("ui.ui.kit.window.views.settings.frame.4b7e98a47f") : got.sha_ok === null ? uiText("ui.ui.kit.window.views.settings.frame.cca2165e0b") : uiText("ui.ui.kit.window.views.settings.frame.5fad0b7478");
      updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.4c4325bbea", {p0: String(checked)});
      // Compatibility belongs to the current agent-owned update protocol.
      // The obsolete extension-incompatibility bypass is no longer an action.
      updateStage = 'install';
      const installed = await shell("update_install", { path: got.path, ...(pythonUpdate ? {sourceMode} : {}) }) as { state?: string; message?: string } | null;
      if (installed?.state === 'installer_opened') {
        updOut.textContent = installed.message || uiText("ui.ui.kit.window.views.settings.frame.7bcb42dd13");
        return;
      }
      if (installed?.state === 'needs_resolution') {
        updOut.textContent = installed.message || uiText("ui.ui.kit.window.views.settings.frame.7754f92b67");
        return;
      }
      if (installed?.state === 'cancelled') {
        updOut.textContent = installed.message || uiText("ui.ui.kit.window.views.settings.frame.c59361b97c");
        return;
      }
      updateHandedOff = true;
      updOut.textContent = pythonUpdate ? uiText("ui.ui.kit.window.views.settings.frame.ccdfd7d5c7") : uiText("ui.ui.kit.window.views.settings.frame.bfa7b5f03a");
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (!pythonUpdate && /not found|неизвестн|unknown|command/i.test(text)) {
        updOut.textContent = mac
          ? uiText("ui.ui.kit.window.views.settings.frame.03e1335b12")
          : uiText("ui.ui.kit.window.views.settings.frame.6e78ef36f3");
        void shell("open_path", { path: updUrl }).catch((e2) => toast(humanError(e2).text));
      } else {
        updOut.className = "receipt err";
        updOut.textContent = updateError(updateStage, e);
      }
      manualUpdate.hidden = false;
    } finally {
      window.removeEventListener('helene:package-progress', updateProgress);
      cancelUpdate.hidden = true;
      dlBtn.disabled = updateHandedOff;
    }
  });
  dlBtn.hidden = true;
  const restoreUpstream = button(uiText("ui.ui.kit.window.views.settings.frame.644926f8cb"), 'quiet', () => {
    const modal = paperDialog(uiText("ui.ui.kit.window.views.settings.frame.a975911637"));
    modal.body.append(el('p', '', uiText("ui.ui.kit.window.views.settings.frame.e7957b9cdb")));
    const apply = button(uiText("ui.ui.kit.window.views.settings.frame.5f50c277bd"), 'primary', async () => {
      apply.disabled = true;
      beginUpdateObservation();
      try {
        const admitted = await shell<{state?: string; operation_id?: string}>('restore_upstream', {confirmed: true});
        observedUpdateId = admitted?.state === 'admitted' ? admitted.operation_id || '' : '';
        restoreUpstream.disabled = true;
        updOut.textContent = admitted?.state === 'admitted'
          ? uiText("ui.ui.kit.window.views.settings.frame.80090a6eb8")
          : uiText("ui.ui.kit.window.views.settings.frame.15bf2e1c22");
        setTimeout(restoreUpdateProgress, 600);
        modal.dialog.close();
      } catch (error) {
        apply.disabled = false; toast(humanError(error).text);
        // A lost admission reply is observed through the existing durable job;
        // it never launches another window-owned recovery worker.
        setTimeout(restoreUpdateProgress, 600);
      }
    });
    modal.footer.append(button(uiText("ui.ui.kit.window.views.settings.frame.8fbe9b75cb"), 'quiet', () => modal.dialog.close()), apply);
  });
  restoreUpstream.hidden = !pythonUpdate;
  about.append(manualUpdate);
  aboutRow.append(
    ver,
    button(uiText("ui.ui.kit.window.views.settings.frame.93b08f7b14"), "quiet", async () => {
      updOut.className = "receipt";
      updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.25cd3cf450");
      try {
        const r = await shell<{ current: string; latest: string; newer: boolean; url: string; notes: string; sha256?: string }>("update_check", {
          url: String(draft.update?.url || "").trim() || UPDATE_URL_DEFAULT,
        });
        if (r.newer) {
          updOut.className = "receipt ok";
          updOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.0e1eac53ff", {p0: String(r.latest), p1: String(r.notes || "")}).trim();
          updUrl = r.url || "";
          updSha = r.sha256 || "";
          dlBtn.hidden = !updUrl;
        } else {
          updOut.textContent = r.latest === r.current
            ? uiText("ui.ui.kit.window.views.settings.frame.270a544544", {p0: String(r.current)})
            : uiText("ui.ui.kit.window.views.settings.frame.34e3f72dc4", {p0: String(r.current), p1: String(r.latest)});
          updUrl = "";
          updSha = "";
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
    restoreUpstream,
    cancelUpdate,
    updOut,
  );
  const logsRow = el("div", "actions");
  logsRow.append(
    button(uiText("ui.ui.kit.window.views.settings.frame.6580927797"), "quiet", async () => {
      try {
        const p = await shell<string>("logs_bundle");
        toast(uiText("ui.ui.kit.window.views.settings.frame.40da34e969") + p);
        await shell("reveal_path", { path: p });
      } catch (e) {
        toast(humanError(e).text);
      }
    }),
    button(uiText("ui.ui.kit.window.views.settings.frame.02020a507f"), "quiet", () => {
      if (aboutInfo) void shell("open_path", { path: aboutInfo.log }).catch((e) => toast(humanError(e).text));
    }),
  );
  // Выпуск менял интерфейс, прежняя папка отложена рядом (КОНТРАКТ A→B §5).
  const prev = String((c.installed && (c.installed as Record<string, unknown>).static_prev) || "").trim();
  if (prev) {
    about.append(el("p", "field-hint", uiText("ui.ui.kit.window.views.settings.frame.04ee482f56", {p0: String(prev)})));
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
    field(uiText("ui.ui.kit.window.views.settings.frame.1b821df2a9"), String(draft.update?.url || ""), (v) => (draft.update!.url = v), {
      mono: true,
      placeholder: uiText("ui.ui.kit.window.views.settings.frame.b6d8935862"),
      hint: uiText("ui.ui.kit.window.views.settings.frame.f54d268f73"),
    }),
    logsRow,
  );
  center.append(inGroup(card(uiText("ui.ui.kit.window.views.settings.frame.254f667d9a"), about), GROUP.app));

  // --- сохранить
  const save = el("div", "actions");
  const saveOut = el("span", "receipt");
  save.append(
    button(uiText("ui.ui.kit.window.views.settings.frame.b4d30cae52"), "primary", async () => {
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
      if(out.phone?.mode==='stable')out.phone.local_port=draft.phone?.local_port || draft.port;
      const connectionError = collectConnection(out, draft);
      if (connectionError) {
        saveOut.className = "receipt err";
        saveOut.textContent = connectionError;
        return;
      }
      out.setup_complete = true;
      try {
        const before = c;
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
        const restartBlocks = blocksNeedingRestart(before, out);
        const restartNote = configureRestart(restartBtn,restartForSettings(before,out,!!cfg.needs_local_setup),restartBlocks);
        saveOut.textContent =
          uiText("ui.ui.kit.window.views.settings.frame.f5a508aaaf") +
          relayWords + restartNote + modeNote;
        if (cfg.needs_local_setup) saveOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.09c42c74b7");
        // Пустой ключ при первом запуске — не «настройки сохранены», а полдела:
        // движок поднимется, но агент без мозга молчит. Обещать «впервые запустить
        // агента» здесь было бы ложью; кнопка перезапуска в этой тропе не нужна.
        if (cfg.needs_local_setup && !String(out.model?.key || "").trim()) {
          saveOut.className = "receipt err";
          saveOut.textContent =
            uiText("ui.ui.kit.window.views.settings.frame.23d4a2e64e") +
            uiText("ui.ui.kit.window.views.settings.frame.8907062253");
          restartBtn.hidden = true;
        } else {
          restartBtn.hidden = !restartNote;
        }
        S.agent = String(out.agent?.name || S.agent);
      } catch (e) {
        if (e instanceof StaleConfig) {
          // Файл менял кто-то ещё (установщик, харнесс, Блокнот). Развилка
          // вместо молчаливой перезаписи — как у /api/md.
          saveOut.className = "receipt err";
          saveOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.ebb88e16a0");
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
    validateOwnerContext(out);
    const revision = container.dataset.settingsRevision;
    const args: Record<string, unknown> = { config: JSON.stringify(out) };
    if (seenMtime && !force) args.mtimeNs = seenMtime;
    try {
      const r = await shell<{ ok?: boolean; code?: string; error?: string; mtime_ns?: string | number; relay?: string } | null>("config_save", args);
      // КОНТРАКТ A→B §1: `{ok: false, code: "stale", mtime_ns, error}` — файл
      // менял кто-то ещё, черновик не записан.
      if (r && typeof r === "object" && r.ok === false && r.code === "stale") throw new StaleConfig(r.error || "stale");
      if (r && typeof r === "object" && r.ok === false) throw new Error(r.error || uiText("ui.ui.kit.window.views.settings.frame.47078ae8ed"));
      if (r && typeof r === "object" && r.mtime_ns != null) seenMtime = String(r.mtime_ns);
      if (revision === container.dataset.settingsRevision) delete container.dataset.settingsDirty;
      let saved = out;
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
          saved = r2.config;
        }
      }).catch(() => {
        // Не перечиталось — срез остаётся прежним; худшее, что даёт эта
        // миллисекундная гонка, — один лишний хвост у расписки.
      });
      if (revision === container.dataset.settingsRevision) built.didSave?.(saved);
      if (out.lang !== undefined && out.lang !== localePreference() && revision === container.dataset.settingsRevision) {
        saveLocale(out.lang ?? '');
        // The confirmed write owns this reload. A newer unsaved form is retained.
        setTimeout(() => { if (!container.dataset.settingsDirty) location.reload(); }, 0);
      }
      phoneSettings.dispatchEvent(new CustomEvent('configuration-saved',{detail:saved}));
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
  conflictBox.innerHTML = uiText("ui.ui.kit.window.views.settings.frame.3de4278a00", {p0: String(mac ? uiText("ui.ui.kit.window.views.settings.frame.c4797349dd") : uiText("ui.ui.kit.window.views.settings.frame.8c06502737"))});
  const conflictRow = el("div", "actions");
  conflictRow.style.marginTop = "10px";
  conflictRow.append(
    button(uiText("ui.ui.kit.window.views.settings.frame.f7cc0e2a0a"), "primary", () => void render(container, edition)),
    button(uiText("ui.ui.kit.window.views.settings.frame.444100de18"), "quiet", async () => {
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
        const restartNote = configureRestart(restartBtn,restartForSettings(before,out,!!cfg.needs_local_setup),restartBlocks);
        saveOut.textContent =
          uiText("ui.ui.kit.window.views.settings.frame.fceeef4237") +
          (relayNote ? ` ${relayNote}.` : "") + restartNote + modeNote;
        // Первый запуск — те же слова, что у «Сохранить»: кнопка ниже зовёт
        // restart_self и поднимает агента впервые (симметрия, ревью 06.10).
        if (cfg.needs_local_setup) saveOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.f50957e168");
        restartBtn.hidden = !restartNote;
        if (cfg.needs_local_setup && !String(out.model?.key || "").trim()) {
          saveOut.className = "receipt err";
          saveOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.d1bf9082ad");
          restartBtn.hidden = true;
        }
      } catch (e) {
        saveOut.className = "receipt err";
        saveOut.textContent = humanError(e).text;
      }
    }),
  );
  conflictBox.append(conflictRow);
  // configureRestart names the exact scope on every save; this is only the initial label.
  const restartBtn = button(uiText("ui.ui.kit.window.views.settings.frame.bfa262aa90"), "quiet", () => {
    // 05.10: кнопка обязана уходить с экрана в момент нажатия — иначе владелец
    // жмёт её снова и снова, не зная, взялся ли перезапуск. Движок выйдет на
    // границе хода и поднимется с новыми настройками; ошибки придут тостом.
    restartBtn.hidden = true;
    saveOut.className = "receipt";
    const scope=restartBtn.dataset.scope || 'engine';
    saveOut.textContent = scope==='application' ? uiText("ui.ui.kit.window.views.settings.frame.e40e49f2ec") : scope==='window'
      ? uiText("ui.ui.kit.window.views.settings.frame.663113d2b7") : uiText("ui.ui.kit.window.views.settings.frame.b6c5d0add8");
    dispatchEvent(new CustomEvent("frame-restart",{detail:{scope}}));
  });
  restartBtn.hidden = true;
  save.append(restartBtn);
  const saveCard = el("section", "card save-bar");
  saveCard.append(save, conflictBox);
  const sourceInfo = el("details", "settings-source");
  sourceInfo.append(el("summary", "", uiText("ui.ui.kit.window.views.settings.frame.682d554ec5")), el("p", "mono", loaded.path));
  center.append(sourceInfo, saveCard);

  if(activityScope()!==settingsScope)return;
  mountSettings(container, center, built.groups || []);
  // The frame attaches this cached page after render() resolves.
  setTimeout(()=>{
    if(!updateWork.isConnected)return;
    let unobserve=()=>{};
    unobserve=observeActivity(activityScope,value=>{
      if(!updateWork.isConnected){unobserve();return;}
      activity=value;renderSettingsWork();
    });
  },0);
}

/**
 * Экран настроек — карточки и оглавление слева. Оглавление собирается из
 * заголовков карточек: ни одной второй копии списка.
 */
function mountSettings(container: HTMLElement, center: HTMLElement, groups: SettingsGroup[] = []) {
  const wrap = el("div", "settings");
  const nav = el("nav", "settings-nav");
  nav.setAttribute("aria-label", uiText("ui.ui.kit.window.views.settings.frame.2d73b4c384"));
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
    // Group navigation precedes its local index in both DOM/focus and visual order.
    // The outer grid owns the full-width tab row; no responsive DOM moves needed.
    wrap.append(tabs);
    let saved = "";
    try {
      saved = localStorage.getItem("settings.group") || "";
    } catch {
      // без хранилища вкладка просто не запомнится
    }
    const start = cfg.needs_subscription_login && live.some(g => g.id === GROUP.brain)
      ? GROUP.brain : live.some((g) => g.id === saved) ? saved : live[0].id;
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
      // Appearance includes a saved interface-language setting as well as
      // immediately applied visual preferences; keep the save action available.
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
        // Measure wrapped tabs at this viewport/DPI; an 8px anchor margin put
        // the section heading underneath their sticky surface.
        const tabs = wrap.querySelector<HTMLElement>(".settings-tabs");
        c.style.scrollMarginTop = `${(tabs?.getBoundingClientRect().height || 0) + 12}px`;
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
  const every = field(uiText("ui.ui.kit.window.views.settings.frame.54f58c5c72"), String(b.every_days ?? 7), (v) => {
    const n = Math.max(0, Math.min(365, Math.floor(Number(v))));
    b.every_days = Number.isFinite(n) ? n : 7;
  }, { hint: uiText("ui.ui.kit.window.views.settings.frame.4bc9ef872d") });
  const keep = field(uiText("ui.ui.kit.window.views.settings.frame.9da9101d3c"), String(b.keep ?? 8), (v) => {
    const n = Math.max(1, Math.min(1000, Math.floor(Number(v))));
    b.keep = Number.isFinite(n) ? n : 8;
  }, { hint: uiText("ui.ui.kit.window.views.settings.frame.4cb0c81212") });
  const dir = field(uiText("ui.ui.kit.window.views.settings.frame.73a0f69d54"), String(b.dir ?? ""), (v) => (b.dir = v.trim()), {
    mono: true,
    placeholder: uiText("ui.ui.kit.window.views.settings.frame.b604b54308"),
    hint: uiText("ui.ui.kit.window.views.settings.frame.3ffb594e94"),
  });
  grid.append(every, ...(electron?.shell === "python" ? [] : [keep]), dir);
  const out = el("span", "receipt");
  const listBox = el("ul", "backup-list");
  let where = "";
  const refresh = () => {
    shell<{ dir: string; items: Array<{ name: string; bytes: number; when: number }> }>("backup_list")
      .then((r) => {
        where = r.dir;
        listBox.replaceChildren();
        const kinds: Record<string, string> = { auto: uiText("ui.ui.kit.window.views.settings.frame.51ec8d5076"), manual: uiText("ui.ui.kit.window.views.settings.frame.730255815d"), before: uiText("ui.ui.kit.window.views.settings.frame.1e33b27e8c") };
        for (const it of r.items.slice(0, 6)) {
          const kind = it.name.includes("-auto") ? kinds.auto : it.name.includes("-manual") ? kinds.manual : it.name.includes("-before-") ? kinds.before : "";
          const when = it.when ? new Date(it.when * 1000).toLocaleString(locale(), { day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" }) : it.name;
          listBox.append(el("li", "", uiText("ui.ui.kit.window.views.settings.frame.95dafacad6", {p0: String(when), p1: String(kind ? " · " + kind : ""), p2: String((it.bytes / 1048576).toFixed(1))})));
        }
        // Период 0 — расписание выключено: обещать «снимется сама» было бы неправдой.
        if (!r.items.length)
          listBox.append(el("li", "muted", (b.every_days ?? 7) === 0
            ? uiText("ui.ui.kit.window.views.settings.frame.4fef4817b7")
            : uiText("ui.ui.kit.window.views.settings.frame.06acdad79c")));
      })
      .catch(() => {
        // Окно без оболочки (браузер, Praxis): копии снимает сервер, здесь смотреть нечего.
        listBox.replaceChildren(el("li", "muted", uiText("ui.ui.kit.window.views.settings.frame.64e5c66bf4")));
      });
  };
  const now = button(uiText("ui.ui.kit.window.views.settings.frame.b2bfec6cf1"), "quiet", async () => {
    now.disabled = true;
    out.className = "receipt";
    out.textContent = uiText("ui.ui.kit.window.views.settings.frame.a97c3726be");
    try {
      const r = await shell<{ path: string; files: number }>("backup_now");
      out.className = "receipt ok";
      out.textContent = uiText("ui.ui.kit.window.views.settings.frame.8306e1de21", {p0: String(r.files), p1: String(r.path)});
      refresh();
    } catch (e) {
      out.className = "receipt err";
      out.textContent = humanError(e).text;
    } finally {
      now.disabled = false;
    }
  });
  const open = button(uiText("ui.ui.kit.window.views.settings.frame.7f58de8f04"), "quiet", () => {
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
      (electron?.shell === "python"
        ? uiText("ui.ui.kit.window.views.settings.frame.e2db7985d8")
        : uiText("ui.ui.kit.window.views.settings.frame.3592296efb")) +
        uiText("ui.ui.kit.window.views.settings.frame.28ced6de7b") +
        uiText("ui.ui.kit.window.views.settings.frame.d6ee4b3b15"),
    ),
  );
  refresh();
  return card(uiText("ui.ui.kit.window.views.settings.frame.0076b35b56"), box);
}

function transferCard(draft: Config, posix = false): HTMLElement {
  const box = el("div");
  if (electron?.shell === 'python' && platformOf(electron) === 'linux') {
    const migrate = button(uiText("ui.ui.kit.window.views.settings.frame.2131646a7a"), 'quiet', () => {
      const dialog = paperDialog(uiText("ui.ui.kit.window.views.settings.frame.9aae280ba1"));
      dialog.body.append(el('p', '', uiText("ui.ui.kit.window.views.settings.frame.b4d49323aa")));
      dialog.footer.append(button(uiText("ui.ui.kit.window.views.settings.frame.9b126710ba"), 'primary', async () => {
        dialog.dialog.close();
        await shell('migration_wizard').catch(e => toast(humanError(e).text));
      }));
    });
    box.append(migrate);
  }
  // Команда обратного импорта — путём питона ЭТОЙ системы (runtime/python.exe
  // против runtime/bin/python3): подсказка, которую копируют в консоль, обязана
  // работать как есть.
  const importCmd = posix
    ? uiText("ui.ui.kit.window.views.settings.frame.0438fd0ca1")
    : uiText("ui.ui.kit.window.views.settings.frame.e801cd084c");
  const exportOut = el("span", "receipt");
  const exportBtn = button(uiText("ui.ui.kit.window.views.settings.frame.a340373cb7"), "quiet", async () => {
    exportBtn.disabled = true;
    exportOut.className = "receipt";
    exportOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.55c5a44b1b");
    try {
      const path = await shell<string>("carry_export");
      exportOut.className = "receipt ok";
      exportOut.textContent = uiText("ui.ui.kit.window.views.settings.frame.e19fc67763", {p0: String(path)});
      toast(uiText("ui.ui.kit.window.views.settings.frame.d3520ce558"));
      await shell("reveal_path", { path }).catch(() => {});
    } catch (e) {
      exportOut.className = "receipt err";
      exportOut.textContent = humanError(e).text;
    } finally { exportBtn.disabled = false; }
  });
  const exportRow = el("div", "actions");
  exportRow.append(exportBtn, exportOut);
  const steps = el("ol", "field-hint");
  const unavailable = el('p','receipt',SERVER_TRANSFER_NOTICE());unavailable.setAttribute('role','status');box.append(unavailable);
  steps.append(el("li", "", uiText("ui.ui.kit.window.views.settings.frame.4624978ffe")),
    el("li", "", uiText("ui.ui.kit.window.views.settings.frame.2d33c98813")));
  box.append(button(uiText("ui.ui.kit.window.views.settings.frame.981be49c36"),"primary",()=>dispatchEvent(new CustomEvent("frame-go",{detail:"learn"}))));
  const details = el("details");
  details.append(el("summary", "field-label", uiText("ui.ui.kit.window.views.settings.frame.709c03fe68")),
    el("p", "field-hint", uiText("ui.ui.kit.window.views.settings.frame.c7ce599cdd")),
    el("p", "field-hint", uiText("ui.ui.kit.window.views.settings.frame.5e8dfa0906", {p0: String(importCmd)})));
  box.append(steps, exportRow, details);

  // --- окно к харнессу на сервере
  const remote = el("div");
  remote.style.marginTop = "12px";
  const remoteToggle = toggle(uiText("ui.ui.kit.window.views.settings.frame.52ae75e074"), draft.mode === "remote", (v) => {
    draft.mode = v ? "remote" : "local";
    syncRemote();
  });
  const baseField = field(uiText("ui.ui.kit.window.views.settings.frame.db17a151fc"), String(draft.base || ""), (v) => (draft.base = v), {
    mono: true,
    placeholder: "https://helene.example.com",
    hint: uiText("ui.ui.kit.window.views.settings.frame.904b7d082c"),
  });
  const keyField = field(uiText("ui.ui.kit.window.views.settings.frame.4a5a0cbeaa"), String(draft.key || ""), (v) => (draft.key = v), {
    type: "password",
    mono: true,
    hint: uiText("ui.ui.kit.window.views.settings.frame.0a56711bbd"),
  });
  const remoteNote = el("p", "field-hint");
  const syncRemote = () => {
    const on = draft.mode === "remote";
    baseField.hidden = !on;
    keyField.hidden = !on;
    remoteNote.textContent = on
      ? uiText("ui.ui.kit.window.views.settings.frame.ec6e09ac37")
      : uiText("ui.ui.kit.window.views.settings.frame.779b741909");
  };
  remote.append(remoteToggle, baseField, keyField, remoteNote);
  syncRemote();
  box.append(remote);
  return card(uiText("ui.ui.kit.window.views.settings.frame.04120307c4"), box, uiText("ui.ui.kit.window.views.settings.frame.d6da472d31"));
}

/**
 * Mini App entry belongs to the configured bot, locally and on a VPS.
 * The origin is diagnostic; users never copy it or enroll through a QR.
 */
interface PhoneState { state: string; message: string; url?: string; telegram?: string; bot_url?: string;
  checks?: Record<string, {state:string;message:string;last_attempt?:number;retry_at?:number}> }

export function phoneCard(draft: Config, savedEnabled: boolean, savedMode: string,
                   remoteBase: string, _qrSvg: (text: string) => Promise<string>, opts:{showToggle?:boolean;onBridge?:(bridge:ServerBridge)=>void}={}): HTMLElement {
  draft.phone = draft.phone || {};
  let savedExternal=draft.phone.external||'',savedBridge=draft.phone.bridge_profile||'';
  if(!['automatic','stable'].includes(draft.phone.mode || ''))draft.phone.mode = "automatic";
  const remote = !!remoteBase;
  const phone = el("div");
  const hint = el("p", "field-hint", uiText("ui.ui.kit.window.views.settings.frame.93de73e123"));
  const receipt = el("p", "receipt");
  receipt.setAttribute("aria-live", "polite");
  const checks = el("div", "phone-checks");
  let status: PhoneState = {state:"waiting",message:uiText("ui.ui.kit.window.views.settings.frame.c2ae8db156")};
  const devicesBox = el("div", "devices");
  const drawDevices = async () => {
    try {
      const rows = await api<Array<{id:string;name:string;created:string}>>("/pair/devices");
      devicesBox.replaceChildren();
      if (!rows.length) return;
      devicesBox.append(el("p", "field-label", uiText("ui.ui.kit.window.views.settings.frame.be1cbefc60")));
      for (const device of rows) {
        const row = el("div", "device-row");
        row.append(el("span", "", `${device.name} · ${new Date(device.created).toLocaleDateString(locale())}`));
        row.append(button(uiText("ui.ui.kit.window.views.settings.frame.7ba56eea85"), "quiet", async () => {
          await post("/pair/revoke", {id:device.id}).catch(e => toast(humanError(e).text));
          void drawDevices();
        }));
        devicesBox.append(row);
      }
    } catch { /* existing paired devices stay revocable through the channel */ }
  };
  const retry = button(uiText("ui.ui.kit.window.views.settings.frame.12b40a98ab"), "quiet", async () => {
    await post("/api/phone/retry", {}).catch(e => toast(humanError(e).text));
    void refresh();
  });
  const botLink = el("a", "btn btn-quiet", uiText("ui.ui.kit.window.views.settings.frame.74d51d1a52"));
  botLink.target = "_blank";
  botLink.rel = "noopener noreferrer";
  botLink.hidden = true;
  function sync() {
    const saved = remote || (savedEnabled && ['automatic','stable'].includes(savedMode) && savedMode===draft.phone?.mode &&
      (savedMode!=='stable'||(savedExternal===(draft.phone?.external||'')&&savedBridge===(draft.phone?.bridge_profile||''))));
    const on = remote || !!draft.phone?.enabled;
    receipt.textContent = !on ? uiText("ui.ui.kit.window.views.settings.frame.6723fcf24b") : !saved
      ? uiText("ui.ui.kit.window.views.settings.frame.3b7a4f138d") : status.message;
    receipt.classList.toggle("err", status.state === "error" || status.state === "retry");
    retry.hidden = !saved || !on;
    const validBot = /^https:\/\/t\.me\/[A-Za-z0-9_]+(?:\?start=helene)?$/.test(status.bot_url || '');
    botLink.hidden = !on || !saved || status.telegram !== "ready" || !validBot;
    if (validBot) botLink.href = status.bot_url!;
    checks.replaceChildren();
    if (on && saved) for (const [key,label] of [['bot',uiText("ui.ui.kit.window.views.settings.frame.33123129a8")],['https',uiText("ui.ui.kit.window.views.settings.frame.b97273e65d")],['menu',uiText("ui.ui.kit.window.views.settings.frame.d6ea7b8001")],['authorization',uiText("ui.ui.kit.window.views.settings.frame.661a9c5f29")]]) {
      const check=status.checks?.[key]; if(!check)continue;
      const row=el('p','receipt');row.classList.toggle('err',['error','retry'].includes(check.state));
      const at=check.last_attempt ? uiText("ui.ui.kit.window.views.settings.frame.b17ca685a7", {p0: String(new Date(check.last_attempt*1000).toLocaleTimeString(locale()))}) : '';
      const retryAt=check.retry_at && check.retry_at*1000>Date.now() ? uiText("ui.ui.kit.window.views.settings.frame.2bca132071", {p0: String(new Date(check.retry_at*1000).toLocaleTimeString(locale()))}) : '';
      row.textContent=`${label}: ${check.message}${at}${retryAt}`;checks.append(row);
    }
  }
  async function refresh() {
    if (!remote && (!savedEnabled || !['automatic','stable'].includes(savedMode))) { sync(); return; }
    try { status = await api<PhoneState>("/api/phone"); }
    catch (e) { const error=humanError(e); status={state:"error",message:error.detail || error.text}; }
    sync();
  }
  const enabled=toggle(uiText("ui.ui.kit.window.views.settings.frame.ecaf7b4d6a"), !!draft.phone.enabled, v => { draft.phone!.enabled=v;sync(); });
  if (!remote && opts.showToggle!==false) phone.append(enabled);
  if(!remote) {
    const route=document.createElement('select');route.setAttribute('aria-label',uiText("ui.ui.kit.window.views.settings.frame.f0589aa2cb"));
    for(const [value,label] of [['automatic',uiText("ui.ui.kit.window.views.settings.frame.07a12aa6ea")],['stable',uiText("ui.ui.kit.window.views.settings.frame.f9705c60af")]]) {
      const option=document.createElement('option');option.value=value;option.textContent=label;route.append(option);
    }
    route.value=draft.phone.mode || 'automatic';
    const profiles=el('select','field-input');profiles.setAttribute('aria-label',uiText("ui.ui.kit.window.views.settings.frame.025baa7c2a"));
    profiles.append(new Option(uiText("ui.ui.kit.window.views.settings.frame.bad301da45"),''));
    const profileNote=el('p','receipt');
    const fixed=field(uiText("ui.ui.kit.window.views.settings.frame.1c8caa8990"),draft.phone.external || '',value=>{draft.phone!.external=value.trim();delete draft.phone!.bridge_profile;profiles.value='';sync()},
      {placeholder:'https://…',hint:uiText("ui.ui.kit.window.views.settings.frame.14cf28cced")});
    const stable=el('div');stable.append(profiles,fixed,profileNote);stable.hidden=route.value!=='stable';
    route.addEventListener('change',()=>{draft.phone!.mode=route.value;if(route.value==='stable')draft.phone!.local_port=draft.phone?.local_port || draft.port;else delete draft.phone!.bridge_profile;stable.hidden=route.value!=='stable';sync()});
    let bridges:ServerBridge[]=[];
    profiles.addEventListener('change',async()=>{
      const selected=bridges.find(value=>value.id===profiles.value);
      if(!selected){delete draft.phone!.bridge_profile;return;}
      profiles.disabled=true;profileNote.textContent=uiText("ui.ui.kit.window.views.settings.frame.e2d027a902");
      try{
        const checked=await shell<{ok:boolean}>('server_bridge',{action:'check',request:JSON.stringify({id:selected.id})});
        if(!checked.ok)throw new UiError(uiText("ui.ui.kit.window.views.settings.frame.19aae8d623"));
        if(opts.onBridge)opts.onBridge(selected);else{useBridge(draft,selected);cardNode.dispatchEvent(new CustomEvent('bridge-selected',{detail:selected}));}
        profileNote.textContent=uiText("ui.ui.kit.window.views.settings.frame.1f37c24b41");
        profiles.dispatchEvent(new Event('input',{bubbles:true}));
      }catch(error){profiles.value=draft.phone?.bridge_profile||'';profileNote.textContent=humanError(error).text;}
      finally{profiles.disabled=false;}
    });
    if(electron?.shell==='python')void shell<{bridges:ServerBridge[]}>('server_bridge',{action:'list'}).then(result=>{
      bridges=result.bridges.filter(value=>value.external&&value.phase!=='error');
      profiles.append(...bridges.map(value=>new Option(value.name+' · '+value.external,value.id)));
      profiles.value=draft.phone?.bridge_profile||'';
    }).catch(error=>{profileNote.textContent=humanError(error).text;});else profiles.hidden=true;
    phone.append(route,stable);
    // Update the existing card: its identity is retained by the settings index.
    queueMicrotask(()=>cardNode.addEventListener('bridge-selected',event=>{
      const bridge=(event as CustomEvent<ServerBridge>).detail;
      if(bridge.external&&!bridges.some(value=>value.id===bridge.id)){
        bridges.push(bridge);profiles.append(new Option(bridge.name+' · '+bridge.external,bridge.id));
      }
      route.value=draft.phone?.mode||'automatic';route.dispatchEvent(new Event('change'));
      fixed.querySelector('input')!.value=draft.phone?.external||'';
      profiles.value=draft.phone?.bridge_profile||'';enabled.setAttribute('aria-checked',String(!!draft.phone?.enabled));sync();
    }));
  }
  phone.append(hint,receipt,checks);
  const actions=el('div','actions');actions.append(botLink,retry);phone.append(actions,devicesBox);
  const cardNode=card('Mini App Telegram',phone);
  cardNode.addEventListener('configuration-saved',event=>{
    const saved=(event as CustomEvent<Config>).detail.phone;
    savedEnabled=!!saved?.enabled;savedMode=saved?.mode||'';
    savedExternal=saved?.external||'';savedBridge=saved?.bridge_profile||'';void refresh();
  });
  void refresh();void drawDevices();
  const poll=()=>{if(!cardNode.isConnected)return;void refresh();setTimeout(poll,2000)};
  setTimeout(poll,2000);
  return cardNode;
}
