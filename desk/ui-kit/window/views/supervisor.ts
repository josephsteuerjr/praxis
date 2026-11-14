import { t as uiText, locale } from "../../../lang/lang";
// Управление харнессом и его журналы — та часть экрана «Система», которая не
// рассказывает, а ДЕЙСТВУЕТ.
//
// Зачем отдельный файл. Окно к серверу было смотрелкой: видно переписку, ходы и
// кадр, а упавший раннер или неподнявшееся реле чинились только через ssh.
// Здесь — вторая сторона файлового протокола `deskd/control.py`: канал кладёт
// просьбу, надзор (`server/serverboot.py`) исполняет и отвечает распиской.
//
// ⚠ Две вещи, которых здесь намеренно НЕТ.
//   * Обещания «перезапускаю…» без надзора. Просьба, положенная в дерево, где
//     надзора нет, пролежит вечно; канал это знает и отвечает отказом с
//     причиной, а окно показывает причину, а не крутилку.
//   * Кнопки «обновить ядро» у самого контейнера. Контейнер не пересобирает сам
//     себя. Обновляет исполнитель РЯДОМ (`server/updater`, 27.09) — его раздел
//     рисует `update-card.ts`, и там «Подтвердить» есть только у плана, который он
//     уже сверил и показал. Нет исполнителя — команды названы словами.
import { api, post } from "../api";
import { esc, fmtTime } from "../lib";
import { updateActive, updateCardHTML, type UpdateState } from "./update-card";
import { mountAutonomy, type AutonomyState } from "../../autonomy";
import { cfg } from "../api";
import { FAST_MODE_HINT, serviceTierChoices, serviceTierReceipt } from "../../service-tier";
import { bindBrainModelControls, brainModelProblem, tierClearedByRoute, type ModelCatalog } from "../../model-controls";

export interface SupervisorChild {
  id: string;
  name: string;
  alive: boolean;
  pid: number | null;
  since_utc: string;
  falls: number;
  halted: string;
}

export interface Receipt {
  id: string;
  action: string;
  target: string;
  done: boolean;
  note: string;
  done_utc: string;
}

export interface Supervisor {
  kind: string;
  alive: boolean;
  beat_age: number | null;
  started_utc: string;
  in_container: boolean;
  children: SupervisorChild[];
  targets: Array<{ id: string; title: string }>;
  receipt: Receipt | null;
  pending: { target?: string } | null;
  control: { available: boolean; why: string };
}

export interface LogRow {
  id: string;
  title: string;
  exists: boolean;
  size?: number;
  changed_utc?: string;
}

function size(bytes: number | undefined): string {
  if (!bytes) return uiText("ui.ui.kit.window.views.supervisor.67dc5403f9");
  if (bytes < 1024) return bytes.toLocaleString(locale()) + uiText("ui.ui.kit.window.views.supervisor.ec78fc60af");
  if (bytes < 1024 * 1024) return (bytes / 1024).toLocaleString(locale(), {maximumFractionDigits: 0}) + uiText("ui.ui.kit.window.views.supervisor.69a96a5345");
  return (bytes / 1024 / 1024).toLocaleString(locale(), {minimumFractionDigits: 1, maximumFractionDigits: 1}) + uiText("ui.ui.kit.window.views.supervisor.883817f199");
}

function childRow(c: SupervisorChild): string {
  const state = c.halted
    ? uiText("ui.ui.kit.window.views.supervisor.5c2b020630", {p0: String(esc(c.halted))})
    : c.alive
      ? uiText("ui.ui.kit.window.views.supervisor.bdb78f6e9e", {p0: String(c.pid ?? "?")})
      : uiText("ui.ui.kit.window.views.supervisor.6331a74875");
  return `<tr><td><b>${esc(c.name)}</b></td><td>${state}</td>
    <td class="muted">${c.since_utc ? esc(fmtTime(c.since_utc)) : ""}</td>
    <td class="muted">${c.falls ? esc(String(c.falls)) + uiText("ui.ui.kit.window.views.supervisor.f560af2035") : ""}</td></tr>`;
}

