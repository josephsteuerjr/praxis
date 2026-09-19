// Карточка «Режим» — только для окна Hélène: выбор ограды и опция службы правятся
// там, где живёт харнесс. Пульт этой карточки не рисует вовсе (агент на сервере),
// а ЧИТАЕТ режим он тем же `ui-kit/window/mode.ts`, что и экран «Система».
//
// Разрез прошёл здесь 10.09, при разделении окна на два приложения: выше — типы и
// чтение `/api/mode`, общие обоим; ниже — карточка, которая пишет helene.json
// рядом с собой, и на чужом харнессе означала бы правку не того конфига.
import { el, humanError, toast } from "../../ui-kit/window/lib";
import { button as btn, toggle as switchRow } from "../../ui-kit/dom";
import { shell } from "../../ui-kit/window/api";
import { LEGACY_SERVICE, SESSION0_WARNING_FALLBACK,
         type ModeChoice, type ModeState, type StoredService } from "../../ui-kit/window/mode";

/**
 * Может ли эта учётная запись поднять права администратора.
 *
 * Ручка `admin_state` живёт в оболочке (shell/src/main.rs) и отвечает
 * `{ admin, can_elevate, elevation }`. Её может не быть — окно старее оболочки
 * или запущено вне Tauri, — и тогда ответ честный «не знаю»: опцию службы не
 * запираем, но и не обещаем, что она пройдёт. Врать в обе стороны нельзя:
 * «нет прав» при неизвестности отняло бы у владельца службу на ровном месте.
 *
 * ⚠ `elevation: "unknown"` — это тоже «не спросили», а не «нельзя»: оболочка
 * ставит его, когда сам Windows не ответил про токен (`token_elevation_type`
 * вернул 0). Прочитать его как «прав нет» значило бы запереть службу по нашей
 * же слепоте. «Нельзя» говорит только `elevation: "default"` — учётная запись
 * без администраторского токена вовсе.
 */
async function adminProbe(): Promise<{ known: boolean; canElevate: boolean }> {
  try {
    const r = await shell<{ admin?: boolean; can_elevate?: boolean; elevation?: string }>("admin_state");
    if (!r || (r.admin === undefined && r.can_elevate === undefined)) return { known: false, canElevate: false };
    const canElevate = !!r.admin || !!r.can_elevate;
    if (!canElevate && r.elevation !== undefined && r.elevation !== "default") {
      return { known: false, canElevate: false };
    }
    return { known: true, canElevate };
  } catch {
    // Команды нет (или окно не в оболочке) — это «не спросили», а не «нельзя».
    return { known: false, canElevate: false };
  }
}

export interface ModeCard {
  /** Готовая карточка для экрана Настроек. */
  el: HTMLElement;
  /** Выбранная ОГРАДА: "sandbox" | "interactive". "" — режим не прочитан, писать нечего. */
  name(): string;
  /** Название выбранной ограды словами харнесса. "" — нет ответа трубы. */
  title(): string;
  /** Какой должна быть `sandbox.enabled` при этой ограде. */
  sandbox(): boolean;
  /** `service.session0`, как её надо записать. */
  session0(): boolean;
  /** `service.firewall`, как её надо записать. */
  firewall(): boolean;
  /** Хвост для расписки «Сохранить»: что ещё осталось сделать. Может быть пуст. */
  note(): string;
}

/**
 * Карточка «Режим»: выбор ограды плюс отдельной секцией опция службы.
 *
 * @param live    ответ `/api/mode`; null — труба не ответила
 * @param failure отказ трубы, если он был (показываем словами, а не молчим)
 * @param stored  галочки службы, КАК ОНИ ЛЕЖАТ В ФАЙЛЕ: труба отдаёт
 *        действующие, а без службы `session0` всегда false — писать по ней
 *        значило бы молча стирать выбор владельца
 * @param onPick  зовётся при смене ограды: соседним карточкам (песочница,
 *        монтирование) надо обновить свои строки состояния
 * @param mac     агент живёт на macOS: служба там есть с 0.8.0, но это демон
 *        launchd, а не служба Windows — секция рисуется словами харнесса, без
 *        UAC и брандмауэра, а галочек у неё нет вовсе (`service.toggles` пуст:
 *        нулевая сессия и правило брандмауэра — механизмы Windows). Галочки
 *        службы в файле при этом не трогаем: они уедут обратно такими, какие
 *        лежали.
 */
