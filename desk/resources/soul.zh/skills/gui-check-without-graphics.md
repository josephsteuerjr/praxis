# 无实体显示器时检查 GUI

先区分界面类型。Playwright 可测试真实网页界面，Electron 有实验性 API。Xvfb 提供 Linux 虚拟显示器，但不会把原生控件或任意 webview 变成 DOM；这些需要相应自动化和截图。HTML 模型应明确标为模型。

`playwright-core` 不自带浏览器。寻找兼容可执行文件，或在隔离测试环境安装。记录版本、路径及连接方式。无头浏览器通常无需显示器，Linux 窗口模式可能需要 Xvfb。

1. 使用独立配置档和不含真实秘密的测试配置，核对环境地址。
2. 启动真实应用/backend，等待可观察就绪状态，不把固定 sleep 当作证明。
3. 按 role、无障碍名称或 test id 选择控件，检查 visible/enabled/checked 及标签。
4. 经 UI 改设置，检查持久化、相邻字段和 backend 下一行动，再检查反向切换与重新打开。
5. 保存断言、console/pageerror 和两种状态截图，实际打开图片审查；有 PNG 不等于已看过。
6. 只清理测试所属进程和配置档。

最小示例需要适配，本身不验证 backend：

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

DOM 不证明可读性或不存在遮挡。无头网页测试不能完成原生外壳、系统对话框、安装包或实体手机验收。注明已测界面及限制。

文档：
https://playwright.dev/docs/api/class-electron
https://playwright.dev/docs/browsers
https://playwright.dev/docs/ci
