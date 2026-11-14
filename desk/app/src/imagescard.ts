import { t as uiText } from "../../lang/lang";
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
  body.append(toggle(uiText("ui.app.src.imagescard.d6ed5de729"), enabled, (value) => enabled = value));
  const grid = el("div", "form-grid two");
  grid.append(field(uiText("ui.app.src.imagescard.f3bdf7cce3"), model, (value) => model = value, { mono: true }),
              field(uiText("ui.app.src.imagescard.572095d4a7"), size, (value) => size = value, { hint: uiText("ui.app.src.imagescard.ba614dc573") }));
  const options = (label: string, current: string, values: Array<{value:string;title:string;text:string}>, change: (value: string) => void) => {
    const row = el("div", "field");
    row.append(el("span", "field-label", label));
    const control = choice(values, current, change);
    control.el.setAttribute('aria-label', label);
    row.append(control.el); return row;
  };
  body.append(grid);
  body.append(options(uiText("ui.app.src.imagescard.43c6960012"), quality, [
    {value:'auto',title:uiText("ui.app.src.imagescard.13fa2396f4"),text:uiText("ui.app.src.imagescard.8363be6539")},
    {value:'low',title:uiText("ui.app.src.imagescard.65fd24f1cb"),text:uiText("ui.app.src.imagescard.925941bde6")},
    {value:'medium',title:uiText("ui.app.src.imagescard.10fb83a9e3"),text:uiText("ui.app.src.imagescard.2e6ed4894d")},
    {value:'high',title:uiText("ui.app.src.imagescard.38f1bbc278"),text:uiText("ui.app.src.imagescard.4c72045304")},
  ], value => quality=value));
  body.append(options(uiText("ui.app.src.imagescard.2084425180"), background, [
    {value:'opaque',title:uiText("ui.app.src.imagescard.f252ce497b"),text:uiText("ui.app.src.imagescard.08c4e2f573")},
    {value:'transparent',title:uiText("ui.app.src.imagescard.504001466b"),text:uiText("ui.app.src.imagescard.955c2208d8")},
    {value:'auto',title:uiText("ui.app.src.imagescard.13fa2396f4"),text:uiText("ui.app.src.imagescard.413632ea50")},
  ], value => background=value));
  const status = el("span", "receipt");
  const refresh = async () => {
    try { const state = await shell<string>("relay_status"); status.textContent = state === "authorized" ? uiText("ui.app.src.imagescard.3358405cf3") : state === "pending" ? uiText("ui.app.src.imagescard.db6d9d455a") : uiText("ui.app.src.imagescard.7eeab6504a"); }
    catch (error) { status.textContent = humanError(error).text; }
  };
  const actions = el("div", "actions");
  actions.append(button(uiText("ui.app.src.imagescard.59624437c6"), "quiet", login), button(uiText("ui.app.src.imagescard.8b02fe4085"), "quiet", () => void refresh()), status);
  body.append(actions);
  return {
    el: card(uiText("ui.app.src.imagescard.0a6258a5f3"), body, uiText("ui.app.src.imagescard.f85c0645eb")),
    enabled: () => enabled,
    collect(out: Config): string {
      if (!model.trim()) return uiText("ui.app.src.imagescard.e762a8343a");
      if (size.trim() !== "auto" && !/^[1-9]\d{0,4}x[1-9]\d{0,4}$/.test(size.trim())) return uiText("ui.app.src.imagescard.ef52ac3c47");
      out.images = keepBlock(out.images, { enabled, model: model.trim(), quality, size: size.trim(), background });
      return "";
    },
  };
}
