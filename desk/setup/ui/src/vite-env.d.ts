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
 *  агент дотягивается; `SERVICE_OPTION` — ставить ли службу Windows, поверх
 *  любой из оград. Служба ограду не снимает и не включает. */
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
  export const SERVICE_OPTION: ServiceOption;
  export const SESSION0_WARNING: string;
  export const COMPUTER_OPTION: ComputerOption;
}
