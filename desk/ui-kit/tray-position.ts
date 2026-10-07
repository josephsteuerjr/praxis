export interface PopupPoint { x: number; y: number }
export interface PopupArea extends PopupPoint { width: number; height: number }
export function placeTray(point: PopupPoint, area: PopupArea, requestedHeight: number) {
  if (![point.x, point.y, area.x, area.y, area.width, area.height, requestedHeight].every(Number.isFinite)
      || area.width <= 0 || area.height <= 0) throw new Error('Не удалось определить место карточки');
  const pad = 24, gap = 8;
  const width = Math.min(368, area.width + 2 * pad - 2 * gap);
  const height = Math.min(Math.max(160, Math.min(580, requestedHeight)), area.height + 2 * pad - 2 * gap);
  const minX = area.x + gap - pad, minY = area.y + gap - pad;
  const maxX = Math.max(minX, area.x + area.width - gap + pad - width);
  const maxY = Math.max(minY, area.y + area.height - gap + pad - height);
  const wantedY = point.y < area.y + area.height / 2 ? point.y + gap - pad : point.y - gap - height + pad;
  return { x: Math.round(Math.max(minX, Math.min(maxX, point.x - width + pad + 20))),
    y: Math.round(Math.max(minY, Math.min(maxY, wantedY))), width: Math.round(width), height: Math.round(height) };
}
