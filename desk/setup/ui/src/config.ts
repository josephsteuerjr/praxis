// Одна точка правды прототипа: имя, кадр, тексты.
//
// 1.2 (27.09): тот же мастер ставит и Praxis — окно к агенту на своём сервере (exe,
// собранный с фичей `praxis`, говорит это строкой `window.SETUP_VARIANT`; в превью —
// `?variant=praxis`). До 1.2 Praxis ставили серые страницы NSIS.
export const VARIANT: "helene" | "praxis" =
  (globalThis as { SETUP_VARIANT?: string }).SETUP_VARIANT === "praxis" ||
  new URLSearchParams(globalThis.location?.search ?? "").get("variant") === "praxis"
    ? "praxis"
    : "helene";
export const isPraxis = (): boolean => VARIANT === "praxis";
export const PRODUCT_NAME = VARIANT === "praxis" ? "Praxis" : "Hélène";

// Основной дизайн-кадр — Full HD. Композиция сжимается пропорционально до
// MIN_SCALE (1280×720), дальше обрезается границами окна, не перестраивается.
export const STAGE = { w: 1920, h: 1080 } as const;
export const MIN_SCALE = 1280 / STAGE.w;

// 28.09: презентация («Программный комплекс…») и печатная машинка ушли из мастера
// вместе с заставкой — остались только слова темы.
export const COPY = {
  theme: { system: "Системная", light: "Светлая", dark: "Тёмная" },
} as const;