export function modeCard(
  live: ModeState | null,
  failure: unknown,
  stored: StoredService,
  onPick: (name: string, sandbox: boolean, title: string) => void,
  mac = false,
): ModeCard {
  const box = el("section", "card");
  box.append(el("h3", "", "Режим"));

  // --- ограды, которые прислала труба.
  //
  // Старое "service" выбрасываем: служба — не ограда, и старый харнесс, который
  // всё ещё присылает её третьим пунктом, не должен уводить окно в запись
  // "service" в `agent_mode`. Ровно эта запись и снимала песочницу молча.
  const fences = (live?.choices || []).filter((c) => c && c.name && c.name !== LEGACY_SERVICE);

  // --- труба не ответила, режим не прочитался или выбирать не из чего
  //
  // Пустой список — это старый харнесс рядом с новым окном: свои ограды на
  // такой случай окно НЕ придумывает (описания разошлись бы с теми, по которым
  // владелец выбирал в установщике), а честно говорит, что выбора нет.
  if (!live || !live.name || !fences.length) {
    const why = failure
      ? humanError(failure).text
      : !live || !live.name
        ? live?.text || "Режим не прочитан."
        : "Канал ответил, но списка режимов не прислал — выбирать не из чего. Похоже, код агента старее окна.";
    box.append(el("p", "receipt err", why));
    box.append(
      el(
        "p",
        "field-hint",
        "Пока режим не прочитан, менять его отсюда нельзя: программа не знает, из чего переключает. " +
          "Открой раздел заново, когда агент ответит; что записано в файле — видно на экране «Система».",
      ),
    );
    for (const n of live?.notes || []) box.append(el("p", "receipt err", n));
    return {
      el: box,
      name: () => "",
      title: () => "",
      sandbox: () => false,
      session0: () => stored.session0,
      firewall: () => stored.firewall,
      note: () => "",
    };
  }

  const choiceOf = (name: string): ModeChoice | undefined => fences.find((c) => c.name === name);

  // ⚠ Старый харнесс рядом с новым окном отвечает СТАРОЙ картиной: режимом он
  // называет службу и вместе с ней присылает `sandbox: false` — ту самую
  // склейку, из которой вырос P0. Верить этому флагу нельзя: он означает не
  // «ограды нет», а «спросили не про то».
  const legacyPipe = live.name === LEGACY_SERVICE;
  // Начальный выбор. Обычно это то, что ответила труба; при старой картине —
  // ПЕСОЧНИЦА, а не выведенное из вранья «интерактивный». Ошибиться здесь можно
  // в две стороны, и они не равны: лишняя ограда чинится одним щелчком, а
  // молча снятая — это и есть та беда, ради которой всё переписано. То же
  // правило и у харнесса: без следов ограды `modes.infer` выбирает песочницу.
  let picked = choiceOf(live.name)?.name
    || (legacyPipe
      ? choiceOf("sandbox")?.name || fences[0].name
      : fences.find((c) => c.sandbox === !!live.sandbox)?.name || fences[0].name);
  let session0 = stored.session0;
  let firewall = stored.firewall;
  // Стоит ли служба. Пришло от трубы (SCM на Windows, файл демона на macOS),
  // но живой ответ оболочки свежее: после «Поставить»/«Снять» он меняется, а
  // ответ трубы остаётся с загрузки.
  let installed: boolean | null = live.service_installed;
  let admin = { known: false, canElevate: false };

  const now = el("p", "receipt");
  const pickRow = el("div", "choice stack");
  pickRow.setAttribute("role", "radiogroup");
  const planBox = el("div", "mode-block");
  const svcBox = el("section", "mode-service");
  const svcRow = el("div", "actions");
  const svcOut = el("span", "receipt");
  const svcAdmin = el("p", "field-hint");
  // ⚠ Кто держит движок ПРЯМО СЕЙЧАС — словами. Мина истории продукта: второй
  // экземпляр молча приклеивался к чужому движку, и владелец не знал, чьего
  // агента видит. Под живой службой окно — клиент: своих процессов оно не
  // поднимает, и сказать об этом обязано здесь, а не только в журнале.
  const svcClient = el("p", "field-hint");
  const togglesBox = el("div", "mode-block");

  const syncNow = () => {
    const svc = installed === null ? "спросить не удалось" : installed ? "установлена" : "не установлена";
    // Хвост про службу — только там, где служба бывает, и её собственным
    // именем: «Служба Windows» на Mac было бы словом не про эту машину.
    const svcTail = ` ${mac ? "Служба" : "Служба Windows"}: ${svc}.`;
    if (legacyPipe) {
      // Врать «Сейчас: Служба» нельзя: службы-режима не существует, а какая
      // ограда стоит на самом деле, этот харнесс не сказал.
      now.textContent = `Сейчас: ограда не названа — код агента отвечает старой картиной, где режимом считалась служба.${svcTail}`;
      return;
    }
    const src = live.explicit ? live.source : `записи в файле ещё нет, ограда выведена — ${live.source}`;
    now.textContent = `Сейчас: ${live.title}.${svcTail} Источник: ${src}.`;
  };

  const syncPick = () => {
    for (const b of pickRow.querySelectorAll<HTMLButtonElement>(".choice-item")) {
      b.setAttribute("aria-checked", String(b.dataset.value === picked));
    }
    syncPlan();
  };

  // --- две ограды: названия и описания ЦЕЛИКОМ из `modes.catalogue()`.
  // Запирать здесь нечего: ни песочница, ни интерактивный прав администратора
  // не требуют (`needs_admin: false` у обеих) — админ нужен только службе, и
  // спрашивают его там, в её секции.
  for (const c of fences) {
    const b = el("button", "choice-item");
    b.type = "button";
    b.setAttribute("role", "radio");
    b.dataset.value = c.name;
    b.append(el("span", "choice-title", c.title), el("span", "choice-text", c.text));
    b.addEventListener("click", () => {
      picked = c.name;
      syncPick();
      onPick(picked, c.sandbox, c.title);
    });
    pickRow.append(b);
  }

  // --- что ещё надо сделать, чтобы выбор не остался на бумаге
  const syncPlan = () => {
    planBox.replaceChildren();
    if (legacyPipe) {
      planBox.append(
        el(
          "p",
          "receipt err",
          "Этот код агента ещё считает службу режимом — старая картина, в которой ограда и служба были одним списком. " +
            "Какая ограда стоит на самом деле, он не сказал, поэтому выбрана песочница: лишнюю ограду ты снимешь щелчком, " +
            "а снятая молча — это то, ради чего всё и переделано. Проверь выбор и сохрани; служба от этого не тронется.",
        ),
      );
      return;
    }
    if (picked === live.name) {
      // Предупреждения о текущем состоянии берём готовыми у харнесса: там уже
      // написано человеческими словами и «sandbox.enabled разошлась с режимом»,
      // и «session0 включена, а службы нет».
      for (const n of live.notes) planBox.append(el("p", "receipt err", n));
      return;
    }
    planBox.append(el("p", "receipt", "Ограда сменится после сохранения и перезапуска программы."));
    if (installed) {
      planBox.append(
        el(
          "p",
          "field-hint",
          `${mac ? "Служба" : "Служба Windows"} останется на месте: она не режим, ограду не снимает и не включает. ` +
            "Снимать её ради смены ограды не нужно — но настройки она читает при своём старте, " +
            "поэтому после сохранения перезапусти её (кнопки ниже).",
        ),
      );
    }
  };

  // --- служба: отдельная секция, а не третья карточка выбора.
  //
  // Тексты — из `live.service` (modes.service_option). Если их нет, харнесс
  // старее окна: своих не пишем, говорим об этом прямо и галочки не трогаем —
  // они уедут в файл ровно такими, какими лежали.
  const option = live.service;
  svcBox.append(el("h4", "", option?.title || live.service_title || (mac ? "Служба" : "Служба Windows")));
  const svcText = option?.text || live.service_text || "";
  if (svcText) svcBox.append(el("p", "choice-text", svcText));
  else {
    // Запасной текст — ровно про ОТСУТСТВИЕ описания, и ни про что больше.
    // ⚠ Этот блок однажды уже разъехался: между `if (svcText)` и его `else`
    // вставили оговорку, `else` прилип к ней — и на Windows, где оговорки нет
    // по построению, окно на каждом открытии врало «код агента старее окна».
    // Поэтому здесь отдельный блок, а не `else` от соседнего условия.
    svcBox.append(
      el(
        "p",
        "receipt err",
        "Про службу код агента ничего не рассказал — похоже, он старее окна. Ставить и снимать её кнопками " +
          "ниже можно, а тумблеры службы окно оставит в файле такими, какие они есть.",
      ),
    );
  }
  // Чего служба НЕ даёт — рядом с тем, что даёт, и ДО кнопки. На macOS это
  // окна и экран: их у процесса вне сеанса нет.
  const svcWarn = option?.warning || live.service_warning || "";
  if (svcWarn) svcBox.append(el("p", "receipt err", svcWarn));

  /** Заперта ли установка службы и почему. Пустая строка — можно ставить. */
  const lockedWhy = (): string => {
    if (installed === true) return ""; // уже стоит — ставить нечего
    // На macOS пробы прав нет и быть не может: администратором здесь становятся
    // вводом пароля в системном диалоге, а не членством в группе, проверенным
    // заранее. Запирать кнопку по нашей слепоте — отнимать выбор.
    if (mac) return "";
    if (admin.known && !admin.canElevate) {
      return "Служба недоступна: у этой учётной записи нет прав администратора. Служба ставится один раз — " +
        "попроси того, кто хозяин компьютера, или войди под его учётной записью. " +
        "Ограда при этом работает как выбрана: службе она не подчиняется.";
    }
    return "";
  };

  const svcRefresh = async () => {
    try {
      const st = await shell<string>("service_state");
      svcOut.className = "receipt " + (st === "running" ? "ok" : st === "unknown" ? "err" : "");
      svcOut.textContent =
        st === "running"
          ? "Служба работает"
          : st === "stopped"
            ? "Служба поставлена, но не запущена"
            : st === "unknown"
              // Четвёртый ответ, а не третий: файл службы на месте, а спросить
              // систему о её состоянии не вышло. Назвать это «Службы нет» —
              // соврать владельцу про его же машину.
              ? "Служба поставлена; спросить систему о её состоянии не вышло"
              : "Службы нет";
      svcClient.textContent =
        st === "running"
          ? "Движок и канал держит служба — это окно работает клиентом: своих процессов оно не " +
            "поднимает, и «Перезапустить» их не тронет." +
            // Кто поднимает тело под службой — разное на разных системах:
            // на macOS это окно (TCC живёт у Helene.app), на Windows тело
            // поднимает сам движок в интерактивной половине (`session-host`).
            (mac ? " Тело тула `computer` поднимает окно, пока оно открыто." : "")
          : "";
      // Ответ оболочки свежее ответа трубы: пересобираем всё, что от него зависит.
      installed = st !== "absent";
      installBtn.hidden = st !== "absent";
      removeBtn.hidden = st === "absent";
      syncNow();
      syncPlan();
      syncAdmin();
      syncToggles();
    } catch (e) {
      svcOut.textContent = humanError(e).text;
    }
  };
  const afterService = () => {
    let tries = 0;
    const poll = window.setInterval(() => {
      void svcRefresh();
      if (++tries > 10) clearInterval(poll);
    }, 2500);
  };
  const installBtn = btn("Поставить службу", "quiet", async () => {
    const why = lockedWhy();
    if (why) {
      toast(why);
      return;
    }
    try {
      toast(await shell<string>("install_service"));
      afterService();
    } catch (e) {
      toast(humanError(e).text);
    }
  });
  const removeBtn = btn("Снять службу", "quiet", async () => {
    try {
      toast(await shell<string>("remove_service"));
      afterService();
    } catch (e) {
      toast(humanError(e).text);
    }
  });
  svcRow.append(installBtn, removeBtn, svcOut);

  // Строка про права: три разных ответа, и ни один из них не «наверное».
  const syncAdmin = () => {
    const why = lockedWhy();
    installBtn.setAttribute("aria-disabled", String(!!why));
    if (installed === true) {
      svcAdmin.className = "field-hint";
      svcAdmin.textContent = mac
        ? "Служба уже стоит. Снять её можно кнопкой выше — система спросит пароль администратора."
        : "Служба уже стоит. Снять её можно кнопкой выше — Windows спросит права администратора.";
      return;
    }
    if (mac) {
      svcAdmin.className = "field-hint";
      svcAdmin.textContent =
        "Система спросит пароль администратора: положить описание службы в /Library/LaunchDaemons " +
        "может только он. Больше ничего под этими правами не делается.";
      return;
    }
    if (why) {
      svcAdmin.className = "receipt err";
      svcAdmin.textContent = why;
      return;
    }
    svcAdmin.className = "field-hint";
    svcAdmin.textContent = admin.known
      ? "Права администратора у этой учётной записи есть — Windows всё равно спросит подтверждение окном UAC."
      : "Есть ли у этой учётной записи права администратора, программа спросить не смогла. Windows попросит подтверждение при установке; без него служба не встанет.";
  };

  // --- галочки службы: две РАЗНЫЕ, и они не связаны друг с другом.
  //
  // Рисуем по списку из трубы (`service.toggles`), а не своим перечнем: харнесс
  // знает и умолчания, и оговорки. Ключи, которых окно писать не умеет, честно
  // называем — молча съесть галочку хуже, чем сказать «правь руками».
  const toggleView = new Map<string, { row: HTMLElement; note: HTMLElement }>();
  const known: Record<string, { get(): boolean; set(v: boolean): void }> = {
    "service.session0": { get: () => session0, set: (v) => (session0 = v) },
    "service.firewall": { get: () => firewall, set: (v) => (firewall = v) },
  };
  for (const t of option?.toggles || []) {
    const hand = known[t.key];
    const row = el("div", "mode-block");
    if (!hand) {
      row.append(
        el("p", "receipt err", `${t.title || t.key}: эту тумблер окно писать не умеет — код агента новее окна.`),
        el("p", "field-hint", `Правь её руками в helene.json, ключ ${t.key}. Что делает: ${t.text || "—"}`),
      );
      togglesBox.append(row);
      continue;
    }
    row.append(switchRow(t.title || t.key, hand.get(), (v) => {
      hand.set(v);
      syncToggles();
    }));
    if (t.text) row.append(el("p", "field-hint", t.text));
    // Оговорка — всегда на виду, а не после включения: её читают ДО.
    const warn = t.key === "service.session0"
      ? t.warning || live.session0_warning || SESSION0_WARNING_FALLBACK
      : t.warning;
    if (warn) row.append(el("p", "receipt err", warn));
    const note = el("p", "field-hint");
    row.append(note);
    toggleView.set(t.key, { row, note });
    togglesBox.append(row);
  }

  const syncToggles = () => {
    const s0 = toggleView.get("service.session0");
    if (s0) {
      s0.note.className = session0 && installed === false ? "receipt err" : "field-hint";
      s0.note.textContent = !session0
        ? `Выключено. Ключ service.session0 — его читает сама служба; на ограду «${choiceOf(picked)?.title || live.title}» не влияет.`
        : installed === false
          ? "Включено, но службы нет — дать нулевую сессию некому. Поставь службу кнопкой выше, иначе тумблер остаётся словом в файле."
          : "Включено: брокер будет выполнять команды агента правами системы, а код агента поднимется под службой, без рабочего стола.";
    }
    const fw = toggleView.get("service.firewall");
    if (fw) {
      fw.note.className = "field-hint";
      fw.note.textContent = firewall
        ? "Включено (умолчание). Кнопка «Телефон» откроет порт через службу; прав системы агенту это не даёт и с нулевой сессией не связано."
        : "Выключено: правило брандмауэра для телефона будет спрашивать права окном Windows каждый раз.";
    }
  };

  svcBox.append(svcRow, svcClient, svcAdmin, togglesBox);

  box.append(now, pickRow, planBox);
  // Секция службы — там, где служба бывает: Windows (SCM) и macOS (launchd).
  // Галочек у неё на Mac нет — `togglesBox` там остаётся пустым, и это решает
  // харнесс (`modes.service_option`), а не окно.
  box.append(svcBox);
  box.append(
    el(
      "p",
      "field-hint",
      "Ограда и служба — два разных вопроса. Ограду выбираешь здесь, и она раскладывается в ручки сама " +
        "(отдельного тумблера песочницы больше нет). Служба ставится поверх любой ограды и ни одну из них не снимает. " +
        "Применяется перезапуском программы.",
    ),
  );
  syncNow();
  syncPick();
  syncAdmin();
  syncToggles();
  void svcRefresh();
  if (!mac) {
    // Права спрашиваем после отрисовки: ответа может не быть вовсе, и ждать его
    // экрану незачем — как придёт, секция службы перерисуется сама. На macOS
    // спрашивать нечего: администратором там становятся вводом пароля.
    void adminProbe().then((r) => {
      admin = r;
      syncAdmin();
    });
  }

  return {
    el: box,
    name: () => choiceOf(picked)?.name || "",
    title: () => choiceOf(picked)?.title || "",
    sandbox: () => !!choiceOf(picked)?.sandbox,
    session0: () => session0,
    firewall: () => firewall,
    note: () => {
      const bits: string[] = [];
      if (live.legacy_service) {
        bits.push(
          `В файле режимом была записана служба — так писала первая редакция. Записал ограду «${choiceOf(picked)?.title || ""}»; ` +
            "саму службу это не тронуло.",
        );
      }
      // На macOS нулевой сессии нет — как и службы, которой её давать.
      if (!mac && session0 && installed === false) {
        bits.push("Нулевая сессия включена, но служба не установлена — дать её некому.");
      }
      if (legacyPipe) {
        bits.push(
          `Харнесс называл режимом службу — записал ограду «${choiceOf(picked)?.title || ""}». Проверь, та ли она.`,
        );
      }
      if (picked !== live.name && installed) {
        bits.push("Служба читает настройки при своём старте — перезапусти её, иначе ограда сменится только в окне.");
      }
      return bits.length ? " " + bits.join(" ") : "";
    },
  };
}