/** Панель управления: кто держит харнесс, что поднято, чем можно тряхнуть. */
export function supervisorHTML(s: Supervisor | null): string {
  if (!s) return uiText("ui.ui.kit.window.views.supervisor.53e2479c43");
  if (!s.kind) {
    // Записки нет вовсе: харнесс поднят не надзором. На Windows это оболочка, и
    // у неё своя кнопка — врать про удалённый перезапуск здесь нечем.
    return `<p class="muted">${esc(s.control.why)}.</p>`;
  }
  const beat = s.alive
    ? uiText("ui.ui.kit.window.views.supervisor.fcc158b2e9", {p0: String(s.beat_age ?? "?")})
    : `<span class="receipt err">${esc(s.control.why)}</span>`;
  const head = uiText("ui.ui.kit.window.views.supervisor.338d7d1b13", {p0: String(esc(s.kind)), p1: String(s.in_container ? uiText("ui.ui.kit.window.views.supervisor.3c0f26b783") : ""), p2: String(esc(fmtTime(s.started_utc))), p3: String(beat)});
  const table = s.children.length
    ? `<table class="grid">${s.children.map(childRow).join("")}</table>`
    : uiText("ui.ui.kit.window.views.supervisor.e6a4555a27");
  const buttons = s.control.available
    ? uiText("ui.ui.kit.window.views.supervisor.9f4c038cb3", {p0: String(s.targets
        .map(
          (t) =>
            uiText("ui.ui.kit.window.views.supervisor.1a4154cbd8", {p0: String(esc(t.id)), p1: String(esc(t.title))}),
        )
        .join("")), p1: String(s.in_container
           ? uiText("ui.ui.kit.window.views.supervisor.29e84f9ac8")
           : uiText("ui.ui.kit.window.views.supervisor.aa60cf7be0"))})
    : uiText("ui.ui.kit.window.views.supervisor.d6bb86116e", {p0: String(esc(s.control.why))});
  const receipt = s.receipt
    ? uiText("ui.ui.kit.window.views.supervisor.40d01165ed", {p0: String(s.receipt.done ? "receipt ok" : "receipt err"), p1: String(esc(
        fmtTime(s.receipt.done_utc),
      )), p2: String(esc(s.receipt.note))})
    : "";
  const pending = s.pending
    ? uiText("ui.ui.kit.window.views.supervisor.546a71bcc9", {p0: String(esc(String(s.pending.target || "")))})
    : "";
  // Обновление — отдельный раздел ниже (`update-card.ts`): канал с доступом к докеру
  // хоста был бы не обновлением, а отмычкой, поэтому обновляет сосед снаружи.
  return head + table + buttons + pending + receipt;
}

/** Что уже нарисовано в коробке обновления — чтобы опрос не менял её без нужды. */
const drawnUpdate = new WeakMap<HTMLElement, string>();
/** Последний ответ канала — его и показываем, пока канал молчит (по панели: коробку
 *  раздела перерисовка панели заменяет). */
const lastUpdate = new WeakMap<HTMLElement, UpdateState>();

/**
 * Раздел «Обновление» в свою коробку: её опрос перерисовывает только её.
 *
 * ⚠ И только когда ответ ИЗМЕНИЛСЯ. Пока план ждёт «да», опрос идёт каждые четыре
 * секунды, и перерисовка того же самого подменяла кнопку под курсором новой: нажатие
 * посреди подмены терялось, а строка ответа под кнопками стиралась (найдено на
 * стенде 27.09 — «Подтвердить» оказывался устаревшим элементом).
 *
 * ⚠ Канал молчит — это не «исполнителя нет». Посреди подмены агент остановлен, и канал
 * (он живёт в том же контейнере) не отвечает минуты; прежде карточка в это время
 * советовала распаковать поставку руками и поднять исполнителя — ровно то, что ломает
 * идущую подмену (ревью 27.09). Теперь остаётся последнее, что было известно, с
 * пометкой, без кнопок; ветка «исполнителя нет» — только по ответу канала.
 * -> ответ канала и молчит ли он.
 */
