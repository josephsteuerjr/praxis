// Ограда рук и служба Windows — ДВА НЕЗАВИСИМЫХ ВОПРОСА, а не один список.
//
// ⚠⚠ ГЛАВНОЕ ИСПРАВЛЕНИЕ ЭТОГО ФАЙЛА. Первая редакция знала три режима одним
// списком (`sandbox | interactive | service`), и из этого рос P0: у владельца,
// поставившего службу И песочницу, режим выводился как `service`, а `service`
// означал «ограды нет» — песочница снималась МОЛЧА. Здесь измерения разведены:
//
//   1. РЕЖИМ — про ограду рук: `sandbox` (агент заперт в своей папке, наружу
//      только смонтированное) или `interactive` (файлы и процессы с правами
//      владельца, админ — по запросу окном Windows). Больше значений нет.
//   2. СЛУЖБА — опция ПОВЕРХ любой из оград: ставится один раз под
//      администратором, даёт защиту папки установки, жизнь без окна и брокера
//      прав. Ограду она не снимает и не включает. Внутри опции две РАЗНЫЕ
//      галочки: `service.session0` (доступ агента к правам системы, выключено)
//      и `service.firewall` (правило брандмауэра ставит служба, включено).
//
// Поэтому в `agent_mode` НИКОГДА не пишется "service": туда едет только ограда.
//
// ⚠⚠ ЭТОТ ФАЙЛ НИЧЕГО НЕ ВЫВОДИТ САМ. Названия, описания, список выборов и все
// предупреждения приходят готовыми из `/api/mode`
// (localharness/modes.py → deskd/readers.py::mode_state). Своих описаний здесь
// нет намеренно: расхождение текстов означало бы, что владелец выбирает одно, а
// получает другое. Единственное исключение — оговорка нулевой сессии, см.
// SESSION0_WARNING_FALLBACK ниже, и там объяснено, почему копия вообще нужна.
//
// ⚠ Ключ режима — `agent_mode`, а НЕ `mode`. `mode` в helene.json уже занят и
// означает другое: где живёт харнесс, "local" | "remote". Его читают
// shell/src/main.rs (иначе окно не поднимет ни трубу, ни руннер) и
// svc/src/main.rs (иначе служба откажется стартовать). Записать режим туда =
// выключить продукт.
//
// `sandbox.enabled` режим ВЫСТАВЛЯЕТ (в песочнице true, иначе false) — та же
// раскладка, что делает `modes.apply` перед `fence.install`. Отдельной ручки
// «Песочница для рук» в Настройках нет: две ручки на одну ограду — это две
// правды, и побеждала бы то одна, то другая.
import { api } from "./api";

/** Ключ режима в helene.json. Не трогать `mode` — см. шапку файла. */
export const MODE_KEY = "agent_mode";

/**
 * Имя, под которым служба ходила третьим режимом в первой редакции.
 *
 * Оно ещё лежит в конфигах, поставленных первой волной, и старый харнесс рядом
 * с новым окном может прислать его третьим пунктом `choices`. Оградой оно не
 * является ни в каком виде: карточка выбрасывает такой пункт из списка (см.
 * `fences`), а в `agent_mode` его не пишет никто и никогда.
 */
export const LEGACY_SERVICE = "service";

/** Одна ограда рук: карточка выбора из `modes.catalogue()`. */
export interface ModeChoice {
  name: string;
  title: string;
  text: string;
  needs_admin: boolean;
  sandbox: boolean;
}

/** Галочка внутри опции службы: `modes.service_option().toggles[]`. */
export interface ServiceToggle {
  /** Ключ в helene.json: `service.session0` | `service.firewall`. */
  key: string;
  title: string;
  text: string;
  /** Оговорка, которую владелец обязан прочитать ДО включения. Может быть пуста. */
  warning: string;
  default: boolean;
}

/** Опция службы целиком: `modes.service_option()`. Не режим и не выбор ограды. */
export interface ServiceOption {
  name: string;
  title: string;
  text: string;
  needs_admin: boolean;
  toggles: ServiceToggle[];
}

/** Одно из четырёх прав руки `computer`: `modes.computer_option().scopes[]`. */
export interface ComputerScope {
  /** `computer.read` | `computer.files` | `computer.process` | `computer.apps`. */
  key: string;
  title: string;
  text: string;
}

/** Опция «Управление компьютером» целиком: `modes.computer_option()`. Поверх
 *  любого режима, ни одну ограду не снимает — тело живёт снаружи неё. */
export interface ComputerOption {
  name: string;
  title: string;
  text: string;
  /** Оговорка, которую владелец читает ДО включения. */
  warning: string;
  default: boolean;
  needs_admin: boolean;
  scopes: ComputerScope[];
}

