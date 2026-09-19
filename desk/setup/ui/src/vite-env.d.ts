/// <reference types="vite/client" />

declare module "*.md?raw" {
  const text: string;
  export default text;
}

/** Ограда рук и опция службы — прямо из `localharness/modes.py` (см. плагин
 *  helene-modes-from-python в vite.config.ts). Своей копии текстов в
 *  установщике нет: расхождение описаний значило бы, что владелец выбирает
 *  одно, а получает другое.
 *
 *  ⚠ Это ДВА измерения, а не один список. `MODE_CARDS` — насколько далеко
 *  агент дотягивается; `SERVICE_OPTION` — ставить ли службу (Windows — SCM,
 *  macOS — демон launchd), поверх любой из оград. Служба ограду не снимает и
 *  не включает. */
declare module "virtual:helene-modes" {
  export interface ModeCard {
    /** sandbox | interactive — ключ `agent_mode` в helene.json. */
    name: string;
    title: string;
    text: string;
    /** Ограда включена (`sandbox.enabled`). */
    sandbox: boolean;
  }
  /** Галочка внутри опции службы. `key` — путь в helene.json, по которому её
   *  читает тот, кто исполняет: `service.session0` — служба, `service.firewall`
   *  — она же, но про одно узкое действие продукта. */
  export interface ServiceToggle {
    key: string;
    title: string;
    text: string;
    /** Что сказать, когда галочка ВКЛЮЧЕНА. Пустая строка — сказать нечего. */
    warning: string;
    default: boolean;
  }
  export interface ServiceOption {
    title: string;
    text: string;
    /** Чего служба НЕ даёт. Пусто на Windows (там всё сказано описанием); на
     *  macOS — окон и экрана у неё нет: тело тула `computer` поднимает окно.
     *  FileVault сюда не едет — он в документах поставки. */
    warning?: string;
    toggles: ServiceToggle[];
  }
  /** Управление компьютером — опция ПОВЕРХ любого режима (modes.computer_option):
   *  тело руки `computer` живёт снаружи ограды. Установщик спрашивает только
   *  выключатель; четыре права — в Настройках. */
  export interface ComputerOption {
    title: string;
    text: string;
    /** Оговорка, которую владелец читает ДО включения. */
    warning: string;
    default: boolean;
  }
  export const MODE_CARDS: ModeCard[];
  /** Те же ограды словами для macOS — из `TEXTS_MACOS` в modes.py, если движок
   *  их завёл (там нет ни службы, ни тела, ни «окна Windows»). null — в
   *  modes.py такого словаря нет, и на Mac показываются общие тексты. */
  export const MODE_CARDS_MACOS: ModeCard[] | null;
  export const SERVICE_OPTION: ServiceOption;
  /** Та же опция словами macOS — из `SERVICE_TITLE_MACOS`/`SERVICE_TEXT_MACOS`/
   *  `SERVICE_WARNING_MACOS` в modes.py. Механизм там другой (демон launchd), и
   *  галочек у него нет: `toggles` пуст. null — констант в modes.py нет, и на
   *  Mac показываются общие тексты. */
  export const SERVICE_OPTION_MACOS: ServiceOption | null;
  export const SESSION0_WARNING: string;
  export const COMPUTER_OPTION: ComputerOption;
  /** Та же опция словами macOS — из `COMPUTER_TEXT_MACOS` в modes.py (тело без
   *  `.exe`, zsh, два разрешения системы). null — константы в modes.py нет, и
   *  на Mac показывается общий текст. */
  export const COMPUTER_OPTION_MACOS: ComputerOption | null;
}
