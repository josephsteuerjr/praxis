// Первый запуск издания к серверу: адрес и ключ канала спрашиваются в окне.
//
// У варианта Praxis установщика нет по замыслу — поставка распаковывается. До
// 0.5.0 окно с пустым адресом открывалось «как есть» и билось об ошибки связи, а
// человек должен был догадаться отредактировать helene.json рядом с exe. Пишем
// тем же путём, что «Настройки»: config_save → restart_self.
//
// ⚠ Живёт у Praxis, а не в общем окне: у Элен харнесс рядом, и спрашивать адрес
// не у кого. До 10.09 эта карточка лежала в теле окна Элен за веткой
// `cfg.needs_remote` — то есть окно возило в себе первый запуск чужого продукта.
import { ApiError, cfg, shell } from "../../ui-kit/window/api";
import { esc, humanError, q } from "../../ui-kit/window/lib";
import { PRODUCT_NAME } from "../../ui-kit/window/state";

/**
 * Занять экран карточкой подключения, если адреса ещё нет.
 *
 * Возвращает `true`, когда карточка показана: окно тогда не подключается к
 * каналу и не грузит комнаты — связываться пока не с кем.
 */
export function askForServer(view: HTMLElement): boolean {
  if (!cfg.needs_remote) return false;
  document.body.classList.add("first-run");
  view.innerHTML = `<div class="first-run-card">
    <h2>${esc(cfg.product || PRODUCT_NAME)} · подключение к своему серверу</h2>
    <p class="muted">Это окно не запускает агента: оно подключается к агенту, который уже работает на твоём сервере. Нужны адрес и ключ канала (<code>PRAXIS_DESK_TOKEN</code> из <code>desk.env</code> на сервере).</p>
    <label>Адрес сервера<input id="fr-base" type="url" placeholder="https://praxis.example.org" autocomplete="off" spellcheck="false"></label>
    <label>Ключ канала<input id="fr-key" type="password" placeholder="ключ из desk.env" autocomplete="off" spellcheck="false"></label>
    <div class="first-run-row"><button id="fr-go" class="btn" type="button">Подключиться</button><span id="fr-note" class="muted"></span></div>
  </div>`;
  const base = q<HTMLInputElement>("#fr-base");
  const key = q<HTMLInputElement>("#fr-key");
  const note = q<HTMLElement>("#fr-note");
  const go = q<HTMLButtonElement>("#fr-go");
  go.addEventListener("click", async () => {
    const address = base.value.trim().replace(/\/+$/, "");
    if (!/^https?:\/\/.+/i.test(address)) {
      note.textContent = "адрес начинается с https:// — так же, как в браузере";
      return;
    }
    go.disabled = true;
    note.textContent = "проверяю…";
    try {
      // Сначала стучимся, потом пишем: сохранить нерабочий адрес и перезапуститься
      // в ту же пустоту — худший из возможных ответов на первый запуск.
      // Сеть отвечает своей ошибкой («Failed to fetch»), а humanError переводит её
      // как «агент не отвечает, программа продолжает попытки» — здесь это неправда:
      // никто ничего не повторяет, и адрес может быть просто набран с опечаткой.
      const probe = await fetch(address + "/api/health" + (key.value ? "?key=" + encodeURIComponent(key.value.trim()) : "")).catch(() => {
        throw new ApiError("по этому адресу никто не ответил — проверь адрес и что агент на сервере запущен");
      });
      if (!probe.ok) throw new ApiError(probe.status === 403 ? "сервер не принял ключ канала" : `сервер ответил ${probe.status}`);
      const loaded = await shell<{ config?: Record<string, unknown>; mtime_ns?: string }>("config_load");
      const next = { ...(loaded?.config || {}), mode: "remote", base: address, key: key.value.trim(), setup_complete: true };
      const saved = await shell<{ ok?: boolean; error?: string }>("config_save", { config: JSON.stringify(next), mtimeNs: loaded?.mtime_ns });
      if (saved && saved.ok === false) throw new ApiError(saved.error || "настройки не записались");
      note.textContent = "подключено, перезапускаю окно…";
      await shell("restart_self");
    } catch (e) {
      // Свою причину показываем как есть: humanError переводит коды канала, а здесь
      // сообщение уже написано человеку («по этому адресу никто не ответил»).
      note.textContent = "не вышло: " + (e instanceof ApiError ? e.message : humanError(e).text);
      go.disabled = false;
    }
  });
  base.focus();
  return true;
}
