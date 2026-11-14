import { t as uiText, UiError, locale } from "../../../lang/lang";
import { card, field } from "../../dom";
import { keepBlock } from "../config";
import type { Config } from "./settings-frame";

export function validateOwnerContext(config: Config) {
  const p = config.owner_context;
  if (!p) return;
  for (const [key, min, max, label] of [["messages", 50, 2000, uiText("ui.ui.kit.window.views.owner.context.card.5e1fa3d0ab")], ["characters", 1000, 600000, uiText("ui.ui.kit.window.views.owner.context.card.8d6ee29fd7")], ["tokens", 2000, 200000, uiText("ui.ui.kit.window.views.owner.context.card.ff63ef2f19")]] as const) {
    const n = p[key];
    if (n !== undefined && (!Number.isInteger(n) || n < min || n > max)) throw new UiError(uiText("ui.ui.kit.window.views.owner.context.card.58ae6ae17b", {p0: String(label), p1: String(min.toLocaleString(locale())), p2: String(max.toLocaleString(locale()))}));
  }
}

export function ownerContextCard(draft: Config) {
  const box = document.createElement("div");
  const p = draft.owner_context || {};
  const edit = (key: "messages" | "characters" | "tokens", value: string) => {
    draft.owner_context = keepBlock(draft.owner_context, { [key]: value.trim() ? Number(value) : NaN });
  };
  box.append(
    field(uiText("ui.ui.kit.window.views.owner.context.card.f86d27ba7c"), String(p.messages ?? 600), (v) => edit("messages", v), { type: "number", hint: uiText("ui.ui.kit.window.views.owner.context.card.cd7598ffe8") }),
    field(uiText("ui.ui.kit.window.views.owner.context.card.eab2eef68f"), String(p.characters ?? 128000), (v) => edit("characters", v), { type: "number", hint: uiText("ui.ui.kit.window.views.owner.context.card.54c6c50178") }),
    field(uiText("ui.ui.kit.window.views.owner.context.card.5a5d57b010"), String(p.tokens ?? 40000), (v) => edit("tokens", v), { type: "number", hint: uiText("ui.ui.kit.window.views.owner.context.card.b73116d1ce") }),
  );
  return card(uiText("ui.ui.kit.window.views.owner.context.card.9a63bdb4b2"), box, uiText("ui.ui.kit.window.views.owner.context.card.2d63f29cbb"));
}
