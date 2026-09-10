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
//   * Кнопки «обновить ядро». Контейнер не пересобирает сам себя, и притворяться
//     тут нечем: обновление — это новая поставка и `docker compose up --build`
//     на хосте. Команды названы словами, а кнопки нет.
import { api, post } from "../api";
import { esc, fmtTime } from "../lib";

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
  if (!bytes) return "пусто";
  if (bytes < 1024) return bytes + " Б";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(0) + " КБ";
  return (bytes / 1024 / 1024).toFixed(1) + " МБ";
}

function childRow(c: SupervisorChild): string {
  const state = c.halted
    ? `<span class="receipt err">остановлен: ${esc(c.halted)}</span>`
    : c.alive
      ? `<span class="mono">жив, pid ${c.pid ?? "?"}</span>`
      : '<span class="receipt err">не поднят</span>';
  return `<tr><td><b>${esc(c.name)}</b></td><td>${state}</td>
    <td class="muted">${c.since_utc ? esc(fmtTime(c.since_utc)) : ""}</td>
    <td class="muted">${c.falls ? esc(String(c.falls)) + " падений за час" : ""}</td></tr>`;
}

/** Панель управления: кто держит харнесс, что поднято, чем можно тряхнуть. */
export function supervisorHTML(s: Supervisor | null): string {
  if (!s) return '<p class="muted">Харнесс о себе ничего не сказал.</p>';
  if (!s.kind) {
    // Записки нет вовсе: харнесс поднят не надзором. На Windows это оболочка, и
    // у неё своя кнопка — врать про удалённый перезапуск здесь нечем.
    return `<p class="muted">${esc(s.control.why)}.</p>`;
  }
  const beat = s.alive
    ? `бьётся ${s.beat_age ?? "?"} с назад`
    : `<span class="receipt err">${esc(s.control.why)}</span>`;
  const head = `<p class="muted">Надзор: <b>${esc(s.kind)}</b>${s.in_container ? " в контейнере" : ""} ·
    поднят ${esc(fmtTime(s.started_utc))} · ${beat}</p>`;
  const table = s.children.length
    ? `<table class="grid">${s.children.map(childRow).join("")}</table>`
    : '<p class="muted">Детей в записке нет.</p>';
  const buttons = s.control.available
    ? `<div class="actions" style="margin-top:10px">${s.targets
        .map(
          (t) =>
            `<button class="btn quiet" data-restart="${esc(t.id)}">Перезапустить ${esc(t.title)}</button>`,
        )
        .join("")}<span class="receipt" id="sv-note"></span></div>
       <p class="muted">«Перезапустить весь харнесс» ${
         s.in_container
           ? "гасит надзор — контейнер поднимает его заново; окно на несколько секунд потеряет связь"
           : "перезапускает детей на месте: надзор запущен не в контейнере, выходить ему некуда"
       }.</p>`
    : `<p class="muted">Управление отсюда недоступно: ${esc(s.control.why)}.</p>`;
  const receipt = s.receipt
    ? `<p class="${s.receipt.done ? "receipt ok" : "receipt err"}">Последняя просьба (${esc(
        fmtTime(s.receipt.done_utc),
      )}): ${esc(s.receipt.note)}</p>`
    : "";
  const pending = s.pending
    ? `<p class="receipt">Просьба лежит и ещё не взята: ${esc(String(s.pending.target || ""))}</p>`
    : "";
  // Кнопки «обновить ядро» здесь нет и не будет: контейнер не пересобирает сам
  // себя, а канал с доступом к докеру хоста — это не обновление, а отмычка.
  // Поэтому — словами, что и где сделать.
  const update = s.in_container
    ? `<p class="muted">Обновление ядра — дело хоста, а не контейнера: распакуй новую поставку в ту же папку и
       <code>docker compose -f server/docker-compose.yml up -d --build</code>. Данные агента (<code>data/</code>) и
       <code>helene.json</code> лежат рядом с контейнером и переживают пересборку.</p>`
    : "";
  return head + table + buttons + pending + receipt + update;
}

/** Журналы: что есть, насколько свежо, и место под хвост выбранного. */
export function logsHTML(rows: LogRow[] | null): string {
  if (!rows || !rows.length) return "";
  const buttons = rows
    .map(
      (r) =>
        `<button class="btn quiet" data-log="${esc(r.id)}"${r.exists ? "" : " disabled"}>${esc(r.title)} <span class="muted">${
          r.exists ? esc(size(r.size)) : "нет"
        }</span></button>`,
    )
    .join("");
  return `<h3 class="section-title">Журналы</h3>
    <p class="muted">Хвост журнала — то же, что <code>docker logs</code>, только без ssh. Показывается последние 300 строк.</p>
    <div class="actions">${buttons}<button class="btn quiet" data-log-again hidden>Обновить</button></div>
    <pre class="mono" id="log-view" style="max-height:340px;overflow:auto;white-space:pre-wrap;margin-top:10px" hidden></pre>`;
}

/** Перерисовать панель свежим ответом канала. Обработчики при этом не трогаются. */
async function draw(box: HTMLElement): Promise<void> {
  const [sR, lR] = await Promise.allSettled([api<Supervisor>("/api/supervisor"), api<LogRow[]>("/api/logs")]);
  const state = sR.status === "fulfilled" ? sR.value : null;
  const logs = lR.status === "fulfilled" ? lR.value : null;
  box.innerHTML = `<h3 class="section-title">Управление</h3>${supervisorHTML(state)}${logsHTML(logs)}`;
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
    view.textContent = "читаю…";
    try {
      const got = await api<{ ok: boolean; title: string; text: string; note?: string; exists: boolean }>(
        `/api/log/${encodeURIComponent(id)}?lines=300`,
      );
      view.textContent = got.exists ? got.text || "(пусто)" : got.note || "журнала нет";
    } catch (e) {
      view.textContent = "не прочиталось: " + String(e);
    }
  };

  box.addEventListener("click", async (ev) => {
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
      note.textContent = "прошу…";
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
        note.textContent = "не дошло: " + String(e);
      }
    }
    buttons.forEach((b) => (b.disabled = false));
  });
}
