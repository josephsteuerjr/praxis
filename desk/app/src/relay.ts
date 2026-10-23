// Адреса локального реле подписки ChatGPT — ОДИН источник на всё окно.
//
// Реле 1.9.1 обслуживает /chat/completions и /v1/chat/completions.
// Корневой адрес сохранён для совместимости с установщиком; /v1/models
// проверяет каталог, а не авторизацию или возможность генерации.
//
// Вторая сторона этого шва — установщик: `setup/src/install.rs` (config_json,
// ветка "chatgpt") и его юнит-тест `relay_base_url_has_no_v1`. Значения обязаны
// совпадать; на стороне окна их держит здесь `app/test/relay-url.test.mjs`.
import contract from "../../ui-kit/contract.json";

/** Порт локального реле — из ui-kit/contract.json (тот же, что в install.rs и shell). */
export const RELAY_PORT: number = contract.ports.relay;

/** Адрес мозга для helene.json (model.base_url). БЕЗ «/v1» — см. выше. */
export const RELAY_BASE_URL = `http://127.0.0.1:${RELAY_PORT}`;

/** Адрес для проверки «реле живо» — /v1/models. С «/v1»: маршрут такой есть. */
export const RELAY_PROBE_URL = `${RELAY_BASE_URL}/v1`;

/**
 * Адрес мозга для ВЫБРАННОГО порта.
 *
 * ⚠ Порт не всегда 5011. Установщик берёт первый свободный из
 * `RELAY_PORT..RELAY_PORT+19` (`setup/src/install.rs`), когда 5011 занят —
 * например, реле от прежней установки, пережившим снятие. Окно, писавшее
 * константу, переписывало выбранный порт обратно на 5011, и первое же
 * «Сохранить» уводило каждый ход в ЧУЖОЕ реле. Порт берётся из `relay.port`.
 */
export function relayBaseUrl(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/** Адрес пробы «/v1/models» для выбранного порта. */
export function relayProbeUrl(port: number): string {
  return `${relayBaseUrl(port)}/v1`;
}

/** Префикс ключа петли окно→реле. Тот же, что в install.rs. */
export const RELAY_KEY_PREFIX = "sk-frame-";

/**
 * Ключ петли к реле: единственный Bearer, который защищает подписку владельца
 * на открытом локальном порту. `Math.random()` здесь был не криптографическим
 * ГПСЧ; установщик на той же роли берёт `random_hex(24)`. Длина и префикс
 * совпадают с install.rs, чтобы живое реле, прочитавшее прежний ключ при
 * старте, не начало отвечать 401.
 */
export function newRelayKey(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  let hex = "";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return RELAY_KEY_PREFIX + hex;
}