async function drawUpdate(
  box: HTMLElement,
  inContainer: boolean,
): Promise<{ state: UpdateState | null; offline: boolean }> {
  const slot = box.querySelector<HTMLElement>("#update-box");
  let state: UpdateState | null = null;
  let offline = false;
  try {
    state = await api<UpdateState>("/api/update");
    lastUpdate.set(box, state);
  } catch {
    offline = true;
    state = lastUpdate.get(box) || null;
  }
  const html = updateCardHTML(state, { inContainer, fmt: fmtTime, offline });
  if (slot && drawnUpdate.get(slot) !== html) {
    slot.innerHTML = html;
    drawnUpdate.set(slot, html);
  }
  return { state, offline };
}

export interface ContainerRow {
  name: string;
  known: boolean;
  up: boolean;
  status: string;
  image: string;
  since: string;
}

export interface Containers {
  available: boolean;
  why: string;
  containers: ContainerRow[];
  allowed?: string[];
}

export interface Brain {
  ok: boolean;
  note?: string;
  why?: string;
  path?: string;
  writable?: boolean;
  roles?: Record<string, Record<string, string | number>>;
  service_tiers?: Record<string, {requested?:string|null;actual?:string|null;available?:boolean|null;codex?:boolean}>;
  frameworks?: Record<string, { base_url: string; key_present: boolean }>;
}

export interface BrainModels {
  ok: boolean;
  why?: string;
  by_framework?: Record<string, ModelCatalog & { ok: boolean; models?: string[]; why?: string; url?: string }>;
}

/**
 * Контейнеры рядом с агентом: живы ли, и кнопка поднять.
 *
 * ⚠ Почему это отдельно от надзора выше. Файловый протокол просит АГЕНТА
 * перезапустить своих детей — и он бесполезен ровно тогда, когда нужнее всего:
 * если агент лёг, просьбу со стола некому взять. Здесь просьба идёт мимо него,
 * в службу рядом (`server/deskctl.py`), и потому работает над мёртвым агентом.
 * Что ей позволено трогать, решает её закрытый список, а не это окно.
 */
export function containersHTML(state: Containers | null): string {
  if (!state || !state.available) {
    const why = state?.why || uiText("ui.ui.kit.window.views.supervisor.6f315a9924");
    return uiText("ui.ui.kit.window.views.supervisor.51b08d8e97", {p0: String(esc(why))});
  }
  const rows = state.containers
    .map(
      (c) => uiText("ui.ui.kit.window.views.supervisor.4f73923df9", {p0: String(esc(c.name)), p1: String(c.up ? `<span class="mono">${esc(c.status)}</span>` : `<span class="receipt err">${esc(c.status)}</span>`), p2: String(esc(c.image)), p3: String(esc(c.name)), p4: String(esc(c.name)), p5: String(esc(c.name))}),
    )
    .join("");
  return uiText("ui.ui.kit.window.views.supervisor.5aaecbf8aa", {p0: String(rows)});
}

