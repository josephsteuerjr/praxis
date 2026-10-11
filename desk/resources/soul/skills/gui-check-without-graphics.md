# GUI-проверка без физического дисплея

Источник идеи: NF/freebuff, личка 10.10.2026. Это процедурная памятка, не свидетельство прогона конкретного приложения.

## Сначала определить поверхность
- Реальный веб-интерфейс: Playwright открывает штатный сервер приложения с тестовым профилем и настоящим backend.
- Electron: экспериментальный Electron API Playwright; запускать реальный executable и проверять его BrowserWindow. На Linux оконному запуску может понадобиться Xvfb.
- Нативный GUI / иная webview: Playwright не получает управление автоматически. Xvfb даёт виртуальный дисплей, но не превращает нативные контролы в DOM. Нужны отдельная автоматизация и захват экрана. Не заменять приложение HTML-макетом без явной маркировки ограничения.

## Окружение
playwright-core не поставляет браузер. Найти совместимый установленный executable либо отдельно установить браузер в изолированной тестовой среде. Указать версии, executablePath, способ подключения и запуска в отчёте. Headless-браузеру X не нужен; headed Linux-прогону обычно нужен дисплей/Xvfb.

## Проверка
1. Изолированный профиль, копия тестового конфига; никаких реальных секретов. Перед действиями подтвердить адрес и тестовое назначение среды.
2. Запустить настоящее приложение/backend. Дождаться готовности, не использовать фиксированный sleep как доказательство.
3. Селекторы по role, accessible name, test id. Проверить visible/enabled/checked, подпись и доступность элемента.
4. Переключить настройку через интерфейс, проверить сохранение конфигурации, сохранность соседних полей, проекцию и изменение следующего действия backend. Повторить обратный переход и перезапуск.
5. Сохранить скриншоты обоих состояний, console/pageerror, результаты assertions, версии и команды. Открыть изображения собственным инструментом чтения: наличие PNG не равно визуальной вычитке.
6. Освободить только запущенные тестом процессы и профили.

## Что результат НЕ доказывает
DOM-assertions не доказывают читаемость, отсутствие обрезания и перекрытий. Скриншот и его осмотр дополняют их. Headless веб-тест не закрывает приёмку нативной оболочки, системных диалогов, systemd или установки пакета. Для каждой поверхности отдельно перечислить проверенное и непроверенное.

## Документация
- https://playwright.dev/docs/api/class-electron
- https://playwright.dev/docs/browsers
- https://playwright.dev/docs/ci
- https://www.npmjs.com/package/playwright-core

## Минимальный переносимый шаблон (веб-интерфейс)
```js
const { chromium } = require('playwright-core');
(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.BROWSER_PATH, headless: true
  });
  try {
    const page = await browser.newPage();
    page.on('pageerror', e => console.error(e));
    await page.goto(process.env.TEST_APP_URL);
    const toggle = page.getByRole('checkbox', { name: 'Бомж-режим', exact: true });
    await toggle.waitFor({ state: 'visible' });
    await toggle.check();
    if (!(await toggle.isChecked())) throw new Error('toggle not checked');
    // Здесь добавить ожидание и проверку штатного backend/config, а не только DOM.
    await page.screenshot({ path: 'gate-on.png', fullPage: true });
    await toggle.uncheck();
    if (await toggle.isChecked()) throw new Error('toggle still checked');
    await page.screenshot({ path: 'gate-off.png', fullPage: true });
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
```
Шаблон требует адаптации и реального прогона; сохранение памятки не объявлять внедрённым стендом.
