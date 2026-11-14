import { t as uiText } from "../lang/lang";

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
  get sub() {return uiText("ui.ui.kit.providers.b81189dcda");},
  get pay() {return uiText("ui.ui.kit.providers.cd7a8020bc");},
  get both() {return uiText("ui.ui.kit.providers.f713a42c04");},
};

export const ANTHROPIC_PRESETS: Preset[] = [
  {
    id: "zai",
    label: "Z.ai (GLM)",
    url: "https://api.z.ai/api/anthropic",
    model: "glm-5.3",
    billing: "both",
    get note() {return uiText("ui.ui.kit.providers.a860a267c7");},
  },
  {
    id: "minimax",
    label: "MiniMax",
    url: "https://api.minimax.io/anthropic",
    model: "MiniMax-M3.1-Flash",
    billing: "both",
    get note() {return uiText("ui.ui.kit.providers.e772798c09");},
  },
  {
    id: "kimi",
    label: "Kimi (Moonshot)",
    url: "https://api.kimi.com/coding",
    model: "k3",
    billing: "both",
    get note() {return uiText("ui.ui.kit.providers.9a34791794");},
  },
  {
    id: "anthropic",
    label: "Anthropic",
    url: "https://api.anthropic.com",
    model: "",
    billing: "pay",
    get note() {return uiText("ui.ui.kit.providers.a464f03d7e");},
  },
  {
    id: "deepseek",
    label: "DeepSeek",
    url: "https://api.deepseek.com/anthropic",
    model: "deepseek-flash",
    billing: "pay",
    get note() {return uiText("ui.ui.kit.providers.1e869d5853");},
  },
  {
    id: "qwen",
    label: "Qwen (Alibaba)",
    url: "https://coding.dashscope.aliyuncs.com/apps/anthropic",
    model: "qwen3.8-max",
    billing: "both",
    get note() {return uiText("ui.ui.kit.providers.37addf6eff");},
  },
];

export const OPENAI_PRESETS: Preset[] = [
  {
    id: "openai",
    label: "OpenAI",
    url: "https://api.openai.com/v1",
    model: "gpt-5.4",
    billing: "pay",
    get note() {return uiText("ui.ui.kit.providers.951f6fab42");},
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

export interface ModelDetails {
  id: string;
  reasoning_efforts?: string[];
  default_reasoning_effort?: string;
  service_tiers?: Array<{id:string;name?:string;description?:string}>;
  additional_speed_tiers?: string[];
}

export function fastModeAvailability(details?: ModelDetails): boolean | null {
  if (!details || details.service_tiers === undefined && details.additional_speed_tiers === undefined) return null;
  if (details.service_tiers !== undefined) return details.service_tiers.some(tier => tier.id === 'priority' || tier.id === 'fast');
  return details.additional_speed_tiers?.some(tier => tier === 'priority' || tier === 'fast') === true;
}

const LOW: EffortChip = { value: "low", label: "low" };
const MEDIUM: EffortChip = { value: "medium", label: "medium" };
const HIGH: EffortChip = { value: "high", label: "high" };
const XHIGH: EffortChip = { value: "xhigh", label: "xhigh" };
export const CODEX_REASONING_LEVELS = ['minimal','low','medium','high','xhigh','max','ultra'] as const;

/** Шкала рассуждения, которую программа ДЕЙСТВИТЕЛЬНО передаст провайдеру. */
export function effortPlan(provider: ProviderKind, model: string, baseUrl = "", reasoningEfforts?: readonly string[]): EffortPlan {
  const m = (model || "").trim().toLowerCase();
  let host = "";
  try { host = new URL(baseUrl).hostname; } catch {}
  const max: EffortChip = { value: "max", label: "max" };
  const defaults: EffortChip = { value: "", get label() {return uiText("ui.ui.kit.providers.33a2024abd");} };
  if (provider === "chatgpt") {
    const allowed = [...new Set((reasoningEfforts || []).filter(value => (CODEX_REASONING_LEVELS as readonly string[]).includes(value)))];
    return {
      chips: [{value:'',get label() {return uiText("ui.ui.kit.providers.fd0560ebbf");}}, ...allowed.map(value => ({value,label:value}))],
      hint: reasoningEfforts === undefined
        ? uiText("ui.ui.kit.providers.143871cd4c")
        : uiText("ui.ui.kit.providers.d4d7b2c0f2"),
    };
  }
  if (provider === "anthropic") {
    if (host === "api.z.ai" || (!baseUrl && m.startsWith("glm-"))) {
      return {
        chips: [{ value: "", get label() {return uiText("ui.ui.kit.providers.be3e6666ea");} }, LOW, HIGH, max],
        get hint() {return uiText("ui.ui.kit.providers.dd02284875");},
      };
    }
    if (["api.kimi.com", "api.kimi.ai", "api.moonshot.ai", "api.moonshot.cn", "api.deepseek.com"].includes(host)) {
      return { chips: [defaults, LOW, HIGH, max], get hint() {return uiText("ui.ui.kit.providers.cbf311c005");} };
    }
    if (["api.minimax.io", "api.minimaxi.com"].includes(host) && m.startsWith("minimax-m3.1")) {
      return { chips: [defaults, LOW, MEDIUM, HIGH, XHIGH, max], get hint() {return uiText("ui.ui.kit.providers.16526a8c13");} };
    }
    if (host.endsWith(".aliyuncs.com") && m.startsWith("qwen3.8")) {
      return { chips: [defaults, LOW, MEDIUM, XHIGH], get hint() {return uiText("ui.ui.kit.providers.f12e1cbbf6");} };
    }
    if (host === "api.anthropic.com") {
      const version = /claude-(?:opus|sonnet|haiku)-(\d+)[.-](\d+)/.exec(m);
      const major = Number(version?.[1] || 0), minor = Number(version?.[2] || 0);
      if (major > 4 || (major === 4 && minor >= 7)) return { chips: [defaults, LOW, MEDIUM, HIGH, XHIGH, max], get hint() {return uiText("ui.ui.kit.providers.384325b23c");} };
      if (major === 4 && minor === 6) return { chips: [defaults, LOW, MEDIUM, HIGH, max], hint: "Claude 4.6: adaptive thinking; effort low / medium / high / max." };
      if (m.startsWith("claude-opus-4-5") || m.startsWith("claude-opus-4.5")) return { chips: [defaults, LOW, MEDIUM, HIGH], hint: "Claude Opus 4.5: effort low / medium / high." };
    }
    return {
      chips: [],
      get hint() {return uiText("ui.ui.kit.providers.7fff0b45aa");},
    };
  }
  if (provider === "local") {
    return {
      chips: [{ value: "", get label() {return uiText("ui.ui.kit.providers.33a2024abd");} }, LOW, MEDIUM, HIGH],
      get hint() {return uiText("ui.ui.kit.providers.0d92e787cc");},
    };
  }
  return {
    chips: [{ value: "", get label() {return uiText("ui.ui.kit.providers.33a2024abd");} }, LOW, MEDIUM, HIGH, XHIGH],
    get hint() {return uiText("ui.ui.kit.providers.b7664bd7f4");},
  };
}

/** Ступень, которую можно сохранить для провайдера: чужая сбрасывается в пустую. */
export function clampEffort(provider: ProviderKind, model: string, effort: string, baseUrl = "", reasoningEfforts?: readonly string[]): string {
  const value = (effort || "").trim().toLowerCase();
  return effortPlan(provider, model, baseUrl, reasoningEfforts).chips.some((c) => c.value === value) ? value : "";
}