/** Мозг: какая модель у какой роли и чем её заменить, если эта замолчала. */
export function brainHTML(state: Brain | null, models: BrainModels | null): string {
  if (!state || !state.ok) {
    const why = state?.note || state?.why || uiText("ui.ui.kit.window.views.supervisor.5121320c17");
    return uiText("ui.ui.kit.window.views.supervisor.dc9cd25e70", {p0: String(esc(why))});
  }
  const live: string[] = [];
  for (const spec of Object.values(models?.by_framework || {})) {
    if (spec.ok && spec.models) live.push(...spec.models);
  }
  const options = (current: string) => {
    const seen = new Set<string>([current, ...live].filter(Boolean));
    return [...seen]
      .map((m) => `<option value="${esc(m)}"${m === current ? " selected" : ""}>${esc(m)}</option>`)
      .join("");
  };
  // 12.09: у роли четыре свои ручки (модель, фреймворк, запасная и её фреймворк) плюс
  // усилие; раньше отсюда менялась только основная модель. Уезжает только изменённое.
  void options;
  const frameworks = Object.keys(state.frameworks || {});
  const efforts = ["", "low", "medium", "high", "xhigh"];
  const choice = (values: string[], current: string) =>
    [...new Set([current, ...values])]
      .map((v) => `<option value="${esc(v)}"${v === current ? " selected" : ""}>${esc(v || "—")}</option>`)
      .join("");
  const datalist = `<datalist id="brain-models">${[...new Set(live)].map((m) => `<option value="${esc(m)}"></option>`).join("")}</datalist>`;
  const rows = Object.entries(state.roles || {})
    .map(([role, spec]) => {
      const f = (k: string) => String(spec[k] ?? "");
      const input = (k: string, placeholder = "") =>
        `<input class="field-input" list="brain-models" data-f="${k}" data-was="${esc(f(k))}" value="${esc(f(k))}" placeholder="${esc(placeholder)}" spellcheck="false">`;
      const select = (k: string, values: string[]) =>
        `<select class="field-input" data-f="${k}" data-was="${esc(f(k))}">${choice(values, f(k))}</select>`;
      const tier = `<select class="field-input" data-f="service_tier" data-was="${esc(f('service_tier'))}">${serviceTierChoices(f('service_tier')).map(([value,title])=>`<option value="${esc(value)}"${value===f('service_tier')?' selected':''}>${esc(title)}</option>`).join('')}</select>`;
      return uiText("ui.ui.kit.window.views.supervisor.2fa3a01bd0", {p0: String(esc(role)), p1: String(state.service_tiers?.[role]?.codex===true || state.service_tiers?.[role]?.available===true), p2: String(state.service_tiers?.[role]?.available), p3: String(esc(role)), p4: String(esc(f("framework"))), p5: String(esc(f("model"))), p6: String(input("model")), p7: String(select("framework", frameworks)), p8: String(input("fallback_model", uiText("ui.ui.kit.window.views.supervisor.e2efc81fb0"))), p9: String(select("fallback_framework", ["", ...frameworks])), p10: String(select("reasoning_effort", efforts)), p11: String(state.service_tiers?.[role]?.codex || state.service_tiers?.[role]?.available?'':' hidden'), p12: String(tier), p13: String(esc(serviceTierReceipt(state.service_tiers?.[role]?.actual))), p14: String(esc(role))});
    })
    .join("");
  const liveNote = live.length
    ? uiText("ui.ui.kit.window.views.supervisor.6c3d475e48", {p0: String(live.length)})
    : uiText("ui.ui.kit.window.views.supervisor.04dfe118a9");
  return uiText("ui.ui.kit.window.views.supervisor.4c32adb469", {p0: String(esc(liveNote)), p1: String(esc(FAST_MODE_HINT())), p2: String(datalist), p3: String(rows)});
}


/** Прерывание живого хода (12.09): просьба в memory/.control, раннер снимает ход на тике. */
export function interruptHTML(): string {
  return uiText("ui.ui.kit.window.views.supervisor.828d859e9e");
}

/** Журналы: что есть, насколько свежо, и место под хвост выбранного. */
export function logsHTML(rows: LogRow[] | null): string {
  if (!rows || !rows.length) return "";
  const buttons = rows
    .map(
      (r) =>
        `<button class="btn quiet" data-log="${esc(r.id)}"${r.exists ? "" : " disabled"}>${esc(r.title)} <span class="muted">${
          r.exists ? esc(size(r.size)) : uiText("ui.ui.kit.window.views.supervisor.dc9337ff5b")
        }</span></button>`,
    )
    .join("");
  return uiText("ui.ui.kit.window.views.supervisor.34a1189b70", {p0: String(buttons)});
}

