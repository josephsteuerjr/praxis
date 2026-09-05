// Адреса локального реле подписки ChatGPT — ОДИН источник на всё окно.
//
// Почему отдельный файл, а не литерал по месту. Реле объявляет два разных
// маршрута, и разница между ними стоила подписки:
//
//   POST /chat/completions   — вызов модели (маршрута /v1/chat/completions
//                              у реле НЕТ вовсе: _relay_prod_src/src/main.rs);
//   GET  /v1/models          — список моделей, авторизации не требует.
//
// Клиент OpenAI в ядре приклеивает «/chat/completions» к base_url
// (live/llm.py), поэтому base_url с «/v1» даёт 404 на КАЖДЫЙ ход, а кнопка
// «Показать модели подписки» рядом остаётся зелёной — она бьёт в /v1/models.
// Ровно так подписка и умирала: установщик писал адрес без /v1, а первое же
// «Сохранить» в окне возвращало /v1 обратно.
//
// Вторая сторона этого шва — установщик: `setup/src/install.rs` (config_json,
// ветка "chatgpt") и его юнит-тест `relay_base_url_has_no_v1`. Значения обязаны
// совпадать; на стороне окна их держит здесь `app/test/relay-url.test.mjs`.

/** Порт локального реле. Тот же, что RELAY_PORT в setup/src/install.rs. */
export const RELAY_PORT = 5011;

/** Адрес мозга для helene.json (model.base_url). БЕЗ «/v1» — см. выше. */
export const RELAY_BASE_URL = `http://127.0.0.1:${RELAY_PORT}`;

/** Адрес для проверки «реле живо» — /v1/models. С «/v1»: маршрут такой есть. */
export const RELAY_PROBE_URL = `${RELAY_BASE_URL}/v1`;

/**
 * Адрес мозга для ВЫБРАННОГО порта.
 *
 * ⚠ Порт не всегда 5011. Установщик берёт первый свободный из
 * `RELAY_PORT..RELAY_PORT+19` (`setup/src/install.rs:628`), когда 5011 занят —
 * например, реле от прежней установки, пережившим снятие. Окно, писавшее
 * константу, переписывало выбранный порт обратно на 5011, и первое же
 * «Сохранить» уводило каждый ход в ЧУЖОЕ реле: тот же отказ, что и с «/v1»,
 * только с другой стороны шва. Порт берётся из `relay.port` в helene.json.
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
 * на открытом локальном порту (shell/src/main.rs: «открытый локальный порт
 * позволял бы любому процессу на машине жечь подписку»).
 *
 * `Math.random()` здесь был не криптографическим ГПСЧ и предсказуем по
 * состоянию движка; установщик на той же роли берёт `random_hex(24)`.
 * Длина и префикс совпадают с install.rs, чтобы живое реле, прочитавшее
 * прежний ключ при старте, не начало отвечать 401.
 */
export function newRelayKey(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  let hex = "";
  for (const b of bytes) hex += b.toString(16).padStart(2, "0");
  return RELAY_KEY_PREFIX + hex;
}
