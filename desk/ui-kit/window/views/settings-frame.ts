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
// четыре штуки в одном файле. Появились они 09.09 как заплатка (Пульт ругался
// на чужой харнесс пустыми полями и красными строками), и заплаткой же и
// оставались: окно Элен таскало в себе логику Пульта, а Пульт — карточки
// агента, которого рядом нет.
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

export interface Config {
  agent?: { name?: string };
  phone?: { enabled?: boolean };
  update?: { url?: string };
  owner?: { name?: string; room?: string };
  model?: { framework?: string; base_url?: string; model?: string; key?: string; keys?: Record<string, string>; max_tokens?: number; reasoning_effort?: string; fallback_model?: string; vision_model?: string };
  // `instructions` экран не показывает, но обязан сохранить: этой ручкой
  // оболочка гасит 23 КБ чужого системного промпта Codex CLI перед конституцией
  // (shell/src/main.rs, RELAY_INSTRUCTIONS). Раньше блок relay пересобирался
  // заново, и ручка исчезала при первом же «Сохранить».
  relay?: { enabled?: boolean; port?: number; instructions?: string; [k: string]: unknown };
  telegram?: { bot_token?: string; owner_id?: number | string; mode?: string; api_id?: string | number; api_hash?: string; phone?: string };
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
}