/** Перерисовать панель свежим ответом канала. Обработчики при этом не трогаются. */
async function draw(box: HTMLElement): Promise<void> {
  const [sR, lR, cR, bR, mR] = await Promise.allSettled([
    api<Supervisor>("/api/supervisor"),
    api<LogRow[]>("/api/logs"),
    api<Containers>("/api/containers"),
    api<Brain>("/api/brain"),
    api<BrainModels>("/api/brain-models"),
  ]);
  const state = sR.status === "fulfilled" ? sR.value : null;
  const logs = lR.status === "fulfilled" ? lR.value : null;
  const boxes = cR.status === "fulfilled" ? cR.value : null;
  const brain = bR.status === "fulfilled" ? bR.value : null;
  const models = mR.status === "fulfilled" ? mR.value : null;
  // Контейнеры и мозг рисуются, только когда служба рядом объявлена: у окна
  // Элен её нет, и пустой раздел там был бы обещанием без исполнителя.
  const extra = boxes?.available ? containersHTML(boxes) + brainHTML(brain, models) : "";
  box.innerHTML = uiText("ui.ui.kit.window.views.supervisor.fd1bf02058", {p0: String(interruptHTML()), p1: String(supervisorHTML(state)), p2: String(extra), p3: String(logsHTML(logs))});
  bindBrainModelControls(box, models?.by_framework || {});
  mountAutonomy(box.querySelector<HTMLElement>("[data-autonomy-box]")!, {
    read: () => api<AutonomyState>("/api/autonomy"), write: action => post("/api/autonomy", { action }),
    identity: () => cfg.base + "\n" + cfg.key,
  });
  // Канал молчит — «в контейнере ли мы» помним с прошлого ответа: иначе посреди подмены
  // раздел обновления пропал бы целиком.
  if (state) box.dataset.inContainer = state.in_container ? "1" : "";
  await drawUpdate(box, box.dataset.inContainer === "1");
}

/**
 * Нарисовать панель и оживить кнопки. Возвращается, когда нарисовано; дальше
 * живёт на обработчиках.
 *
 * ⚠ Обработчик вешается ОДИН раз и на сам `box`, а не на кнопки: перерисовка
 * меняет только его внутренности. Иначе после каждого перезапуска слушателей
 * становилось бы на один больше, и третье нажатие слало бы три просьбы.
 */
