import { button, card, choice, field, toggle } from "../../ui-kit/dom";
import { keepBlock } from "../../ui-kit/window/config";
import { el, humanError } from "../../ui-kit/window/lib";
import { shell } from "../../ui-kit/window/api";
import type { Config } from "../../ui-kit/window/views/settings-frame";

export function imagesCard(draft: Config, login: () => void) {
  const saved = (draft.images || {}) as Record<string, unknown>;
  let enabled = saved.enabled === true;
  let model = String(saved.model || "gpt-image-2");
  let quality = String(saved.quality || "auto");
  let size = String(saved.size || "auto");
  let background = String(saved.background || "opaque");
  const body = el("div", "images-options");
  body.append(toggle("Генерация изображений", enabled, (value) => enabled = value));
  const grid = el("div", "form-grid two");
  grid.append(field("Модель", model, (value) => model = value, { mono: true }),
              field("Размер", size, (value) => size = value, { hint: "auto или 1024x1024" }));
  const options = (label: string, current: string, values: Array<{value:string;title:string;text:string}>, change: (value: string) => void) => {
    const row = el("div", "field");
    row.append(el("span", "field-label", label));
    const control = choice(values, current, change);
    control.el.setAttribute('aria-label', label);
    row.append(control.el); return row;
  };
  body.append(grid);
  body.append(options("Качество", quality, [
    {value:'auto',title:'По запросу',text:'Модель выберет качество для задачи.'},
    {value:'low',title:'Черновик',text:'Для эскиза и проверки идеи.'},
    {value:'medium',title:'Обычное',text:'Для готовой иллюстрации.'},
    {value:'high',title:'Подробное',text:'Для проработки мелких деталей.'},
  ], value => quality=value));
  body.append(options("Фон", background, [
    {value:'opaque',title:'С фоном',text:'Картинка заполняет весь холст.'},
    {value:'transparent',title:'Прозрачный',text:'Объект без фона — для наложения на другой рисунок.'},
    {value:'auto',title:'По запросу',text:'Модель выберет, нужен ли фон, по твоему описанию.'},
  ], value => background=value));
  const status = el("span", "receipt");
  const refresh = async () => {
    try { const state = await shell<string>("relay_status"); status.textContent = state === "authorized" ? "Вход в ChatGPT выполнен" : state === "pending" ? "Ждём вход в браузере" : "Нужен вход в ChatGPT"; }
    catch (error) { status.textContent = humanError(error).text; }
  };
  const actions = el("div", "actions");
  actions.append(button("Войти в ChatGPT", "quiet", login), button("Проверить вход", "quiet", () => void refresh()), status);
  body.append(actions);
  return {
    el: card("Изображения", body, "Создание и правка картинок через подписку ChatGPT. Агент может обращаться к этой модели при любом голосе. После выбора нажми «Сохранить»."),
    enabled: () => enabled,
    collect(out: Config): string {
      if (!model.trim()) return "Нужно имя модели изображений.";
      if (size.trim() !== "auto" && !/^[1-9]\d{0,4}x[1-9]\d{0,4}$/.test(size.trim())) return "Размер изображения: auto или ШИРИНАxВЫСОТА.";
      out.images = keepBlock(out.images, { enabled, model: model.trim(), quality, size: size.trim(), background });
      return "";
    },
  };
}