/** Что записано владельцем в `computer` (modes.computer_state). */
export interface ComputerState {
  enabled: boolean;
  scopes: string[];
  port: number;
  explicit: boolean;
}

/** Снимок харнесса о теле — `memory/.state/body.json` (localharness/body.py).
 *  Только то, что он прислал; пустой объект — снимка ещё нет. */
export interface ComputerLive {
  enabled?: boolean;
  available?: boolean;
  reason?: string;
  port?: number;
  device?: string;
  scopes?: string[];
  bridge_pid?: number;
  body_pid?: number;
  /** true — тело ответило через мост; false — спросили, не ответило; null — не спрашивали. */
  connected?: boolean | null;
  identity?: { kind?: string; session_id?: number | null; integrity?: string; elevated?: boolean };
  checked_at?: string;
  logs?: string[];
  /** macOS: система агента по слову тела (`desktop.status.platform`). */
  platform?: string;
  /** macOS: два разрешения системы (TCC) по слову тела — «Запись экрана» и
   *  «Универсальный доступ». null или нет поля — не спрашивали, и окно строк
   *  про них не рисует: «не спрашивали» и «нет» — разные ответы. */
  tcc?: { screen_recording?: boolean; accessibility?: boolean } | null;
  /** macOS: слова тела о том, какого разрешения нет и куда за ним идти. */
  hints?: string[];
}

/**
 * Ответ `/api/mode` (он же блок `mode` в `/api/state` и в анатомии).
 *
 * Форма ПЛОСКАЯ и намеренно держит два ответа рядом: `name`/`sandbox` — какая
 * ограда, `service_installed`/`session0`/`firewall` — что со службой. Склеивать
 * их обратно на экране нельзя: из этой склейки и вырос P0.
 */
export interface ModeState {
  name: string;
  title: string;
  text: string;
  sandbox: boolean;
  explicit: boolean;
  source: string;
  /** true | false | null — «спросить не у кого» (SCM не ответил). */
  service_installed: boolean | null;
  service_title: string;
  service_text: string;
  /** Действующая нулевая сессия: без службы всегда false. */
  session0: boolean;
  /** Как галочка ЗАПИСАНА в файле — по ней и надо писать обратно. */
  session0_set: boolean;
  session0_warning: string;
  firewall: boolean;
  firewall_set: boolean;
  /** В ключе режима лежит старое "service" — первая редакция. */
  legacy_service: boolean;
  notes: string[];
  choices: ModeChoice[];
  service: ServiceOption | null;
  /** Управление компьютером — опция поверх режима (06.09). Старый харнесс
   *  не присылает ни одного из трёх полей; окно говорит об этом словами. */
  computer?: ComputerState | null;
  computer_option?: ComputerOption | null;
  computer_live?: ComputerLive;
  /** Живые просьбы агента о папках (КОНТРАКТ A→B §2): `memory/.state/mounts.json`
   *  как он лежит сейчас, а не снимок анатомии со старта. Старый харнесс поля не шлёт. */
  mounts_live?: { updated_at: string | null; requests: Array<{ path?: string; real?: string; access?: string; why?: string; at?: string; asked?: number }> };
  config: string;
}

/**
 * Режим по трубе. Ходит по тому же каналу, что и остальные ручки окна:
 * `/api/mode` заведена И в HTTP, И в диспетчере трубы (deskapp.py), поэтому в
 * окне она работает.
 */
export async function loadMode(): Promise<ModeState> {
  return api<ModeState>("/api/mode");
}

/**
 * Оговорка нулевой сессии — КОПИЯ `modes.SESSION0_WARNING`.
 *
 * Копия нужна вот почему: поле `session0_warning` труба отдаёт только когда
 * галочка УЖЕ включена (`modes.resolve`: пусто, если session0 выключена), а
 * прочитать оговорку владелец должен ДО того, как включит. С 04.09 тот же текст
 * приезжает всегда — в `service.toggles[].warning`, — и живой текст здесь
 * всегда побеждает: к этой строке падаем только тогда, когда харнесс старее
 * окна и опции службы не прислал вовсе. Разойтись им не даёт тест
 * app/test/mode-key.test.mjs: он сверяет строку с питоном побайтно.
 */
export const SESSION0_WARNING_FALLBACK =
  "Нулевая сессия разрешена: агент получает права системы и при этом не видит " +
  "рабочего стола. Слабая модель может не понять, что делает.";

// Обёртки DOM — общие, из ui-kit/dom.ts (одна копия на все интерфейсы).

/** Галочки службы, как они ЛЕЖАТ В ФАЙЛЕ. Труба отдаёт действующие — они врут вне службы. */
export interface StoredService {
  session0: boolean;
  firewall: boolean;
}
