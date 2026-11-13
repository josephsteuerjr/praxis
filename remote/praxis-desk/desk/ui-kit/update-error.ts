/** Keep release failures tied to their operation, with the original reason. */
export function updateError(stage: 'download' | 'install', error: unknown): string {
  const raw = (error instanceof Error ? error.message : String(error ?? '')).trim();
  const step = stage === 'download' ? 'Скачивание и проверка обновления' : 'Запуск обновления';
  return `${step}: ${raw || 'программа не сообщила причину'}. Можно открыть выпуск и скачать помощник вручную.`;
}
