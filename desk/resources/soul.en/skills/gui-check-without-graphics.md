# GUI checks without a physical display

Identify the surface first. Playwright can test a real web interface; Electron has an experimental API. Xvfb supplies a virtual Linux display but does not turn native controls or an arbitrary webview into DOM. Those need suitable automation and screen capture. Label HTML mock-ups explicitly.

`playwright-core` does not bundle a browser. Find a compatible executable or install one in an isolated test environment. Record versions, path and connection method. A headless browser usually needs no display; a windowed Linux run may need Xvfb.

1. Use a separate profile and test configuration without live secrets. Verify the environment's address.
2. Start the actual application/backend and wait for observable readiness, not a fixed sleep as proof.
3. Select by role, accessible name or test id; check visible/enabled/checked state and labels.
4. Change the setting through UI; check persistence, neighbouring fields and the backend's next action. Check reversal and reopening.
5. Retain assertions, console/pageerror and screenshots of both states. Open the images: PNG existence is not visual review.
6. Clean up only test-owned processes and profiles.

This minimal example needs adaptation and does not itself verify the backend:

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

DOM does not prove readability or absence of overlap. A headless web test does not qualify a native shell, system dialogs, package installation or a physical phone. State the tested surface and limits.

Documentation:
https://playwright.dev/docs/api/class-electron
https://playwright.dev/docs/browsers
https://playwright.dev/docs/ci
