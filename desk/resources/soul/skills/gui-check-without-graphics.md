# GUI-проверка без физического дисплея

Сначала определить поверхность. Playwright проверяет реальный веб-интерфейс; для Electron существует экспериментальный API. Xvfb даёт Linux виртуальный дисплей, но не превращает нативные контролы или произвольную webview в DOM. Для них нужны подходящая автоматизация и захват экрана. HTML-макет явно маркировать как макет.

`playwright-core` сам не поставляет браузер. Найти совместимый executable или установить его в изолированной тестовой среде. Зафиксировать версии, путь и способ подключения. Headless-браузеру дисплей обычно не нужен; оконному Linux-запуску может понадобиться Xvfb.

1. Использовать отдельный профиль и тестовый конфиг без живых секретов. Проверить адрес среды.
2. Запустить настоящее приложение/backend и дождаться наблюдаемой готовности, не считать фиксированный sleep её доказательством.
3. Выбирать элементы по role, доступному имени или test id; проверять visible/enabled/checked и подписи.
4. Переключить настройку через UI; проверить сохранение, соседние поля и следующее действие backend. Проверить обратное переключение и повторное открытие.
5. Сохранить assertions, console/pageerror и скриншоты обоих состояний. Открыть изображения: наличие PNG не равно визуальной вычитке.
6. Освободить только принадлежащие тесту процессы и профили.

Минимальный пример требует адаптации и не проверяет backend сам по себе:

    const { chromium } = require('playwright-core');
    const browser = await chromium.launch({ executablePath: process.env.BROWSER_PATH, headless: true });
    try {
      const page = await browser.newPage();
      await page.goto(process.env.TEST_APP_URL);
      const toggle = page.getByRole('checkbox', { name: 'Example option', exact: true });
      await toggle.check();
      if (!(await toggle.isChecked())) throw new Error('toggle not checked');
      await page.screenshot({ path: 'gate-on.png', fullPage: true });
      await toggle.uncheck();
      if (await toggle.isChecked()) throw new Error('toggle still checked');
      await page.screenshot({ path: 'gate-off.png', fullPage: true });
    } finally { await browser.close(); }

DOM не доказывает читаемость и отсутствие перекрытий. Headless веб-тест не закрывает native shell, системные диалоги, установку пакета или реальный телефон. Назвать проверенную поверхность и ограничения.

Документация:
https://playwright.dev/docs/api/class-electron
https://playwright.dev/docs/browsers
https://playwright.dev/docs/ci
