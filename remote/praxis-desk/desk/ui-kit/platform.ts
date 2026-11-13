// Система агента и клавиатура окна — ДВА разных вопроса, и здесь они разведены.
//
//   1. ХОСТ — на какой системе живёт агент (оболочка отвечает `app_info.platform`,
//      установщик — `defaults.platform`: слово std::env::consts::OS). По нему
//      окно решает ДВА разных дела, и путать их нельзя:
//        · прячет то, чего на этой системе нет по построению — на macOS это
//          галочка нулевой сессии, правило брандмауэра и UAC. Не «на macOS
//          этого нет» строкой, а просто нет карточки (решение владельца, 19.09);
//        · меняет СЛОВА там, где механизм есть, но другой: имена файлов без
//          `.exe`, «служба» вместо «службы Windows», два разрешения системы.
//      ⚠ Список «чего нет» с 0.8.0 короче: тело тула `computer` (Accessibility,
//      CoreGraphics) и служба (демон launchd `app.helene.svc`) на macOS ЕСТЬ —
//      их карточки и слои рисуются на обеих системах.
//   2. КЛИЕНТ — на какой машине ОТКРЫТО окно. Подсказки сочетаний клавиш (⌘
//      против Ctrl+) — про неё: Пульт в браузере на Mac ходит к серверу на
//      Linux, и ⌘ там правильный, хотя хост — не Mac. Это знает браузер
//      (`navigator.platform`), и ответа оболочки для этого ждать не надо.
//
// Ни строчки DOM и ни одного импорта с побочным эффектом: файл проверяется
// прогоном (app/test/platform.test.mjs), и node должен уметь его загрузить.
// Поэтому список систем — копия `ui-kit/contract.json` (`platforms`): JSON
// без атрибутов импорта node не читает, а стенд сверяет копию с оригиналом.

/** Системы, под которые собирается продукт — `contract.json: platforms`. */
export const PLATFORMS: readonly string[] = ["windows", "macos", "linux"];

/**
 * Слово системы из ответа оболочки или установщика. Незнакомое, пустое,
 * отсутствующее (старая оболочка поля не шлёт) — "" : ничего не прячем.
 * Прятать по догадке нельзя: на Windows без ответа пропала бы карточка службы.
 */
export function platformOf(info: { platform?: unknown } | null | undefined): string {
  const p = String(info?.platform ?? "").trim().toLowerCase();
  return PLATFORMS.includes(p) ? p : "";
}

/** Хост — macOS. Тело и служба здесь ЕСТЬ (0.8.0), и меняются в основном слова;
 *  нет по построению только нулевой сессии, правила брандмауэра и UAC. */
export function isMacPlatform(platform: string): boolean {
  return platform === "macos";
}

/** Окно открыто на Mac — по слову браузера. Только для клавиш и подписей. */
export function clientIsMac(nav: { platform?: string; userAgent?: string } | null | undefined): boolean {
  const p = String(nav?.platform || "");
  const ua = String(nav?.userAgent || "");
  return /^Mac|iPhone|iPad|iPod/.test(p) || /Macintosh|iPhone|iPad/.test(ua);
}

/** Подпись сочетания клавиш для этой клавиатуры: `Ctrl+B` → `⌘B` на Mac. */
export function kbdLabel(text: string, clientMac: boolean): string {
  return clientMac ? text.replace(/Ctrl\+/g, "⌘") : text;
}
