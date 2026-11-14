import { t as uiText } from "../../lang/lang";
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
  view.innerHTML = uiText("ui.remote.src.first.run.9d83e043be", {p0: String(esc(cfg.product || PRODUCT_NAME))});
  const base = q<HTMLInputElement>("#fr-base");
  const key = q<HTMLInputElement>("#fr-key");
  const note = q<HTMLElement>("#fr-note");
  const go = q<HTMLButtonElement>("#fr-go");
  go.addEventListener("click", async () => {
    const address = base.value.trim().replace(/\/+$/, "");
    if (!/^https?:\/\/.+/i.test(address)) {
      note.textContent = uiText("ui.remote.src.first.run.10341306ea");
      return;
    }
    go.disabled = true;
    note.textContent = uiText("ui.remote.src.first.run.1bdd492f1c");
    try {
      // Сначала стучимся, потом пишем: сохранить нерабочий адрес и перезапуститься
      // в ту же пустоту — худший из возможных ответов на первый запуск.
      // Сеть отвечает своей ошибкой («Failed to fetch»), а humanError переводит её
      // как «агент не отвечает, программа продолжает попытки» — здесь это неправда:
      // никто ничего не повторяет, и адрес может быть просто набран с опечаткой.
      const probe = await fetch(address + "/api/health" + (key.value ? "?key=" + encodeURIComponent(key.value.trim()) : "")).catch(() => {
        throw new ApiError(uiText("ui.remote.src.first.run.f30c24da05"));
      });
      if (!probe.ok) throw new ApiError(probe.status === 403 ? uiText("ui.remote.src.first.run.603d36c7c4") : uiText("ui.remote.src.first.run.dcbdc3688a", {p0: String(probe.status)}));
      const loaded = await shell<{ config?: Record<string, unknown>; mtime_ns?: string }>("config_load");
      const next = { ...(loaded?.config || {}), mode: "remote", base: address, key: key.value.trim(), setup_complete: true };
      const saved = await shell<{ ok?: boolean; error?: string }>("config_save", { config: JSON.stringify(next), mtimeNs: loaded?.mtime_ns });
      if (saved && saved.ok === false) throw new ApiError(saved.error || uiText("ui.remote.src.first.run.bd4afc7c0d"));
      note.textContent = uiText("ui.remote.src.first.run.9b9cf53783");
      await shell("restart_self");
    } catch (e) {
      // Свою причину показываем как есть: humanError переводит коды канала, а здесь
      // сообщение уже написано человеку («по этому адресу никто не ответил»).
      note.textContent = uiText("ui.remote.src.first.run.356e6bf4c0") + (e instanceof ApiError ? e.message : humanError(e).text);
      go.disabled = false;
    }
  });
  base.focus();
  return true;
}
