import { t } from '../../lang/lang';

// Exact product-authored captions only. Unknown backend diagnostics stay literal.
const KEYS: Record<string,string> = {
  "small — быстрее и легче": "voice.model.small.title",
  "Компактнее large-v3-turbo. Может путаться в именах и редких словах.": "voice.model.small.note",
  "large-v3-turbo — как на сервере": "voice.model.turbo.title",
  "Крупнее small и требует больше памяти. Скорость зависит от процессора и длины записи.": "voice.model.turbo.note",
  "XTTS — локальный голос": "voice.xtts.title",
  "Отдельный локальный runtime и модель; без сетевого fallback.": "voice.xtts.note",
  "Fish — облачный голос": "voice.fish.title",
  "Собственный API-ключ и оплата Fish. Без локальных весов и XTTS-worker.": "voice.fish.note",
  "голос агента выключен": "voice.reason.off",
  "голос выключен владельцем": "voice.reason.owner.off",
  "Добавь собственный API-ключ Fish": "voice.reason.fish.key",
  "XTTS runtime не установлен": "voice.reason.xtts.runtime",
  "XTTS модель не установлена": "voice.reason.xtts.model",
  "модель не скачана — голосовые не расшифровываются": "voice.reason.whisper.model",
  "Whisper — отдельное загружаемое расширение": "voice.reason.whisper.extension",
  "Загрузка прервана. Можно повторить; кеш сохранён.": "voice.reason.download.interrupted",
  "Подготавливаю XTTS": "voice.reason.xtts.prepare",
  "Скачиваю XTTS": "voice.reason.xtts.download",
  "Устанавливаю XTTS": "voice.reason.xtts.install",
  "XTTS установлен; применяется перезапуском": "voice.reason.xtts.installed"
};
export function voiceOwnedText(value: string): string {
  return Object.hasOwn(KEYS,value) ? t(KEYS[value]) : value;
}