export async function mountSupervisor(box: HTMLElement): Promise<void> {
  await draw(box);

  // Обновление: пока оно идёт (сверка, ожидание «да», подмена), раздел перерисовывается
  // сам раз в четыре секунды; кончилось — опрос засыпает до следующей кнопки. Пропавшая
  // связь посреди подмены — ожидаема (канал живёт в том самом контейнере): ждём дальше.
  // `grace` — сколько опросов сделать даже без активного плана: после кнопки исполнитель
  // берёт файл не мгновенно, и первый ответ ещё может показывать прежний итог.
  let polling = false;
  const pollUpdate = async (grace = 0) => {
    if (polling) return;
    polling = true;
    let wasActive = false;
    try {
      while (box.isConnected) {
        const got = await drawUpdate(box, box.dataset.inContainer === "1");
        // Молчащий канал — ждём его дальше: раздел дорисуется, когда он вернётся.
        wasActive = got.offline || updateActive(got.state);
        if (!wasActive && grace-- <= 0) break;
        await new Promise((done) => setTimeout(done, 4000));
      }
    } finally {
      polling = false;
    }
  };
  void pollUpdate();

  let shown = "";

  // Узлы ищутся заново на каждый показ: перерисовка панели (`draw`) заменяет
  // её внутренности целиком, и запомненная ссылка указывала бы в оторванный
  // от страницы кусок — текст ложился бы в никуда.
  const showLog = async (id: string) => {
    const view = box.querySelector<HTMLPreElement>("#log-view");
    const again = box.querySelector<HTMLButtonElement>("[data-log-again]");
    if (!view) return;
    shown = id;
    view.hidden = false;
    if (again) again.hidden = false;
    view.textContent = uiText("ui.ui.kit.window.views.supervisor.b7eefd36b7");
    try {
      const got = await api<{ ok: boolean; title: string; text: string; note?: string; exists: boolean }>(
        `/api/log/${encodeURIComponent(id)}?lines=300`,
      );
      view.textContent = got.exists ? got.text || uiText("ui.ui.kit.window.views.supervisor.0b5781e580") : got.note || uiText("ui.ui.kit.window.views.supervisor.0aedb51881");
    } catch (e) {
      view.textContent = uiText("ui.ui.kit.window.views.supervisor.bfd271b125") + String(e);
    }
  };

  const showContainerLog = async (name: string, onlyErrors: boolean) => {
    const view = box.querySelector<HTMLPreElement>("#clog-view");
    if (!view) return;
    view.hidden = false;
    view.textContent = uiText("ui.ui.kit.window.views.supervisor.b7eefd36b7");
    try {
      const got = await api<{ ok: boolean; text?: string; note?: string; lines?: number }>(
        `/api/container-log/${encodeURIComponent(name)}?lines=200${onlyErrors ? "&errors=1" : ""}`,
      );
      view.textContent = got.ok
        ? got.text || (onlyErrors ? uiText("ui.ui.kit.window.views.supervisor.3bcdfe94c2") : uiText("ui.ui.kit.window.views.supervisor.0b5781e580"))
        : got.note || uiText("ui.ui.kit.window.views.supervisor.d283094609");
    } catch (e) {
      view.textContent = uiText("ui.ui.kit.window.views.supervisor.bfd271b125") + String(e);
    }
  };

  box.addEventListener("click", async (ev) => {
    const upd = (ev.target as HTMLElement).closest<HTMLButtonElement>(
      "[data-update-plan],[data-update-confirm],[data-update-verdict]");
    if (upd) {
      const version = upd.getAttribute("data-update-plan");
      const verdict = upd.getAttribute("data-update-verdict");
      const note = box.querySelector<HTMLElement>(version ? "#update-plan-note" : "#update-note");
      const say = (cls: string, text: string) => {
        if (note) {
          note.className = cls;
          note.textContent = text;
        }
      };
      upd.disabled = true;
      say("receipt", version ? uiText("ui.ui.kit.window.views.supervisor.09810ea4c7") : uiText("ui.ui.kit.window.views.supervisor.2f6d13ac8b"));
      try {
        const answer = version
          ? await post<{ ok: boolean; note: string }>("/api/update/plan", {
              version,
              backup: upd.getAttribute("data-backup") || "full",
            })
          : verdict
            ? await post<{ ok: boolean; note: string }>("/api/update/verdict", {
                id: upd.getAttribute("data-id") || "",
                key: upd.getAttribute("data-key") || "",
                verdict,
              })
            : await post<{ ok: boolean; note: string }>("/api/update/confirm", {
                id: upd.getAttribute("data-id") || "",
                nonce: upd.getAttribute("data-nonce") || "",
                decision: upd.getAttribute("data-update-confirm") || "",
              });
        say(answer.ok ? "receipt ok" : "receipt err", answer.note);
        if (answer.ok) {
          // Исполнитель берёт файл раз в три секунды — даём ему взять и перерисовываем.
          await new Promise((done) => setTimeout(done, 3500));
          void pollUpdate(4);
        } else {
          upd.disabled = false;
        }
      } catch (e) {
        say("receipt err", uiText("ui.ui.kit.window.views.supervisor.afbe540226") + String(e));
        upd.disabled = false;
      }
      return;
    }
    const spot = (ev.target as HTMLElement).closest<HTMLElement>(
      "[data-clog],[data-restart-container],[data-brain-apply],[data-interrupt]");
    if (spot) {
      const note = box.querySelector<HTMLElement>(
        spot.hasAttribute("data-brain-apply") ? "#brain-note" : "#cnt-note");
      const clog = spot.getAttribute("data-clog");
      if (clog) {
        await showContainerLog(clog, spot.hasAttribute("data-errors"));
        return;
      }
      if (spot.hasAttribute("data-interrupt")) {
        const inote = box.querySelector<HTMLElement>("#interrupt-note");
        if (inote) {
          inote.className = "receipt";
          inote.textContent = uiText("ui.ui.kit.window.views.supervisor.402838e4c8");
        }
        try {
          const answer = await post<{ ok: boolean; note: string }>("/api/interrupt", {
            scope: spot.getAttribute("data-interrupt") || "all" });
          if (inote) {
            inote.className = answer.ok ? "receipt ok" : "receipt err";
            inote.textContent = answer.note;
          }
        } catch (e) {
          if (inote) {
            inote.className = "receipt err";
            inote.textContent = uiText("ui.ui.kit.window.views.supervisor.afbe540226") + String(e);
          }
        }
        return;
      }
      const restartName = spot.getAttribute("data-restart-container");
      if (restartName) {
        if (note) {
          note.className = "receipt";
          note.textContent = uiText("ui.ui.kit.window.views.supervisor.7b2658d842", {p0: String(restartName)});
        }
        try {
          const answer = await post<{ ok: boolean; note: string }>("/api/containers/restart", { name: restartName });
          if (note) {
            note.className = answer.ok ? "receipt ok" : "receipt err";
            note.textContent = answer.note;
          }
          if (answer.ok) {
            await new Promise((done) => setTimeout(done, 3000));
            await draw(box);
          }
        } catch (e) {
          if (note) {
            note.className = "receipt err";
            note.textContent = uiText("ui.ui.kit.window.views.supervisor.afbe540226") + String(e);
          }
        }
        return;
      }
      const role = spot.getAttribute("data-brain-apply");
      if (role) {
        const form = box.querySelector<HTMLElement>(`[data-brain-form="${role}"]`);
        const problem = brainModelProblem(form);
        if (problem) { if (note) note.textContent = problem; return; }
        const fields: Record<string, string> = {};
        form?.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-f]").forEach((el) => {
          const key = el.getAttribute("data-f") || "";
          if (key && el.dataset.storedEffort === undefined && el.dataset.storedServiceTier === undefined && el.value.trim() !== (el.getAttribute("data-was") ?? "")) fields[key] = el.value.trim();
        });
        if (tierClearedByRoute(form)) fields.service_tier = '';
        if (!Object.keys(fields).length) {
          if (note) {
            note.className = "receipt";
            note.textContent = uiText("ui.ui.kit.window.views.supervisor.f6dba6a0b4");
          }
          return;
        }
        if (note) {
          note.className = "receipt";
          note.textContent = uiText("ui.ui.kit.window.views.supervisor.57366a8072", {p0: String(role), p1: String(Object.keys(fields).join(", "))});
        }
        try {
          const answer = await post<{ ok: boolean; note?: string; now?: Record<string, string> }>(
            "/api/brain", { role, fields });
          if (note) {
            note.className = answer.ok ? "receipt ok" : "receipt err";
            note.textContent = answer.ok
              ? uiText("ui.ui.kit.window.views.supervisor.b104a75f54", {p0: String(role), p1: String(Object.entries(fields).map(([k, v]) => `${k}=${v || "—"}`).join(", ")), p2: String(answer.note || "")})
              : answer.note || uiText("ui.ui.kit.window.views.supervisor.8735f485c3");
          }
          if (answer.ok) {
            await new Promise((done) => setTimeout(done, 1500));
            await draw(box);
          }
        } catch (e) {
          if (note) {
            note.className = "receipt err";
            note.textContent = uiText("ui.ui.kit.window.views.supervisor.afbe540226") + String(e);
          }
        }
        return;
      }
    }
    const target = (ev.target as HTMLElement).closest<HTMLElement>("[data-log],[data-restart],[data-log-again]");
    if (!target) return;
    if (target.hasAttribute("data-log-again")) {
      if (shown) await showLog(shown);
      return;
    }
    const log = target.getAttribute("data-log");
    if (log) {
      await showLog(log);
      return;
    }
    const restart = target.getAttribute("data-restart");
    if (!restart) return;
    const note = box.querySelector<HTMLElement>("#sv-note");
    const buttons = box.querySelectorAll<HTMLButtonElement>("[data-restart]");
    buttons.forEach((b) => (b.disabled = true));
    if (note) {
      note.className = "receipt";
      note.textContent = uiText("ui.ui.kit.window.views.supervisor.9ba666207b");
    }
    try {
      const answer = await post<{ ok: boolean; note: string }>("/api/supervisor/restart", { target: restart });
      if (note) {
        note.className = answer.ok ? "receipt" : "receipt err";
        note.textContent = answer.note;
      }
      if (answer.ok) {
        // Надзор берёт просьбу со стола раз в три секунды: ждём расписку, а
        // потом перерисовываем панель целиком — с новыми pid.
        await new Promise((done) => setTimeout(done, 4000));
        await draw(box);
        return;
      }
    } catch (e) {
      if (note) {
        note.className = "receipt err";
        note.textContent = uiText("ui.ui.kit.window.views.supervisor.afbe540226") + String(e);
      }
    }
    buttons.forEach((b) => (b.disabled = false));
  });
}
