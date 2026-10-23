
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
    note: "Тип ключа определяет Coding Plan или оплату по счётчику; проверь его в кабинете Z.ai. GLM-5.3: low / high / max; доступность моделей зависит от плана.",
  },
  {
    id: "minimax",
    label: "MiniMax",
    url: "https://api.minimax.io/anthropic",
    model: "MiniMax-M3.1-Flash",
    billing: "both",
    note: "Coding Plan и оплата по счётчику используют разные ключи. Для материкового Китая — api.minimaxi.com. M3.1: low / medium / high / xhigh / max; M3 использует adaptive thinking.",
  },
  {
    id: "kimi",
    label: "Kimi (Moonshot)",
    url: "https://api.kimi.com/coding",
    model: "k3",
    billing: "both",
    note: "Kimi Code: Китай — api.kimi.com/coding, международный — api.kimi.ai/coding. По счётчику — api.moonshot.ai/anthropic. Шкала low / high / max, модели и доступ зависят от аккаунта.",
  },
  {
    id: "anthropic",
    label: "Anthropic",
    url: "https://api.anthropic.com",
    model: "",
    billing: "pay",
    note: "Claude по счётчику. Новые модели используют adaptive thinking и output_config.effort; допустимые ступени зависят от версии модели. Каталог не проверяет генерацию.",
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    url: "https://api.deepseek.com/anthropic",
    model: "deepseek-flash",
    billing: "pay",
    note: "По счётчику. Текущий Flash и V4 Pro поддерживают рассуждение; серверное умолчание — high. Ступени low / high / max. Старый V4 Flash может быть алиасом более новой модели.",
  },
  {
    id: "qwen",
    label: "Qwen (Alibaba)",
    url: "https://coding.dashscope.aliyuncs.com/apps/anthropic",
    model: "qwen3.8-max",
    billing: "both",
    note: "Этот адрес — китайский Coding Plan; международный план и API по счётчику имеют другие адреса и каталоги из кабинета Model Studio. Для Qwen3.8: low / medium / xhigh. Отсутствие /models не доказывает отказ генерации.",
  },
];

export const OPENAI_PRESETS: Preset[] = [
  {
    id: "openai",
    label: "OpenAI",
    url: "https://api.openai.com/v1",
    model: "gpt-5.4",
    billing: "pay",
    note: "По счётчику. Прямой API использует Responses; effort передаётся в reasoning.effort и зависит от модели.",
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
export function effortPlan(provider: ProviderKind, model: string, baseUrl = ""): EffortPlan {
  const m = (model || "").trim().toLowerCase();
  let host = "";
  try { host = new URL(baseUrl).hostname; } catch {}
  const max: EffortChip = { value: "max", label: "max" };
  const defaults: EffortChip = { value: "", label: "не задавать" };
  if (provider === "chatgpt") {
    return {
      chips: [defaults, LOW, MEDIUM, HIGH, XHIGH],
      hint: "Ступень передаётся реле; доступные значения и умолчание зависят от выбранной модели и аккаунта. Пустое поле не обещает отключения рассуждения.",
    };
  }
  if (provider === "anthropic") {
    if (host === "api.z.ai" || (!baseUrl && m.startsWith("glm-"))) {
      return {
        chips: [{ value: "", label: "по умолчанию (max)" }, LOW, HIGH, max],
        hint: "Z.ai: шкала low / high / max, у GLM-5.3 рассуждение не выключается. Пустое — серверное умолчание max: самое глубокое и самое дорогое.",
      };
    }
    if (["api.kimi.com", "api.kimi.ai", "api.moonshot.ai", "api.moonshot.cn", "api.deepseek.com"].includes(host)) {
      return { chips: [defaults, LOW, HIGH, max], hint: "output_config.effort: low / high / max. Умолчание и доступность определяет конкретная модель провайдера." };
    }
    if (["api.minimax.io", "api.minimaxi.com"].includes(host) && m.startsWith("minimax-m3.1")) {
      return { chips: [defaults, LOW, MEDIUM, HIGH, XHIGH, max], hint: "MiniMax M3.1: output_config.effort; серверное умолчание max." };
    }
    if (host.endsWith(".aliyuncs.com") && m.startsWith("qwen3.8")) {
      return { chips: [defaults, LOW, MEDIUM, XHIGH], hint: "Qwen3.8: output_config.effort low / medium / xhigh, серверное умолчание xhigh." };
    }
    if (host === "api.anthropic.com") {
      const version = /claude-(?:opus|sonnet|haiku)-(\d+)[.-](\d+)/.exec(m);
      const major = Number(version?.[1] || 0), minor = Number(version?.[2] || 0);
      if (major > 4 || (major === 4 && minor >= 7)) return { chips: [defaults, LOW, MEDIUM, HIGH, XHIGH, max], hint: "Claude 4.7+: adaptive thinking и output_config.effort; поддержка зависит от модели." };
      if (major === 4 && minor === 6) return { chips: [defaults, LOW, MEDIUM, HIGH, max], hint: "Claude 4.6: adaptive thinking; effort low / medium / high / max." };
      if (m.startsWith("claude-opus-4-5") || m.startsWith("claude-opus-4.5")) return { chips: [defaults, LOW, MEDIUM, HIGH], hint: "Claude Opus 4.5: effort low / medium / high." };
    }
    return {
      chips: [],
      hint: "Для этого адреса и модели шкала не подтверждена. Совместимость с Messages сама по себе не задаёт effort; неизвестное значение не отправляется вслепую.",
    };
  }
  if (provider === "local") {
    return {
      chips: [{ value: "", label: "не задавать" }, LOW, MEDIUM, HIGH],
      hint: "reasoning_effort передаётся совместимому серверу; поддержку определяет загруженная модель и сервер, а не название Ollama или LM Studio. Каталог не подтверждает инструменты или зрение.",
    };
  }
  return {
    chips: [{ value: "", label: "не задавать" }, LOW, MEDIUM, HIGH, XHIGH],
    hint: "Поле reasoning_effort зависит от модели. Прямой OpenAI использует Responses, совместимые адреса — Chat Completions, если в конфиге не выбран api_mode. Каталог не доказывает поддержку инструментов или изображений.",
  };
}

/** Ступень, которую можно сохранить для провайдера: чужая сбрасывается в пустую. */
export function clampEffort(provider: ProviderKind, model: string, effort: string, baseUrl = ""): string {
  const value = (effort || "").trim().toLowerCase();
  return effortPlan(provider, model, baseUrl).chips.some((c) => c.value === value) ? value : "";
}
