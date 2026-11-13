import { card, field } from "../../dom";
import { keepBlock } from "../config";
import type { Config } from "./settings-frame";

export function validateOwnerContext(config: Config) {
  const p = config.owner_context;
  if (!p) return;
  for (const [key, min, max, label] of [["messages", 50, 2000, "Число сообщений"], ["characters", 1000, 600000, "Объём текста"], ["tokens", 2000, 200000, "Предел токенов"]] as const) {
    const n = p[key];
    if (n !== undefined && (!Number.isInteger(n) || n < min || n > max)) throw new Error(`${label}: введи целое число от ${min.toLocaleString("ru-RU")} до ${max.toLocaleString("ru-RU")}.`);
  }
}

export function ownerContextCard(draft: Config) {
  const box = document.createElement("div");
  const p = draft.owner_context || {};
  const edit = (key: "messages" | "characters" | "tokens", value: string) => {
    draft.owner_context = keepBlock(draft.owner_context, { [key]: value.trim() ? Number(value) : NaN });
  };
  box.append(
    field("Максимум свежих сообщений", String(p.messages ?? 600), (v) => edit("messages", v), { type: "number", hint: "От 50 до 2000. При свёртке старое остаётся в сводках и архиве." }),
    field("Максимум знаков разговора", String(p.characters ?? 128000), (v) => edit("characters", v), { type: "number", hint: "От 1000 до 600 000. Больший контекст помогает помнить детали, но увеличивает время и расход модели." }),
    field("Ориентировочный максимум токенов разговора", String(p.tokens ?? 40000), (v) => edit("tokens", v), { type: "number", hint: "От 2000 до 200 000. Считается свежий разговор; для конституции, сводок и ответа модели тоже нужно место." }),
  );
  return card("Контекст владельца", box, "Для главного диалога и твоей личной переписки в Telegram. По умолчанию — до 600 свежих сообщений и 128 000 знаков, плюс сводки прошлого. Учитывай размер контекста выбранной модели. После сохранения перезапусти движок.");
}
