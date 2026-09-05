// Провайдеры модели — одна таблица на установщик и окно настроек (слово
// владельца 06.09: «из популярных ведь GLM, MiniMax и Kimi; DeepSeek и Qwen —
// только по счётчику»). Адреса и имена моделей сверены 06.09.2026 с
// документацией провайдеров; менять здесь, а не в двух интерфейсах порознь.
//
// Что программа умеет передать в запрос, а что нет — по коду ядра
// (tree/llm.py), а не по желанию: по протоколу OpenAI уходит `reasoning_effort`
// роли; по протоколу Anthropic ступень уходит ТОЛЬКО моделям glm-* (словарь
// Z.ai low/high/max), остальным — ничего, глубину решает модель. Параметр
// `thinking` (MiniMax M3, DeepSeek V4, Qwen) ядро шлёт лишь из своих рук, не
// из настроек: ручки для него здесь нет, и обещать её на экране нельзя.

export type ProviderKind = "api" | "anthropic" | "chatgpt" | "local";

export interface Preset {
  id: string;
  label: string;
  url: string;
  /** Модель по умолчанию; пусто — пусть владелец выберет из списка сервера. */
  model: string;
  /** Как платят: подписка (coding plan), по счётчику, или и так и так. */
  billing: "sub" | "pay" | "both";
  /** Одна-две фразы владельцу: что за план и как здесь с рассуждением. */
  note: string;
}

export const BILLING_LABEL: Record<Preset["billing"], string> = {
  sub: "подписка",
  pay: "по счётчику",
  both: "подписка или по счётчику",
};

export const ANTHROPIC_PRESETS: Preset[] = [
  {
    id: "zai",
    label: "Z.ai (GLM)",
    url: "https://api.z.ai/api/anthropic",
    model: "glm-5.3",
    billing: "both",
    note: "GLM Coding Plan — подписка; тот же адрес работает и по счётчику. Рассуждение у GLM-5.3 не выключается, ступень low / high / max уходит в запрос.",
  },
  {
    id: "minimax",
    label: "MiniMax",
    url: "https://api.minimax.io/anthropic",
    model: "MiniMax-M2.7",
    billing: "both",
    note: "Coding Plan — подписка, ключ плана отдельный от ключа по счётчику; для аккаунтов материкового Китая адрес api.minimaxi.com. M2.7 думает всегда; M3 — только с параметром thinking, которого программа пока не шлёт.",
  },
  {
    id: "kimi",
    label: "Kimi (Moonshot)",
    url: "https://api.kimi.com/coding",
    model: "k3",
    billing: "both",
    note: "Kimi Code — подписка (модели k3, k3-256k, kimi-for-coding). По счётчику — адрес https://api.moonshot.ai/anthropic и модель kimi-k3. Рассуждение у k3 включено всегда.",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    url: "https://api.anthropic.com",
    model: "",
    billing: "pay",
    note: "Claude по счётчику. Глубину рассуждения выбирает сама модель; список моделей сервер отдаёт — нажми «Проверить».",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    url: "https://api.deepseek.com/anthropic",
    model: "deepseek-v4-flash",
    billing: "pay",
    note: "Только по счётчику (V4 Flash дешевле, V4 Pro сильнее). Рассуждение у V4 включается параметром thinking, которого программа пока не шлёт, — отвечает быстрым режимом.",
  },
  {
    id: "qwen",
    label: "Qwen (Alibaba)",
    url: "https://coding.dashscope.aliyuncs.com/apps/anthropic",
    model: "qwen3.8-max",
    billing: "both",
    note: "Этот адрес — Coding Plan (подписка). По счётчику адрес с id рабочего пространства выдаёт кабинет Model Studio. Списка моделей сервер не отдаёт: «Проверить» скажет «нет /models», это не ошибка. Рассуждение — параметром thinking, его программа пока не шлёт.",
  },
];

export const OPENAI_PRESETS: Preset[] = [
  {
    id: "openai",
    label: "OpenAI",
    url: "https://api.openai.com/v1",
    model: "gpt-5.4",
    billing: "pay",
    note: "По счётчику. Ступень уходит полем reasoning_effort.",
  },
];

export interface EffortChip {
  value: string;
  label: string;
}

export interface EffortPlan {
  /** Ступени, которые ядро действительно передаст; пусто — селектора нет. */
  chips: EffortChip[];
  hint: string;
}

const LOW: EffortChip = { value: "low", label: "low" };
const MEDIUM: EffortChip = { value: "medium", label: "medium" };
const HIGH: EffortChip = { value: "high", label: "high" };
const XHIGH: EffortChip = { value: "xhigh", label: "xhigh" };

/** Шкала рассуждения, которую программа ДЕЙСТВИТЕЛЬНО передаст провайдеру. */
export function effortPlan(provider: ProviderKind, model: string): EffortPlan {
  const m = (model || "").trim().toLowerCase();
  if (provider === "chatgpt") {
    return {
      chips: [{ value: "", label: "без рассуждения" }, LOW, MEDIUM, HIGH, XHIGH],
      hint: "На подписке пустое усилие означает «рассуждение выключено»: реле гасит его своим умолчанием. Это быстро и дёшево; для трудных ходов бери medium и выше.",
    };
  }
  if (provider === "anthropic") {
    if (m.startsWith("glm-")) {
      // Ядро проецирует ступени в словарь Z.ai: low→low, high→high, xhigh→max
      // (tree/llm.py, _GLM_EFFORT); medium там сливается с high, поэтому не
      // предлагается — ярлык обязан совпадать с тем, что уедет.
      return {
        chips: [{ value: "", label: "по умолчанию (max)" }, LOW, HIGH, { value: "xhigh", label: "max" }],
        hint: "Z.ai: шкала low / high / max, у GLM-5.3 рассуждение не выключается. Пустое — серверное умолчание max: самое глубокое и самое дорогое.",
      };
    }
    return {
      chips: [],
      hint: "По протоколу Anthropic ступень в запрос не уходит — глубину решает модель: Claude выбирает сама, Kimi k3 и MiniMax M2.x думают всегда, а MiniMax M3, DeepSeek V4 и Qwen включают рассуждение параметром thinking, которого программа пока не шлёт.",
    };
  }
  if (provider === "local") {
    return {
      chips: [{ value: "", label: "не задавать" }, LOW, MEDIUM, HIGH],
      hint: "Ollama и LM Studio поле reasoning_effort чаще всего пропускают мимо: «не задавать» — самое честное.",
    };
  }
  return {
    chips: [{ value: "", label: "не задавать" }, LOW, MEDIUM, HIGH, XHIGH],
    hint: "Уходит полем reasoning_effort протокола OpenAI. Серверам с другой шкалой (DeepSeek: low / high / max, Qwen: enable_thinking) оставь «не задавать» — чужое значение вернётся отказом 400.",
  };
}

/** Ступень, которую можно сохранить для провайдера: чужая сбрасывается в пустую. */
export function clampEffort(provider: ProviderKind, model: string, effort: string): string {
  const value = (effort || "").trim().toLowerCase();
  return effortPlan(provider, model).chips.some((c) => c.value === value) ? value : "";
}
