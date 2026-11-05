/** Telegram owns fullscreen; never use the browser Fullscreen API here.
 * https://core.telegram.org/bots/webapps (Bot API 8.0).
 */
type Insets = Partial<Record<'top' | 'right' | 'bottom' | 'left', number>>;
export interface TelegramWebApp {
  initData: string;
  colorScheme: 'light' | 'dark';
  isFullscreen?: boolean;
  viewportHeight?: number;
  safeAreaInset?: Insets;
  contentSafeAreaInset?: Insets;
  isVersionAtLeast?(version: string): boolean;
  requestFullscreen?(): void;
  setHeaderColor?(color: string): void;
  ready(): void;
  expand(): void;
  onEvent(name: string, fn: () => void): void;
  BackButton: { show(): void; hide(): void; onClick(fn: () => void): void };
}

export function mountTelegramShell(tg?: TelegramWebApp): void {
  const root = document.documentElement;
  root.classList.add('telegram-shell');
  const supported = !!tg?.requestFullscreen && !!tg.isVersionAtLeast?.('8.0');
  let failed = false;
  let frame = 0;
  const px = (n: number | undefined) => Number.isFinite(n) ? Math.max(0, n!) : 0;
  const update = () => {
    frame = 0;
    const visual = window.visualViewport;
    // Android resizes layout, iOS can resize only the visual viewport. The
    // dynamic height (never stableHeight) keeps composer/sheets above the IME.
    const height = Math.min(tg?.viewportHeight || innerHeight, visual?.height || innerHeight);
    root.style.setProperty('--app-height', `${Math.max(1, height)}px`);
    root.style.setProperty('--app-top', `calc(${px(visual?.offsetTop)}px / var(--ui-scale, 1))`);
    root.style.setProperty('--app-bottom', `calc(${Math.max(0, innerHeight - height - px(visual?.offsetTop))}px / var(--ui-scale, 1))`);
    for (const side of ['top', 'right', 'bottom', 'left'] as const) {
      root.style.setProperty(`--app-safe-${side}`, `calc((max(env(safe-area-inset-${side}, 0px), ${px(tg?.safeAreaInset?.[side])}px) + ${px(tg?.contentSafeAreaInset?.[side])}px) / var(--ui-scale, 1))`);
    }
    const editing = document.activeElement as HTMLElement | null;
    if (editing?.matches('input, textarea') && editing.closest('.phone-settings-body')) {
      editing.scrollIntoView({block: 'nearest', inline: 'nearest'});
    }
    root.dataset.fullscreen = tg?.isFullscreen ? 'active' : failed ? 'unavailable' : supported ? 'requested' : 'expanded';
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(update); };
  for (const name of ['viewportChanged', 'safeAreaChanged', 'contentSafeAreaChanged', 'fullscreenChanged', 'activated']) tg?.onEvent(name, schedule);
  tg?.onEvent('fullscreenFailed', () => { failed = true; tg.expand(); schedule(); });
  window.addEventListener('resize', schedule);
  window.addEventListener('orientationchange', schedule);
  window.visualViewport?.addEventListener('resize', schedule);
  window.visualViewport?.addEventListener('scroll', schedule);
  const header = () => { try { tg?.setHeaderColor?.(tg.colorScheme === 'dark' ? '#1a1815' : '#fdfcfa'); } catch { /* older client */ } };
  tg?.onEvent('themeChanged', header);
  tg?.ready();
  tg?.expand();
  header();
  if (supported) {
    try { tg!.requestFullscreen!(); } catch { failed = true; tg?.expand(); }
  }
  update();
}