/** Что издание даёт каркасу. */
export interface Edition {
  /** Карточки издания. Встают между «Именами» и «Телефоном». */
  cards: HTMLElement[];
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
 * мертва (живой Пульт Праксис, 09.09). Поле в настройках по-прежнему главнее.
 */
export const UPDATE_URL_DEFAULT =
  "https://api.github.com/repos/josephsteuerjr/praxis/releases/latest";

export async function render(container: HTMLElement, edition: EditionFactory): Promise<void> {
  if (!inTauri) {
    const center = el("div", "center");
    // Тумблер телефона в вебе был пустышкой: черновик выбрасывался в мусор,
    // кнопки «Сохранить» в этой ветке нет вовсе — человек щёлкал, и ничего не
    // происходило, и никто не говорил, что не происходит.
    center.append(el("div", "card muted", `Настройки доступны в приложении ${PRODUCT_NAME} на том компьютере, где живёт агент: здесь окно смотрит на удалённый харнесс. Тема — как в системе.`));
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
  const c = loaded.config;
  // Обязательные блоки заводим ЗДЕСЬ и объявляем это типом `Draft`: издание
  // опирается на них полутора сотнями строк карточек, и «наверное, есть» в
  // каждой из них было бы глушением проверки на настоящей гарантии.
  const draft = JSON.parse(JSON.stringify(c)) as Draft;
  draft.agent = draft.agent || {};
  draft.owner = draft.owner || {};
  draft.model = draft.model || {};
  draft.telegram = draft.telegram || {};
  const center = el("div", "center");

  // Издание приносит свои карточки и свою часть записи в конфиг. Всё, что
  // ему нужно спросить у трубы (режим, снимок устройства), оно спрашивает
  // само: каркасу это знать незачем, а Пульту — и подавно.
  const built = await edition({ draft, saved: c, loaded });


  // --- имена
  const names = el("div", "form-grid two");
  names.append(
    field("Имя агента", String(draft.agent.name || ""), (v) => (draft.agent!.name = v)),
    field("Твоё имя", String(draft.owner.name || ""), (v) => (draft.owner!.name = v)),
  );
  // Настройки пишут только helene.json. Конституцию (data/soul/SOUL.md) не
  // переписывает никто, кроме установщика, — а в ней старые имена остаются
  // навсегда, и агент в своём K-слое читает именно их. Обещать обратное нельзя.
  center.append(card("Имена", names, built.namesHint));

  for (const box of built.cards) center.append(box);

  center.append(phoneCard(draft, !!c.phone?.enabled, built.phoneBase, built.qrSvg));

  // --- перенос: экспорт агента одним архивом и окно к харнессу на сервере
  center.append(transferCard(draft));

  // --- автозапуск
  const auto = el("div");
  const autoToggle = toggle("Запускать при входе в Windows", false, async (v) => {
    try {
      await shell("autostart_set", { on: v });
      toast(v ? "Автозапуск включён" : "Автозапуск выключен");
    } catch (e) {
      toast(humanError(e).text);
    }
  });
  shell<boolean>("autostart_get").then((v) => autoToggle.setAttribute("aria-checked", String(v))).catch(() => {});
  auto.append(autoToggle);
  center.append(card("Автозапуск", auto));

  // Тема — только как в системе (слово владельца 07.09): переключателя нет.


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
      // Оболочки нет — окно открыто браузером (Пульт на сервере). Версию
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
  // Скачать и поставить — оболочка (КОНТРАКТ A→B §5: update_download →
  // update_install). Несовпадение суммы оболочка отвергает сама (throw, файл
  // удалён); `sha_ok: null` — сверять было не с чем. Установщик гасит
  // программу — «установщик запущен» говорим ДО вызова. Старая оболочка без
  // этих команд — кнопка честно открывает ссылку на выпуск.
  const dlBtn = button("Скачать и установить", "primary", async () => {
    if (!updUrl) return;
    dlBtn.disabled = true;
    updOut.className = "receipt";
    updOut.textContent = "Скачиваю в «Загрузки»…";
    try {
      const got = await shell<{ path: string; bytes?: number; sha256?: string; sha_ok: boolean | null }>("update_download", { url: updUrl, sha256: updSha });
      const checked = got.sha_ok === true ? "отпечаток сошёлся" : got.sha_ok === null ? "отпечатка в выпуске нет, сверить было не с чем" : "отпечаток проверен";
      updOut.textContent = `Скачано (${checked}). Установщик запущен — программа закроется сама и откроется новой.`;
      await shell("update_install", { path: got.path });
    } catch (e) {
      const text = e instanceof Error ? e.message : String(e ?? "");
      if (/not found|неизвестн|unknown|command/i.test(text)) {
        updOut.textContent = "Эта версия оболочки ещё не умеет ставить обновление сама — открыл страницу выпуска, скачай и запусти helene-setup.exe.";
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
        } else {
          updOut.textContent = `Это последняя версия (${r.current}).`;
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
  about.append(
    aboutRow,
    field("Адрес обновлений", String(draft.update?.url || ""), (v) => (draft.update!.url = v), {
      mono: true,
      placeholder: "https://api.github.com/repos/<владелец>/helene/releases/latest",
      hint: "Адрес выпусков на GitHub (…/releases/latest) или свой JSON с полями version, url и notes. Программа только сообщает о новой версии и даёт ссылку, сама ничего не подменяет.",
    }),
    logsRow,
  );
  center.append(card("О программе", about));

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
      out.phone = keepBlock(out.phone, { enabled: !!draft.phone?.enabled });
      // Местожительство харнесса (карточка «Перенос»): три скаляра. `remote`
      // без адреса — это окно без харнесса, поэтому пустой адрес = `local`.
      const remoteBase = String(draft.base || "").trim();
      const remoteOn = draft.mode === "remote" && !!remoteBase;
      out.mode = remoteOn ? "remote" : "local";
      if (remoteBase) out.base = remoteBase;
      else delete out.base;
      const remoteKey = String(draft.key || "").trim();
      if (remoteKey) out.key = remoteKey;
      else delete out.key;
      out.setup_complete = true;
      try {
        await writeConfig(out);
        saveOut.className = "receipt ok";
        // Под службой перезапуск ОКНА настройки не применит: службу конфиг
        // читает один раз при своём старте. Раньше расписка обещала обратное.
        let svc = "";
        try {
          svc = await shell<string>("service_state");
        } catch {
          // не смогли спросить — говорим общее
        }
        // Хвост расписки — от ИЗДАНИЯ: у Элен это карточка режима (она знает,
        // что осталось сделать — поставить или снять службу), у Пульта его нет
        // вовсе. Расписка не имеет права молчать о незакрытом деле, но и знать
        // про службу каркасу незачем.
        const modeNote = built.note();
        if (svc === "running") {
          saveOut.textContent = "Сохранено. Агента держит служба Windows: чтобы настройки применились, сними и поставь её заново (карточка «Режим» выше)." + modeNote;
          restartBtn.hidden = true;
        } else {
          saveOut.textContent = "Сохранено. Чтобы применить, перезапусти программу." + modeNote;
          restartBtn.hidden = false;
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
  class StaleConfig extends Error {}
  const writeConfig = async (out: Config, force = false) => {
    const args: Record<string, unknown> = { config: JSON.stringify(out) };
    if (seenMtime && !force) args.mtimeNs = seenMtime;
    try {
      const r = await shell<{ ok?: boolean; code?: string; error?: string; mtime_ns?: string | number } | null>("config_save", args);
      // КОНТРАКТ A→B §1: `{ok: false, code: "stale", mtime_ns, error}` — файл
      // менял кто-то ещё, черновик не записан.
      if (r && typeof r === "object" && r.ok === false && r.code === "stale") throw new StaleConfig(r.error || "stale");
      if (r && typeof r === "object" && r.mtime_ns != null) seenMtime = String(r.mtime_ns);
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
    <span>Пока настройки были открыты, helene.json изменил кто-то ещё — установщик, харнесс или ты в Блокноте. Если сохранить как есть, его правка пропадёт.</span></div>`;
  const conflictRow = el("div", "actions");
  conflictRow.style.marginTop = "10px";
  conflictRow.append(
    button("Перечитать (мои правки потеряются)", "primary", () => void render(container, edition)),
    button("Перезаписать своим", "quiet", async () => {
      conflictBox.hidden = true;
      try {
        await writeConfig(JSON.parse(JSON.stringify(draft)), true);
        saveOut.className = "receipt ok";
        saveOut.textContent = "Перезаписано. Чтобы применить, перезапусти программу.";
        restartBtn.hidden = false;
      } catch (e) {
        saveOut.className = "receipt err";
        saveOut.textContent = humanError(e).text;
      }
    }),
  );
  conflictBox.append(conflictRow);
  const restartBtn = button("Перезапустить сейчас", "quiet", () => dispatchEvent(new Event("frame-restart")));
  restartBtn.hidden = true;
  save.append(restartBtn);
  const saveCard = el("section", "card save-bar");
  saveCard.append(save, conflictBox, el("p", "field-hint", `Файл настроек: ${loaded.path}`));
  center.append(saveCard);

  mountSettings(container, center);
}

/**
 * Экран настроек — карточки и оглавление слева. Оглавление собирается из
 * заголовков карточек: ни одной второй копии списка.
 */
function mountSettings(container: HTMLElement, center: HTMLElement) {
  const wrap = el("div", "settings");
  const nav = el("nav", "settings-nav");
  nav.setAttribute("aria-label", "Разделы настроек");
  const body = el("div", "settings-body");
  center.className = "";
  body.append(center);
  const cards = [...center.querySelectorAll<HTMLElement>("section.card")].filter((c) => c.querySelector(":scope > h3"));
  const links: HTMLAnchorElement[] = [];
  cards.forEach((c, i) => {
    const title = c.querySelector(":scope > h3")!.textContent || "";
    c.id = "s-" + i;
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
  wrap.append(nav, body);
  container.replaceChildren(wrap);
  if ("IntersectionObserver" in window && cards.length) {
    const seen = new Map<Element, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target, e.isIntersecting);
        const first = cards.find((c) => seen.get(c));
        if (!first) return;
        for (const l of links) l.setAttribute("aria-current", String(l.getAttribute("href") === "#" + first.id));
      },
      { root: container, rootMargin: "-10% 0px -70% 0px" },
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
function transferCard(draft: Config): HTMLElement {
  const box = el("div");
  const exportOut = el("span", "receipt");
  const exportBtn = button("Экспорт агента", "quiet", async () => {
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
    }
  });
  const exportRow = el("div", "actions");
  exportRow.append(exportBtn, exportOut);
  box.append(
    exportRow,
    el(
      "p",
      "field-hint",
      "Архив — вся папка данных агента (память, конституция, навыки, личный git, вход ChatGPT, сессия Telegram) и helene.json. " +
        "Внутри ключ модели и токены — не для пересылки посторонним; паспорт helene-carry.json в архиве перечисляет их поимённо. " +
        "Не едут: тело руки computer, журналы, ключ окна, стыки смонтированных папок. " +
        "Обратный импорт на этом ПК — из консоли при закрытой программе: runtime\\python.exe app\\localharness\\carry.py import --config helene.json --archive <архив>; прежняя data/ останется рядом как data.before-<штамп>.",
    ),
  );

  // --- окно к харнессу на сервере
  const remote = el("div");
  remote.style.marginTop = "12px";
  const remoteToggle = toggle("Окно ходит к харнессу на сервере", draft.mode === "remote", (v) => {
    draft.mode = v ? "remote" : "local";
    syncRemote();
  });
  const baseField = field("Адрес канала на сервере", String(draft.base || ""), (v) => (draft.base = v), {
    mono: true,
    placeholder: "https://helene.example.com",
    hint: "Тот адрес, по которому Caddy или Tailscale отдаёт канал контейнера (server/docker-compose.yml слушает 127.0.0.1:8094 хоста).",
  });
  const keyField = field("Ключ окна", String(draft.key || ""), (v) => (draft.key = v), {
    type: "password",
    mono: true,
    hint: "Строка ключа печатается при старте контейнера: docker logs helene | head. Тот же ключ — у телефона.",
  });
  const remoteNote = el("p", "field-hint");
  const syncRemote = () => {
    const on = draft.mode === "remote";
    baseField.hidden = !on;
    keyField.hidden = !on;
    remoteNote.textContent = on
      ? "После сохранения и перезапуска оболочка своих детей не поднимает: агент живёт на сервере, окно и телефон ходят туда. Пустой адрес — это снова local."
      : "Сейчас агент живёт на этой машине: канал и харнесс поднимает окно. Перенос на сервер — экспорт выше, затем server/README-СЕРВЕР.md в поставке.";
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
function phoneCard(draft: Config, savedEnabled: boolean, remoteBase: string,
                   qrSvg: (text: string) => Promise<string>): HTMLElement {
  draft.phone = draft.phone || {};
  const remote = !!remoteBase;
  const phone = el("div");
  const phoneToggle = toggle("Разрешить подключение телефона по сети", !!draft.phone.enabled, (v) => {
    draft.phone!.enabled = v;
    syncPhone();
  });
  // Честно про шифрование: соединение идёт открытым текстом по http://, и в
  // общей Wi-Fi (кафе, отель, коворкинг) ключ устройства и вся переписка с
  // агентом видны соседям. Прежняя подсказка обещала «доступ только по ключу»
  // и про отсутствие шифрования молчала.
  const phoneHint = el("p", "field-hint", remote
    ? `Телефон подключается к серверу: QR ведёт на ${remoteBase}. Слушает ли канал сеть и как он закрыт снаружи — ` +
      "решает сам сервер (Caddy, Tailscale), поэтому тумблера здесь нет. Ключ телефона живёт в его браузере; " +
      "отвязать устройство можно ниже."
    : "Канал начнёт слушать сеть, а не только эту машину. Внимание: соединение НЕ шифруется (обычный http). В чужой или общей Wi-Fi — кафе, отель, коворкинг — ключ телефона и переписка с агентом идут открытым текстом, их видно соседям по сети. Дома в своей сети это приемлемо; в любой другой пользуйся Tailscale: поставь его на компьютер и телефон, войди в один аккаунт, и QR даст его адрес. Включение применяется перезапуском.");
  const qrRow = el("div", "actions");
  qrRow.style.marginTop = "12px";
  const qrWhy = el("span", "receipt");
  const qrOut = el("div", "qr-out");
  qrOut.hidden = true;
  const devicesBox = el("div", "devices");
  const drawDevices = async () => {
    let rows: Array<{ id: string; name: string; created: string }> = [];
    try {
      rows = await api("/pair/devices");
    } catch {
      rows = [];
    }
    devicesBox.replaceChildren();
    if (!rows.length) return;
    devicesBox.append(el("p", "field-label", "Подключённые устройства"));
    for (const d of rows) {
      const row = el("div", "device-row");
      row.append(el("span", "", `${d.name} · ${new Date(d.created).toLocaleDateString("ru-RU")}`));
      row.append(button("Отвязать", "quiet", async () => {
        await post("/pair/revoke", { id: d.id }).catch((e) => toast(humanError(e).text));
        void drawDevices();
      }));
      devicesBox.append(row);
    }
  };
  const qrBtn = button("Показать QR", "quiet", async () => {
    try {
      const pair = await post<{ path: string; expires_in: number; uses: number }>("/pair/new", {});
      if (remote) {
        // Адрес один и он известен: тот, по которому это окно и само ходит.
        // Ни LAN, ни Tailscale этой машины к серверному каналу отношения не имеют.
        const link = remoteBase.replace(/\/+$/, "") + pair.path;
        const svg = await qrSvg(link);
        qrOut.hidden = false;
        qrOut.innerHTML = `<div class="qr-pair"><div class="qr">${svg}</div>
          <div class="qr-text"><p><b>Телефон пойдёт на сервер.</b></p>
            <p class="mono qr-url">${esc(link)}</p></div></div>` +
          `<div class="qr-text">
            <p>Открой камеру телефона и наведи на код. Ссылка живёт десять минут и годится дважды.</p>
            <p><b>iPhone:</b> страница откроется в Safari; нажми «Поделиться» → «На экран „Домой“». Второе открытие из значка допишет ключ, поэтому код и двухразовый.</p>
          </div>`;
        void drawDevices();
        return;
      }
      const port = new URL(cfg.base || "http://127.0.0.1:8094").port || "8094";
      // Оба адреса, а не выбор за владельца: Tailscale мог быть запущен для
      // других дел, а телефон в тайлнет не добавлен — тогда QR со 100.x.y.z
      // ведёт туда, куда телефон не дойдёт, и локальный адрес не предлагался
      // никогда.
      const addrs: Array<{ host: string; note: string }> = [];
      if (inTauri) {
        const ts = await shell<string | null>("tailscale_ip").catch(() => null);
        const lan = await shell<string | null>("lan_ip").catch(() => null);
        if (lan) addrs.push({ host: lan + ":" + port, note: "В этой Wi-Fi: телефон должен быть в той же сети." });
        if (ts) addrs.push({ host: ts + ":" + port, note: "Через Tailscale: телефон с Tailscale в том же аккаунте достучится из любой сети." });
      }
      if (!addrs.length) addrs.push({ host: location.host, note: "" });
      const blocks: string[] = [];
      for (const a of addrs) {
        const link = "http://" + a.host + pair.path;
        // Белый фон модулей задан явно: карточка .qr и так белая, но так код
        // остаётся читаемым камерой, даже если карточку когда-нибудь затемнят.
        const svg = await qrSvg(link);
        blocks.push(`<div class="qr-pair"><div class="qr">${svg}</div>
          <div class="qr-text">${a.note ? `<p><b>${esc(a.note)}</b></p>` : ""}
            <p class="mono qr-url">${esc(link)}</p></div></div>`);
      }
      qrOut.hidden = false;
      qrOut.innerHTML = blocks.join("") +
        `<div class="qr-text">
          <p>Открой камеру телефона и наведи на код. Ссылка живёт десять минут и годится дважды.</p>
          <p><b>iPhone:</b> страница откроется в Safari; нажми «Поделиться» → «На экран „Домой“». Второе открытие из значка допишет ключ, поэтому код и двухразовый.</p>
        </div>`;
      if (inTauri) {
        const fw = await shell<string>("firewall_allow", { port: Number(port) }).catch((e) => humanError(e).text);
        toast(fw);
      }
    } catch (e) {
      qrOut.hidden = false;
      qrOut.innerHTML = failHTML(e, { retry: false });
      bindFail(qrOut);
    }
  });
  // Кнопка не смотрела ни на тумблер, ни на то, был ли перезапуск: труба всё
  // ещё слушала 127.0.0.1, владелец получал красивый QR на адрес, где никто не
  // отвечает, а телефон обвинял в этом Wi-Fi.
  const syncPhone = () => {
    if (remote) {
      // Сервер уже слушает сеть — иначе это окно к нему не ходило бы.
      qrBtn.disabled = false;
      qrWhy.textContent = "";
      return;
    }
    const on = !!draft.phone?.enabled;
    qrBtn.disabled = !on || !savedEnabled;
    qrWhy.className = "receipt";
    qrWhy.textContent = !on
      ? "Включи тумблер, сохрани и перезапусти — тогда канал начнёт слушать сеть."
      : !savedEnabled
        ? "Сохрани и перезапусти программу: пока канал слушает только эту машину, и QR вёл бы туда, где никто не отвечает."
        : "";
  };
  qrRow.append(qrBtn, qrWhy);
  syncPhone();
  if (remote) phone.append(phoneHint, qrRow, qrOut, devicesBox);
  else phone.append(phoneToggle, phoneHint, qrRow, qrOut, devicesBox);
  void drawDevices();
  return card("Телефон", phone);

}
